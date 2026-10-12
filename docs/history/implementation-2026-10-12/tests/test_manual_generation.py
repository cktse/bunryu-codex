"""Approved section 11: manual generation can run outside the daily window."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import datetime
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from responses_fixtures import completed_response
from test_content import passing_review, sample
from n1_daily.cli import main
from n1_daily.content import sample_assignment
from n1_daily.store import Store, run_lock


class ManualGenerationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.db = self.root / "lessons.sqlite3"
        self.config = self.root / "config.json"
        self.secrets = self.root / "secrets.json"
        self.config.write_text(json.dumps({"db_path": str(self.db), "llm_endpoint": "https://provider.example/v1/responses",
                                         "generation_model": "gen", "review_model": "review"}))
        self.secrets.write_text('{"llm_api_key":"fixture"}')
        self.secrets.chmod(0o600)
        self.store = Store(self.db)
        self.now = datetime.fromisoformat("2026-10-09T20:00:00+08:00")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda: self.store.close())

    def lesson(self, day):
        lesson = deepcopy(sample())
        lesson.update(date=day, lesson_id=f"n1-{day}")
        return lesson

    def invoke(self, command="generate", day=None, now=None, extra=()):
        arguments = [command, "--config", str(self.config), "--secrets", str(self.secrets), *extra]
        if day:
            arguments += ["--date", day]
        self.stderr = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(self.stderr), patch("n1_daily.cli.datetime") as clock, patch("n1_daily.cli.notification"):
            clock.now.return_value = now or self.now
            return main(arguments)

    def test_generation_accepts_past_today_future_and_any_time(self):
        for day, now in (("2026-10-08", self.now), ("2026-10-10", self.now),
                         ("2026-10-09", self.now.replace(hour=0)), ("2026-10-09", self.now.replace(hour=23, minute=59))):
            with self.subTest(day=day, now=now):
                lesson = self.lesson(day)
                with patch("n1_daily.llm.post_json", side_effect=[completed_response(lesson), completed_response(passing_review(lesson))]) as request:
                    self.assertEqual(0, self.invoke(day=day, now=now))
                record = self.store.get(day)
                self.assertEqual("ready", record["status"])
                self.assertIsNone(record["first_published_at"])
                self.assertEqual(("pending", "pending"), (record["email_status"], record["markdown_status"]))
                self.assertEqual(2, request.call_count)
                self.store.conn.execute("DELETE FROM lessons")
                self.store.conn.commit()

    def test_omitted_date_defaults_to_today_even_after_cutoff(self):
        lesson = self.lesson("2026-10-09")
        with patch("n1_daily.llm.post_json", side_effect=[completed_response(lesson), completed_response(passing_review(lesson))]):
            self.assertEqual(0, self.invoke())
        self.assertEqual("ready", self.store.get("2026-10-09")["status"])

    def test_interrupted_generation_and_review_resume_at_night_after_restart(self):
        day = "2026-10-09"
        lesson = self.lesson(day)
        for phase in ("generating", "reviewing"):
            with self.subTest(phase=phase):
                responses = [TimeoutError()] if phase == "generating" else [completed_response(lesson), TimeoutError()]
                with patch("n1_daily.llm.post_json", side_effect=responses):
                    self.assertEqual(1, self.invoke(now=self.now.replace(hour=8)))
                before = self.store.get(day)
                self.assertEqual(phase, before["phase"])
                self.store.close()
                self.store = Store(self.db)
                replies = [completed_response(lesson), completed_response(passing_review(lesson))] if phase == "generating" else [completed_response(passing_review(lesson))]
                with patch("n1_daily.llm.post_json", side_effect=replies) as request:
                    self.assertEqual(0, self.invoke())
                after = self.store.get(day)
                self.assertEqual(before["assignment"], after["assignment"])
                self.assertEqual(before["attempt_count"], after["attempt_count"])
                self.assertEqual(["gen", "review"] if phase == "generating" else ["review"], [call.args[1]["model"] for call in request.call_args_list])
                self.store.conn.execute("DELETE FROM lessons")
                self.store.conn.commit()

    def test_setup_failure_can_be_repaired_and_retried_outside_window(self):
        original = self.config.read_text()
        self.config.write_text(json.dumps({"db_path": str(self.db)}))
        with patch("n1_daily.llm.post_json") as request:
            self.assertEqual(1, self.invoke(now=self.now.replace(hour=3)))
        request.assert_not_called()
        self.assertEqual(0, self.store.get("2026-10-09")["attempt_count"])
        self.config.write_text(original)
        lesson = self.lesson("2026-10-09")
        with patch("n1_daily.llm.post_json", side_effect=[completed_response(lesson), completed_response(passing_review(lesson))]):
            self.assertEqual(0, self.invoke())
        self.assertEqual(1, self.store.get("2026-10-09")["attempt_count"])

    def test_ready_lesson_reuse_needs_no_settings_or_secrets(self):
        day = "2026-10-08"
        lesson = self.lesson(day)
        with patch("n1_daily.llm.post_json", side_effect=[completed_response(lesson), completed_response(passing_review(lesson))]):
            self.assertEqual(0, self.invoke(day=day))
        before = self.store.get(day)
        self.config.write_text(json.dumps({"db_path": str(self.db)}))
        self.secrets.unlink()
        with patch("n1_daily.llm.post_json", side_effect=AssertionError("no request")):
            self.assertEqual(0, self.invoke(day=day, now=self.now.replace(hour=2)))
        self.assertEqual(before, self.store.get(day))

    def test_exhausted_and_closed_failures_are_preserved_without_calls(self):
        day = "2026-10-08"
        for attempts, reasons in ((2, ["Correction limit exhausted"]), (0, ["Missed publication window; no backlog will be sent"])):
            with self.subTest(attempts=attempts):
                self.store.begin(day, sample_assignment(day))
                self.store.update(day, status="failed", phase="failed", attempt_count=attempts, failure_reasons=reasons)
                before = self.store.get(day)
                with patch("n1_daily.llm.post_json", side_effect=AssertionError("no request")):
                    self.assertEqual(1, self.invoke(day=day))
                self.assertEqual(before, self.store.get(day))

    def test_correction_budget_and_feedback_survive_outside_window(self):
        day = "2026-10-09"
        lesson = self.lesson(day)
        self.store.begin(day, sample_assignment(day))
        self.store.update(day, status="failed", phase="generating", attempt_count=2, lesson=lesson,
                          failure_reasons=["Transport outage"], correction_feedback=["Fix ambiguous q1"])
        with patch("n1_daily.llm.post_json", side_effect=[completed_response(lesson), completed_response(passing_review(lesson))]) as request:
            self.assertEqual(0, self.invoke())
        payload = json.loads(request.call_args_list[0].args[1]["input"][1]["content"])
        self.assertEqual(["Fix ambiguous q1"], payload["failures"])
        self.assertEqual(lesson["questions"], payload["previous_draft"]["questions"])
        self.assertEqual(2, self.store.get(day)["attempt_count"])

    def test_run_lock_still_prevents_overlapping_manual_generation(self):
        with run_lock(self.db), patch("n1_daily.llm.post_json") as request:
            self.assertEqual(2, self.invoke())
        request.assert_not_called()
        self.assertIn("active", self.stderr.getvalue())

    def test_daily_and_publish_retain_their_scheduling_restrictions(self):
        with patch("n1_daily.llm.post_json", side_effect=AssertionError("no request")), patch("n1_daily.cli.GmailSender", side_effect=AssertionError("no send")):
            self.assertEqual(0, self.invoke("daily", now=self.now.replace(hour=3), extra=["--activate-email"]))
            self.assertIsNone(self.store.get("2026-10-09"))
            self.assertEqual(1, self.invoke("daily", extra=["--activate-email"]))
            self.assertEqual("failed", self.store.get("2026-10-09")["phase"])
            self.assertEqual(2, self.invoke("publish", extra=["--activate-email"]))
