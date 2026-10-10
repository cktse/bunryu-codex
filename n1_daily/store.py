"""SQLite is the authoritative record of content and publication state."""

from contextlib import contextmanager
from datetime import date, timedelta
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import sys

from .content import sample_assignment

JSON_COLUMNS = {"assignment", "lesson", "review_result", "failure_reasons", "correction_feedback"}
FIELDS = JSON_COLUMNS | {"status", "phase", "attempt_count", "first_published_at",
    "email_status", "message_bytes", "gmail_id", "email_attempt_at", "email_error",
    "markdown_status", "markdown_path", "markdown_checksum", "markdown_attempt_at",
    "markdown_error", "index_status", "index_error"}


@contextmanager
def run_lock(database_path):
    path = Path(str(database_path) + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        os.chmod(path, 0o600)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("another daily run is active") from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class Store:
    def __init__(self, path, readonly=False):
        self.path = Path(path).expanduser().resolve()
        self.diagnostics = []
        if readonly:
            self.conn = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=5)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA query_only=ON")
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=5)
        os.chmod(self.path, 0o600)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS lessons (
                date TEXT PRIMARY KEY, assignment TEXT NOT NULL, lesson TEXT,
                status TEXT NOT NULL DEFAULT 'draft', phase TEXT NOT NULL DEFAULT 'generating',
                attempt_count INTEGER NOT NULL DEFAULT 0, review_result TEXT,
                failure_reasons TEXT NOT NULL DEFAULT '[]', first_published_at TEXT,
                correction_feedback TEXT NOT NULL DEFAULT '[]',
                email_status TEXT NOT NULL DEFAULT 'pending', message_bytes BLOB, gmail_id TEXT,
                email_attempt_at TEXT, email_error TEXT,
                markdown_status TEXT NOT NULL DEFAULT 'pending', markdown_path TEXT,
                markdown_checksum TEXT, markdown_attempt_at TEXT, markdown_error TEXT,
                index_status TEXT NOT NULL DEFAULT 'pending', index_error TEXT
            );
            CREATE TABLE IF NOT EXISTS review_items (
                lesson_id TEXT NOT NULL, question_id TEXT NOT NULL,
                subject TEXT NOT NULL, target TEXT NOT NULL, original TEXT NOT NULL,
                stage INTEGER NOT NULL DEFAULT 0, due_date TEXT,
                PRIMARY KEY (lesson_id, question_id)
            );
            CREATE TABLE IF NOT EXISTS generated_files (
                path TEXT PRIMARY KEY, checksum TEXT, pending_checksum TEXT
            );
        """)
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(lessons)")}
        if "correction_feedback" not in columns:
            with self.conn:
                self.conn.execute("ALTER TABLE lessons ADD COLUMN correction_feedback TEXT NOT NULL DEFAULT '[]'")

    def close(self):
        self.conn.close()

    def get(self, day):
        row = self.conn.execute("SELECT * FROM lessons WHERE date=?", (day,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        for key in JSON_COLUMNS:
            try:
                result[key] = json.loads(result[key]) if result[key] is not None else None
            except (ValueError, TypeError):
                raise RuntimeError(f"Stored {day}.{key}: invalid JSON") from None
        assignment = result["assignment"]
        if (not isinstance(assignment, dict) or not isinstance(assignment.get("date"), str)
                or not isinstance(assignment.get("theme"), str) or not isinstance(assignment.get("slots"), list)
                or len(assignment["slots"]) != 6 or any(not isinstance(slot, dict)
                    or not {"type", "subject", "review_of"} <= slot.keys() for slot in assignment["slots"])):
            raise RuntimeError(f"Stored {day}.assignment: invalid layout")
        for key in ("failure_reasons", "correction_feedback"):
            if not isinstance(result[key], list) or any(not isinstance(reason, str) for reason in result[key]):
                raise RuntimeError(f"Stored {day}.{key}: invalid layout")
        if isinstance(result["lesson"], dict):
            result["lesson"].update({key: result[key] for key in (
                "status", "attempt_count", "review_result", "failure_reasons", "first_published_at")})
        return result

    def begin(self, day, assignment):
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO lessons(date, assignment) VALUES (?, ?)",
                              (day, json.dumps(assignment, ensure_ascii=False)))
        return self.get(day)

    def _update(self, day, values):
        if not values or set(values) - FIELDS:
            raise ValueError("unknown or empty lesson update")
        encoded = {k: json.dumps(v, ensure_ascii=False) if k in JSON_COLUMNS else v for k, v in values.items()}
        columns = ", ".join(f"{k}=?" for k in encoded)
        self.conn.execute(f"UPDATE lessons SET {columns} WHERE date=?", (*encoded.values(), day))

    def update(self, day, **values):
        with self.conn:
            self._update(day, values)

    def recover_inflight(self):
        # Run only while holding the daily lock; an active sender must not be
        # mistaken for a previous interrupted process.
        with self.conn:
            self.conn.execute("UPDATE lessons SET email_status='unknown', email_error='Interrupted send; check the sending mailbox' WHERE email_status='sending'")

    def history(self, before=None, published_only=False):
        conditions = [] if published_only else ["lesson IS NOT NULL"]
        args = []
        if before:
            conditions.append("date < ?")
            args.append(before)
        if published_only:
            conditions.append("first_published_at IS NOT NULL")
        rows = self.conn.execute("SELECT date FROM lessons WHERE " + " AND ".join(conditions) + " ORDER BY date", args)
        lessons = []
        for row in rows:
            record = self.get(row["date"])
            lesson = record["lesson"]
            if not isinstance(lesson, dict) or not isinstance(lesson.get("lesson_id"), str):
                self.history_diagnostic(row["date"], "lesson/lesson_id")
                continue
            questions = lesson.get("questions")
            if not isinstance(questions, list):
                self.history_diagnostic(row["date"], "questions")
                questions = []
            usable = []
            for i, question in enumerate(questions, 1):
                if (isinstance(question, dict) and isinstance(question.get("id"), str)
                        and isinstance(question.get("subject"), str) and isinstance(question.get("target"), str)):
                    usable.append(question)
                else:
                    self.history_diagnostic(row["date"], f"questions.q{i}")
            lessons.append({**lesson, "questions": usable})
        return lessons

    def history_diagnostic(self, day, field):
        message = f"History {day}.{field}: malformed entry; using remaining usable published material"
        if message not in self.diagnostics:
            self.diagnostics.append(message)
            print(message, file=sys.stderr)

    def review_items(self):
        return [dict(row) for row in self.conn.execute("SELECT * FROM review_items ORDER BY due_date, question_id, lesson_id")]

    def assignment(self, day):
        count = self.conn.execute("SELECT count(*) FROM lessons WHERE first_published_at IS NOT NULL").fetchone()[0]
        assignment = sample_assignment(day, count)
        chosen = 0
        for item in self.review_items():
            if item["due_date"] is None or item["due_date"] > day:
                continue
            for slot in assignment["slots"]:
                if slot["subject"] == item["subject"] and slot["review_of"] is None:
                    slot["review_of"] = {"lesson_id": item["lesson_id"], "question_id": item["question_id"]}
                    chosen += 1
                    break
            if chosen == 2:
                break
        return assignment

    def context(self, day, assignment):
        history = self.history(before=day, published_only=True)
        pairs = sorted({(q["subject"], q["target"]) for old in history for q in old.get("questions", [])
                        if isinstance(q, dict) and isinstance(q.get("subject"), str) and isinstance(q.get("target"), str)})
        originals = []
        for slot in assignment["slots"]:
            ref = slot["review_of"]
            if ref:
                row = self.conn.execute("SELECT original FROM review_items WHERE lesson_id=? AND question_id=?",
                                        (ref["lesson_id"], ref["question_id"])).fetchone()
                if row:
                    originals.append({"reference": ref, "question": json.loads(row["original"])})
        return {"assignment": assignment, "previous_seven_published_lessons": self.history(day, True)[-7:],
                "all_earlier_subject_target_pairs": [list(pair) for pair in pairs], "review_originals": originals}

    def channel_success(self, day, now, **values):
        with self.conn:
            self._update(day, values)
            record = self.get(day)
            if record["first_published_at"]:
                return
            self._update(day, {"first_published_at": now.isoformat()})
            published_date = now.date()
            for q in record["lesson"]["questions"]:
                ref = q.get("review_of")
                if ref:
                    item = self.conn.execute("SELECT stage FROM review_items WHERE lesson_id=? AND question_id=?",
                                             (ref["lesson_id"], ref["question_id"])).fetchone()
                    if item:
                        stage = item["stage"] + 1
                        due = (published_date + timedelta(days=(1, 3, 7, 14)[stage])).isoformat() if stage < 4 else None
                        self.conn.execute("UPDATE review_items SET stage=?, due_date=? WHERE lesson_id=? AND question_id=?",
                                          (stage, due, ref["lesson_id"], ref["question_id"]))
                else:
                    self.conn.execute("INSERT OR IGNORE INTO review_items(lesson_id, question_id, subject, target, original, due_date) VALUES (?, ?, ?, ?, ?, ?)",
                        (record["lesson"]["lesson_id"], q["id"], q["subject"], q["target"], json.dumps(q, ensure_ascii=False),
                         (published_date + timedelta(days=1)).isoformat()))

    def file_record(self, path):
        row = self.conn.execute("SELECT * FROM generated_files WHERE path=?", (str(path),)).fetchone()
        return dict(row) if row else None

    def prepare_file(self, path, checksum):
        with self.conn:
            self.conn.execute("INSERT INTO generated_files(path, pending_checksum) VALUES (?, ?) ON CONFLICT(path) DO UPDATE SET pending_checksum=excluded.pending_checksum", (str(path), checksum))

    def finish_file(self, path, checksum):
        with self.conn:
            self.conn.execute("UPDATE generated_files SET checksum=?, pending_checksum=NULL WHERE path=?", (checksum, str(path)))
