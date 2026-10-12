"""SQLite checkpoints and publication accounting. Callers hold run_lock for writes."""
from contextlib import contextmanager
from datetime import timedelta
import fcntl
import os
from pathlib import Path
import sqlite3
import sys

from .content import SUBJECTS, THEMES, assignment, canonical, plain, target
from .errors import InputError, StorageError
from .jsonio import dumps, loads

COLUMNS = {
    'assignment': 'TEXT NOT NULL', 'lesson': 'TEXT', 'status': "TEXT NOT NULL DEFAULT 'draft'",
    'phase': "TEXT NOT NULL DEFAULT 'generating'", 'attempt_count': 'INTEGER NOT NULL DEFAULT 0',
    'review_result': 'TEXT', 'failure_reasons': "TEXT NOT NULL DEFAULT '[]'", 'first_published_at': 'TEXT',
    'correction_feedback': "TEXT NOT NULL DEFAULT '[]'", 'context': 'TEXT', 'review_stages': 'TEXT',
    'context_reconstructed': 'INTEGER NOT NULL DEFAULT 0', 'binding': 'TEXT', 'check_result': 'TEXT',
    'email_status': "TEXT NOT NULL DEFAULT 'pending'", 'message_bytes': 'BLOB', 'gmail_id': 'TEXT',
    'email_from': 'TEXT', 'email_to': 'TEXT', 'email_attempt_at': 'TEXT', 'email_completed_at': 'TEXT', 'email_error': 'TEXT',
    'markdown_status': "TEXT NOT NULL DEFAULT 'pending'", 'markdown_path': 'TEXT', 'markdown_checksum': 'TEXT',
    'vault_root': 'TEXT', 'markdown_attempt_at': 'TEXT', 'markdown_completed_at': 'TEXT', 'markdown_error': 'TEXT',
    'index_status': "TEXT NOT NULL DEFAULT 'pending'", 'index_error': 'TEXT',
    'index_attempt_at': 'TEXT', 'index_completed_at': 'TEXT', 'notification_fingerprint': 'TEXT',
}
JSON_COLUMNS = {'assignment', 'lesson', 'review_result', 'failure_reasons', 'correction_feedback',
                'context', 'review_stages', 'check_result'}


@contextmanager
def run_lock(database):
    path = Path(str(Path(database).expanduser().resolve()) + '.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'a') as stream:
        os.fchmod(stream.fileno(), 0o600)
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise InputError('A run is already active for this database') from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


class Store:
    def __init__(self, path, *, readonly=False, create=True):
        self.path = Path(path).expanduser().resolve()
        self.readonly = readonly
        self.diagnostics = []
        if readonly or not create:
            mode = 'ro' if readonly else 'rw'
            self.conn = sqlite3.connect(self.path.as_uri() + '?mode=' + mode, uri=True)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(descriptor)
            self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        if readonly:
            self.conn.execute('PRAGMA query_only=ON')
            return
        os.chmod(self.path, 0o600)
        try:
            with self.conn:
                fields = ', '.join(f'{name} {kind}' for name, kind in COLUMNS.items())
                self.conn.execute(f'CREATE TABLE IF NOT EXISTS lessons (date TEXT PRIMARY KEY, {fields})')
                existing = {row['name'] for row in self.conn.execute('PRAGMA table_info(lessons)')}
                for name, kind in COLUMNS.items():
                    if name not in existing:
                        self.conn.execute(f'ALTER TABLE lessons ADD COLUMN {name} {kind}')
                self.conn.execute('CREATE TABLE IF NOT EXISTS review_items ('
                                  'lesson_id TEXT NOT NULL, question_id TEXT NOT NULL, subject TEXT NOT NULL,'
                                  'target TEXT NOT NULL, original TEXT NOT NULL, stage INTEGER NOT NULL DEFAULT 0,'
                                  'due_date TEXT, PRIMARY KEY(lesson_id, question_id))')
                self.conn.execute('CREATE TABLE IF NOT EXISTS generated_files ('
                                  'path TEXT PRIMARY KEY, checksum TEXT, pending_checksum TEXT)')
        except Exception:
            self.close()
            raise

    def close(self):
        self.conn.close()

    def get(self, day):
        row = self.conn.execute('SELECT * FROM lessons WHERE date=?', (day,)).fetchone()
        if row is None:
            return None
        result = {name: None for name in COLUMNS}
        result.update(dict(row))
        for field in JSON_COLUMNS:
            try:
                result[field] = loads(result[field]) if result[field] is not None else None
            except (ValueError, TypeError) as exc:
                raise StorageError(f'Stored {day}.{field}: invalid JSON') from exc
        assigned = result['assignment']
        if (not isinstance(assigned, dict) or assigned.get('date') != day or assigned.get('theme') not in THEMES
                or not isinstance(assigned.get('slots'), list) or len(assigned['slots']) != 6
                or any(not isinstance(slot, dict) or not {'type', 'subject', 'review_of'} <= slot.keys()
                       or not isinstance(slot['type'], str) or slot['type'] not in SUBJECTS
                       or slot['subject'] != SUBJECTS.get(slot['type'])
                       or (slot['review_of'] is not None and (not isinstance(slot['review_of'], dict)
                           or set(slot['review_of']) != {'lesson_id', 'question_id'}
                           or not all(plain(value) for value in slot['review_of'].values())))
                       for slot in assigned['slots'])):
            raise StorageError(f'Stored {day}.assignment: invalid layout')
        for field in ('failure_reasons', 'correction_feedback'):
            if result[field] is None and field == 'correction_feedback':
                result[field] = []
            if not isinstance(result[field], list) or any(not isinstance(v, str) for v in result[field]):
                raise StorageError(f'Stored {day}.{field}: invalid layout')
        for field in ('context', 'review_stages'):
            if result[field] is not None and not isinstance(result[field], dict):
                raise StorageError(f'Stored {day}.{field}: invalid layout')
        if (type(result['attempt_count']) is not int or not 0 <= result['attempt_count'] <= 2
                or (result['context'] is not None and (not isinstance(result['context'].get('history'), list)
                    or result['context'].get('assignment') != assigned
                    or any(not isinstance(old, dict) or not isinstance(old.get('questions'), list)
                           or any(not isinstance(q, dict) for q in old['questions'])
                           for old in result['context']['history'])))
                or (result['review_stages'] is not None and any(type(stage) is not int or not 0 <= stage <= 4
                    for stage in result['review_stages'].values()))):
            raise StorageError(f'Stored {day}: invalid generation checkpoint')
        return result

    def _update(self, day, values):
        if not values or values.keys() - COLUMNS.keys():
            raise ValueError('Unknown or empty storage update')
        encoded = {key: dumps(value) if key in JSON_COLUMNS and value is not None else value for key, value in values.items()}
        self.conn.execute('UPDATE lessons SET ' + ', '.join(f'{key}=?' for key in encoded) + ' WHERE date=?',
                          (*encoded.values(), day))

    def update(self, day, **values):
        with self.conn:
            self._update(day, values)

    def recover_inflight(self):
        with self.conn:
            self.conn.execute("UPDATE lessons SET email_status='unknown', email_error='Interrupted Gmail send; check the sending mailbox before resolution' WHERE email_status='sending'")

    def history(self, before):
        rows = self.conn.execute('SELECT date FROM lessons WHERE first_published_at IS NOT NULL AND date < ? ORDER BY date', (before,)).fetchall()
        history = []
        for row in rows:
            record = self.get(row['date'])
            content = record['lesson']
            if not isinstance(content, dict) or not plain(content.get('lesson_id')):
                self.history_warning(row['date'], 'lesson/lesson_id')
                continue
            questions = content.get('questions')
            if not isinstance(questions, list):
                self.history_warning(row['date'], 'questions')
                questions = []
            usable = []
            for i, q in enumerate(questions, 1):
                if isinstance(q, dict) and all(plain(q.get(k)) for k in ('id', 'subject', 'target')):
                    usable.append(q)
                    if not plain(q.get('prompt_ja')) or not isinstance(q.get('options'), dict):
                        self.history_warning(row['date'], f'questions.q{i}.wording')
                else:
                    self.history_warning(row['date'], f'questions.q{i}')
            history.append({**canonical(content), 'date': row['date'], 'first_published_at': record['first_published_at'], 'questions': usable})
        return history

    def history_warning(self, day, field):
        text = f'History {day}.{field}: malformed published material; using usable fields, unavailable material cannot be checked for duplicates'
        if text not in self.diagnostics:
            self.diagnostics.append(text)
            print(text, file=sys.stderr)

    def review_items(self):
        return [dict(row) for row in self.conn.execute('SELECT * FROM review_items ORDER BY due_date, question_id, lesson_id')]

    def snapshot(self, day, assigned):
        history = self.history(day)
        originals, stages = [], {}
        for slot in assigned['slots']:
            ref = slot['review_of']
            if not ref:
                continue
            row = self.conn.execute('SELECT * FROM review_items WHERE lesson_id=? AND question_id=?',
                                    (ref['lesson_id'], ref['question_id'])).fetchone()
            if row:
                try:
                    original = loads(row['original'])
                except ValueError as exc:
                    raise StorageError('Stored review original: invalid JSON') from exc
                originals.append({'reference': ref, 'question': original})
                stages[ref['lesson_id'] + '/' + ref['question_id']] = row['stage']
        context = {'assignment': assigned, 'history': history, 'previous_seven_published_lessons': history[-7:],
                   'all_earlier_subject_target_pairs': [list(pair) for pair in sorted({target(q) for old in history for q in old['questions']})],
                   'review_originals': originals}
        return context, stages

    def ensure(self, day):
        record = self.get(day)
        if record:
            return self.ensure_context(record)
        count = self.conn.execute('SELECT count(*) FROM lessons WHERE first_published_at IS NOT NULL').fetchone()[0]
        assigned = assignment(day, count)
        history = self.history(day)
        published = {(old['lesson_id'], q['id']): q for old in history for q in old['questions']}
        chosen = 0
        for item in self.review_items():
            original = published.get((item['lesson_id'], item['question_id']))
            if (original is None or any(original[key] != item[key] for key in ('subject', 'target'))
                    or item['due_date'] is None or item['due_date'] > day or item['stage'] >= 4):
                continue
            for slot in assigned['slots']:
                if slot['subject'] == item['subject'] and slot['review_of'] is None:
                    slot['review_of'] = {'lesson_id': item['lesson_id'], 'question_id': item['question_id']}
                    chosen += 1
                    break
            if chosen == 2:
                break
        context, stages = self.snapshot(day, assigned)
        with self.conn:
            self.conn.execute('INSERT INTO lessons(date, assignment, context, review_stages) VALUES (?, ?, ?, ?)',
                              (day, dumps(assigned), dumps(context), dumps(stages)))
        return self.get(day)

    def ensure_context(self, record):
        if record['context'] is None or record['review_stages'] is None:
            context, stages = self.snapshot(record['date'], record['assignment'])
            self.update(record['date'], context=context, review_stages=stages, context_reconstructed=1)
            return self.get(record['date'])
        return record

    def stale_reviews(self, record):
        errors = []
        for slot in record['assignment']['slots']:
            ref = slot['review_of']
            if not ref:
                continue
            key = ref['lesson_id'] + '/' + ref['question_id']
            row = self.conn.execute('SELECT stage, due_date FROM review_items WHERE lesson_id=? AND question_id=?',
                                    (ref['lesson_id'], ref['question_id'])).fetchone()
            if (not row or row['stage'] != record['review_stages'].get(key) or row['stage'] >= 4
                    or not row['due_date'] or row['due_date'] > record['date']):
                errors.append(f'Stale review stage or ineligible due date: {key}')
        return errors

    def channel_success(self, day, now, **values):
        with self.conn:
            record = self.get(day)
            self._update(day, values)
            if record['first_published_at']:
                return
            self._update(day, {'first_published_at': now.isoformat()})
            for q in record['lesson']['questions']:
                ref = q['review_of']
                if ref:
                    key = ref['lesson_id'] + '/' + ref['question_id']
                    expected = record['review_stages'].get(key)
                    if type(expected) is not int or not 0 <= expected < 4:
                        continue
                    stage = expected + 1
                    due = (now.date() + timedelta(days=(1, 3, 7, 14)[stage])).isoformat() if stage < 4 else None
                    self.conn.execute('UPDATE review_items SET stage=?, due_date=? WHERE lesson_id=? AND question_id=? AND stage=?',
                                      (stage, due, ref['lesson_id'], ref['question_id'], expected))
                else:
                    self.conn.execute('INSERT OR IGNORE INTO review_items(lesson_id, question_id, subject, target, original, due_date) VALUES (?, ?, ?, ?, ?, ?)',
                                      (record['lesson']['lesson_id'], q['id'], q['subject'], q['target'], dumps(q),
                                       (now.date() + timedelta(days=1)).isoformat()))

    def file_record(self, path):
        row = self.conn.execute('SELECT * FROM generated_files WHERE path=?', (str(path),)).fetchone()
        return dict(row) if row else None

    def prepare_file(self, path, checksum):
        with self.conn:
            self.conn.execute('INSERT INTO generated_files(path, pending_checksum) VALUES (?, ?) ON CONFLICT(path) DO UPDATE SET pending_checksum=excluded.pending_checksum', (str(path), checksum))

    def finish_file(self, path, checksum):
        with self.conn:
            self.conn.execute('UPDATE generated_files SET checksum=?, pending_checksum=NULL WHERE path=?', (checksum, str(path)))
