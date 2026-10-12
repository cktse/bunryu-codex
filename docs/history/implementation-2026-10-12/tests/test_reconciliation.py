"""Regressions for approved specification section 9, using offline fixtures."""

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from http.client import IncompleteRead
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_content import passing_review, sample
from responses_fixtures import completed_response
from test_workflow import FakeModel, FakeSender
from n1_daily.cli import main
from n1_daily.config import Config
from n1_daily.content import check_lesson, check_review, sample_assignment
from n1_daily.llm import JsonModel
from n1_daily.services import DefiniteFailure, GmailSender, UncertainSend
from n1_daily.store import Store, run_lock
from n1_daily.workflow import daily, deliver, generate


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.db = self.root / "lessons.sqlite3"
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps({"db_path": str(self.db)}))
        self.now = datetime.fromisoformat("2026-10-09T08:00:00+08:00")
        self.store = Store(self.db)
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(self.store.close)

    def ready(self):
        return generate(self.store, "2026-10-09", FakeModel())

    def invoke(self, arguments, now=None):
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(self.stdout), redirect_stderr(self.stderr), patch("n1_daily.cli.datetime") as clock:
            clock.now.return_value = now or self.now
            clock.fromisoformat = datetime.fromisoformat
            with patch("n1_daily.cli.notification") as notification:
                code = main(arguments)
        self.notifications = notification.call_count
        return code

    def test_failed_drafts_do_not_block_future_generation_or_reserve_targets(self):
        for value in ({"questions": None}, {"questions": [None, {}]}, sample()):
            with self.subTest(value=value):
                self.store.begin("2026-10-08", sample_assignment("2026-10-08"))
                self.store.update("2026-10-08", lesson=value, status="failed", phase="failed")
                self.assertEqual("ready", self.ready()["status"])
                stored = self.store.get("2026-10-08")["lesson"]
                self.assertEqual(value["questions"], stored["questions"])
                self.store.conn.execute("DELETE FROM lessons WHERE date='2026-10-09'")
                self.store.conn.commit()

    def test_malformed_published_history_reports_date_and_preserves_usable_questions(self):
        old = sample()
        old.update(date="2026-10-08", lesson_id="n1-2026-10-08")
        old["questions"][0] = None
        old["questions"][1] = {"id": "q2", "subject": "vocabulary", "target": "usable", "options": {}}
        self.store.begin("2026-10-08", sample_assignment("2026-10-08"))
        self.store.update("2026-10-08", lesson=old, first_published_at="2026-10-08T08:00:00+08:00")
        errors = io.StringIO()
        with redirect_stderr(errors):
            context = self.store.context("2026-10-09", self.store.assignment("2026-10-09"))
        self.assertIn(["vocabulary", "usable"], context["all_earlier_subject_target_pairs"])
        self.assertIn("2026-10-08", errors.getvalue())
        self.assertIn("questions", errors.getvalue())
        self.assertIsNone(self.store.get("2026-10-08")["lesson"]["questions"][0])

    def test_incomplete_llm_read_records_retryable_failure_and_resumes(self):
        model = JsonModel("https://provider.example/chat", "fixture-key", "gen", "review")
        with patch("n1_daily.llm.post_json", side_effect=IncompleteRead(b"DO_NOT_LOG", 30)):
            record = generate(self.store, "2026-10-09", model)
        self.assertEqual("failed", record["status"])
        self.assertEqual("generating", record["phase"])
        self.assertEqual(1, record["attempt_count"])
        self.assertNotIn("DO_NOT_LOG", str(record["failure_reasons"]))
        self.assertEqual("ready", self.ready()["status"])

    def test_incomplete_review_preserves_draft_and_does_not_generate_again(self):
        model = JsonModel("https://provider.example/chat", "fixture-key", "gen", "review")
        response = completed_response(sample())
        with patch("n1_daily.llm.post_json", side_effect=[response, IncompleteRead(b"private", 20)]):
            record = generate(self.store, "2026-10-09", model)
        self.assertEqual("reviewing", record["phase"])
        retry = FakeModel()
        self.assertEqual("ready", generate(self.store, "2026-10-09", retry)["status"])
        self.assertEqual(["review"], [call[0] for call in retry.calls])

    def test_truncated_oauth_is_definite_and_truncated_gmail_send_is_unknown(self):
        sender = GmailSender("client", "secret", "refresh")
        with patch("n1_daily.services.urlopen", side_effect=IncompleteRead(b"private", 20)):
            with self.assertRaises(DefiniteFailure):
                sender.send(b"message")
        response = io.BytesIO(b'{"access_token":"fixture"}')
        with patch("n1_daily.services.urlopen", return_value=response), patch("n1_daily.services.post_json", side_effect=IncompleteRead(b"private", 20)):
            with self.assertRaises(UncertainSend):
                sender.send(b"message")

    def test_malformed_oauth_token_is_definite_before_gmail_contact(self):
        sender = GmailSender("client", "secret", "refresh")
        for token in (None, 42, {}, [], "", " "):
            with self.subTest(token=token), patch("n1_daily.services.urlopen", return_value=io.BytesIO(json.dumps({"access_token": token}).encode())), patch("n1_daily.services.post_json") as send:
                with self.assertRaises(DefiniteFailure):
                    sender.send(b"message")
                send.assert_not_called()

    def test_missing_vault_does_not_block_email_and_completed_channels_need_no_settings(self):
        self.ready()
        values = {"db_path": str(self.db), "sender": "sender@example.test", "recipient": "reader@example.test", "google_client_id": "client"}
        self.config.write_text(json.dumps(values))
        secrets = self.root / "secrets.json"
        secrets.write_text(json.dumps({"google_client_secret": "fixture", "google_refresh_token": "fixture"}))
        secrets.chmod(0o600)
        with patch("n1_daily.cli.GmailSender") as sender:
            sender.return_value.send.return_value = "accepted"
            self.assertEqual(1, self.invoke(["daily", "--config", str(self.config), "--secrets", str(secrets), "--activate-email", "--activate-vault"]))
            sender.return_value.send.assert_called_once()
        record = self.store.get("2026-10-09")
        self.assertEqual("sent", record["email_status"])
        self.assertEqual("failed", record["markdown_status"])
        self.assertIn("vault_path", record["markdown_error"])
        first_published = record["first_published_at"]
        vault = self.root / "repaired-vault"
        secrets.unlink()
        self.config.write_text(json.dumps({"db_path": str(self.db), "vault_path": str(vault)}))
        with patch("n1_daily.cli.GmailSender", side_effect=AssertionError("completed email must not be retried")):
            self.assertEqual(0, self.invoke(["daily", "--config", str(self.config), "--activate-email", "--activate-vault"]))
        self.assertTrue((vault / "Lessons/2026/10/2026-10-09.md").exists())
        self.assertEqual(first_published, self.store.get("2026-10-09")["first_published_at"])
        self.config.write_text(json.dumps({"db_path": str(self.db)}))
        self.assertEqual(0, self.invoke(["daily", "--config", str(self.config), "--activate-email", "--activate-vault"]))

    def test_setup_failure_records_reason_without_using_draft_budget_and_notifies_once(self):
        self.config.write_text(json.dumps({"db_path": str(self.db), "vault_path": str(self.root / "vault"), "llm_endpoint": "https://provider.example/chat", "generation_model": "gen", "review_model": "review"}))
        args = ["daily", "--config", str(self.config), "--activate-vault"]
        with patch("n1_daily.services.urlopen", side_effect=AssertionError("network forbidden")):
            self.assertEqual(1, self.invoke(args))
            self.assertEqual(1, self.notifications)
            record = self.store.get("2026-10-09")
            self.assertEqual("failed", record["status"])
            self.assertEqual(0, record["attempt_count"])
            self.assertIn("secrets", str(record["failure_reasons"]))
            self.assertEqual(1, self.invoke(args))
            self.assertEqual(0, self.notifications)
        self.assertEqual("ready", self.ready()["status"])

    def test_review_setup_failure_preserves_phase_draft_and_attempt(self):
        self.ready()
        before = self.store.get("2026-10-09")["lesson"]
        self.store.update("2026-10-09", status="draft", phase="reviewing")
        self.assertEqual(1, self.invoke(["generate", "--config", str(self.config)]))
        record = self.store.get("2026-10-09")
        self.assertEqual("reviewing", record["phase"])
        self.assertEqual(1, record["attempt_count"])
        self.assertEqual(before["questions"], record["lesson"]["questions"])

    def test_ruby_field_paths_are_exact_and_unused_fields_are_rejected(self):
        for field in ("distractors_ja.B.extra", "distractors_ja.A", "reconstructed_sentence_ja"):
            lesson = sample()
            question = lesson["questions"][0]
            question["reconstructed_sentence_ja"] = "斟酌"
            question["distractors_ja"]["A"] = "斟酌"
            text = question["distractors_ja"]["B"][0] if field.endswith("extra") else "斟"
            question["ruby"] = [{"field": field, "start": 0, "end": 1, "text": text, "reading": "しん"}]
            with self.subTest(field=field):
                self.assertTrue(any("ruby" in error for error in check_lesson(lesson, sample_assignment(lesson["date"]), [])))

    def test_failed_reviews_require_all_correction_details(self):
        for field in ("reason", "offending_text", "change"):
            for value in (None, "", False, []):
                review = passing_review(sample())
                result = {"status": "fail", "reason": "wrong rule", "offending_text": "offending phrase", "change": "correct the rule"}
                result[field] = value
                review["questions"][0]["criteria"]["explanation_accuracy"] = result
                with self.subTest(field=field, value=value):
                    self.assertTrue(any(field in error for error in check_review(sample(), review)))

    def test_incomplete_review_feedback_is_saved_and_correction_budget_stays_bounded(self):
        class IncompleteReviewer(FakeModel):
            def review(self, lesson, context):
                result = passing_review(lesson)
                result["questions"][0]["criteria"]["explanation_accuracy"] = {"status": "fail", "reason": "wrong rule"}
                return result
        record = generate(self.store, "2026-10-09", IncompleteReviewer())
        self.assertEqual("failed", record["status"])
        self.assertEqual(2, record["attempt_count"])
        self.assertTrue(any("offending_text" in reason for reason in record["correction_feedback"]))
        self.assertEqual({"status": "fail", "reason": "wrong rule"}, record["review_result"]["questions"][0]["criteria"]["explanation_accuracy"])
        self.assertEqual(2, self.ready()["attempt_count"])

    def test_optional_text_rejects_false_nonstrings_and_ordering_markup(self):
        for field in ("supplement_en", "supplement_yue"):
            for value in (None, 0, False, [], {}):
                lesson = sample()
                lesson["questions"][0][field] = value
                with self.subTest(field=field, value=value):
                    self.assertTrue(any(field in error for error in check_lesson(lesson, sample_assignment(lesson["date"]), [])))
        lesson = sample()
        q = lesson["questions"][5]
        q["prefix_ja"] = "<b>"
        self.assertTrue(any("prefix_ja" in error for error in check_lesson(lesson, sample_assignment(lesson["date"]), [])))

    def test_saved_validation_rechecks_ready_content_and_creates_no_output(self):
        self.ready()
        lesson = self.store.get("2026-10-09")["lesson"]
        del lesson["questions"][0]["options"]["D"]
        self.store.update("2026-10-09", lesson=lesson)
        for command in ("preview", "validate"):
            arguments = [command, "--date", "2026-10-09", "--db", str(self.db)]
            if command == "preview":
                arguments += ["--output", str(self.root / "output")]
            with self.subTest(command=command):
                self.assertEqual(1, self.invoke(arguments))
                self.assertIn("q1.options", self.stderr.getvalue())
                self.assertFalse((self.root / "output").exists())

    def test_standalone_later_theme_is_valid_and_reports_limits(self):
        lesson = sample()
        lesson["theme"] = "社会"
        path = self.root / "lesson.json"
        path.write_text(json.dumps(lesson))
        self.assertEqual(0, self.invoke(["validate", "--lesson", str(path)]))
        self.assertIn("assignment/history", self.stdout.getvalue())
        self.assertIn("not performed", self.stdout.getvalue())

    def test_standalone_later_rotation_and_review_reference_shapes(self):
        lesson = sample()
        lesson["questions"][3:] = [lesson["questions"][4], lesson["questions"][5], lesson["questions"][3]]
        q = lesson["questions"][5]
        q.update(type="reading_comprehension", passage_ja="この説明から筆者の意図を読み取ってください。")
        for i, question in enumerate(lesson["questions"], 1):
            question["id"] = f"q{i}"
        path = self.root / "lesson.json"
        path.write_text(json.dumps(lesson))
        self.assertEqual(0, self.invoke(["validate", "--lesson", str(path)]))
        for ref in (False, [], {}, {"lesson_id": "original"}, {"lesson_id": 1, "question_id": "q1"}):
            with self.subTest(reference=ref):
                lesson["questions"][0]["review_of"] = ref
                path.write_text(json.dumps(lesson))
                self.assertEqual(1, self.invoke(["validate", "--lesson", str(path)]))
                self.assertIn("review_of", self.stderr.getvalue())

    def test_saved_validation_uses_persisted_assignment_and_prior_history(self):
        self.ready()
        self.store.update("2026-10-09", first_published_at=self.now.isoformat())
        self.assertEqual(0, self.invoke(["validate", "--date", "2026-10-09", "--db", str(self.db)]))
        self.assertIn("saved assignment", self.stdout.getvalue())
        old = sample()
        old.update(date="2026-10-08", lesson_id="n1-2026-10-08")
        self.store.begin("2026-10-08", sample_assignment("2026-10-08"))
        self.store.update("2026-10-08", lesson=old, first_published_at="2026-10-08T08:00:00+08:00")
        self.assertEqual(1, self.invoke(["validate", "--date", "2026-10-09", "--db", str(self.db)]))
        self.assertIn("target", self.stderr.getvalue())

    def test_missing_file_context_and_corrupt_storage_are_input_errors(self):
        path = self.root / "lesson.json"
        path.write_text(json.dumps(sample()))
        missing = self.root / "missing.sqlite3"
        self.assertEqual(2, self.invoke(["validate", "--lesson", str(path), "--db", str(missing)]))
        self.assertFalse(missing.exists())
        broken = self.root / "broken.sqlite3"
        broken.write_bytes(b"not a database")
        self.assertEqual(2, self.invoke(["status", "--db", str(broken)]))
        self.ready()
        self.store.conn.execute("UPDATE lessons SET assignment='{}' WHERE date='2026-10-09'")
        self.store.conn.commit()
        self.assertEqual(2, self.invoke(["validate", "--date", "2026-10-09", "--db", str(self.db)]))
        self.assertIn("assignment", self.stderr.getvalue())

    def test_file_with_explicit_context_requires_saved_assignment_and_checks_it(self):
        self.ready()
        path = self.root / "lesson.json"
        lesson = sample()
        lesson["theme"] = "社会"
        path.write_text(json.dumps(lesson))
        self.assertEqual(1, self.invoke(["validate", "--lesson", str(path), "--db", str(self.db)]))
        self.assertIn("theme", self.stderr.getvalue())
        lesson.update(date="2026-10-10", lesson_id="n1-2026-10-10")
        path.write_text(json.dumps(lesson))
        self.assertEqual(2, self.invoke(["validate", "--lesson", str(path), "--db", str(self.db)]))

    def test_input_conflict_and_duplicate_json_keys_are_rejected(self):
        with self.assertRaises(SystemExit):
            self.invoke(["preview", "--date", "2026-10-09", "--lesson", str(self.root / "missing.json")])
        path = self.root / "lesson.json"
        path.write_text('{"date":"2026-10-09","date":"2026-10-10"}')
        self.assertEqual(2, self.invoke(["validate", "--lesson", str(path)]))
        self.assertIn("duplicate", self.stderr.getvalue())

    def test_readonly_commands_do_not_mutate_database_or_recover_active_send(self):
        self.ready()
        self.store.update("2026-10-09", email_status="sending")
        self.db.chmod(0o644)
        before, mode = self.db.read_bytes(), self.db.stat().st_mode
        with run_lock(self.db):
            for command in ("status", "validate", "preview"):
                arguments = [command, "--date", "2026-10-09", "--db", str(self.db)]
                if command == "preview":
                    arguments += ["--output", str(self.root / "preview")]
                with self.subTest(command=command):
                    self.assertEqual(0, self.invoke(arguments))
                    self.assertEqual(before, self.db.read_bytes())
                    self.assertEqual(mode, self.db.stat().st_mode)
        self.assertEqual("sending", self.store.get("2026-10-09")["email_status"])
        missing = self.root / "missing.sqlite3"
        self.assertEqual(2, self.invoke(["status", "--db", str(missing)]))
        self.assertFalse(missing.exists())

    def test_publication_date_and_window_rejected_before_services(self):
        for day, clock in (("2026-10-08", self.now), ("2026-10-10", self.now),
                           ("2026-10-09", self.now.replace(hour=7, minute=59)),
                           ("2026-10-09", self.now.replace(hour=12, minute=1))):
            with self.subTest(day=day, clock=clock), patch("n1_daily.services.urlopen", side_effect=AssertionError("network forbidden")):
                self.assertEqual(2, self.invoke(["publish", "--activate-email", "--config", str(self.config), "--date", day], clock))
                self.assertIsNone(self.store.get(day))

    def test_endpoint_rejects_all_queries_fragments_and_missing_paths(self):
        for endpoint in ("https://provider.example/chat?client_secret=PRIVATE", "https://provider.example/chat?%63lient_secret=PRIVATE",
                         "https://provider.example/chat?version=1", "https://provider.example/chat?", "https://provider.example/chat#PRIVATE",
                         "https://PRIVATE@provider.example/chat", "https://@provider.example/chat", "https://provider.example:bad/chat", "http://provider.example/chat", "https://provider.example"):
            self.config.write_text(json.dumps({"llm_endpoint": endpoint}))
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ValueError) as error:
                    Config.from_file(self.config)
                self.assertNotIn("PRIVATE", str(error.exception))

    def test_request_timeouts_and_configuration_errors_have_safe_retry_state(self):
        from n1_daily.cli import ConfiguredModel
        from n1_daily.config import SecretSource
        for settings, raw, mode in (({}, None, 0o600),
                                    ({"llm_endpoint": "https://provider.example/chat", "generation_model": "gen", "review_model": "review"}, '{"llm_api_key":["PRIVATE"]}', 0o600),
                                    ({"llm_endpoint": "https://provider.example/chat", "generation_model": "gen", "review_model": "review"}, '{"llm_api_key":"PRIVATE"}', 0o644)):
            with self.subTest(settings=settings, mode=mode):
                path = self.root / "secrets.json"
                if raw is not None:
                    path.write_text(raw)
                    path.chmod(mode)
                self.config.write_text(json.dumps(settings))
                config = Config.from_file(self.config)
                model = ConfiguredModel(config, SecretSource(path if raw is not None else None, config))
                with patch("n1_daily.services.urlopen", side_effect=AssertionError("network forbidden")):
                    record = generate(self.store, "2026-10-09", model)
                self.assertEqual("failed", record["status"])
                self.assertEqual(0, record["attempt_count"])
                self.assertNotIn("PRIVATE", str(record["failure_reasons"]))

    def test_actual_completion_time_drives_exposure_and_channel_attempt_times(self):
        self.ready()
        moments = iter([self.now, self.now.replace(minute=2), self.now.replace(minute=3), self.now.replace(minute=4), self.now.replace(minute=5)])
        record = deliver(self.store, "2026-10-09", FakeSender(), self.root / "vault", "a@example.test", "b@example.test", self.now,
                         current_time=lambda: next(moments))
        self.assertEqual(self.now.replace(minute=2).isoformat(), record["first_published_at"])
        self.assertEqual(self.now.isoformat(), record["email_attempt_at"])
        self.assertEqual(self.now.replace(minute=3).isoformat(), record["markdown_attempt_at"])

    def test_markdown_first_publication_uses_write_completion_time(self):
        self.ready()
        moments = iter([self.now, self.now.replace(minute=1), self.now.replace(minute=2)])
        record = deliver(self.store, "2026-10-09", None, self.root / "vault", "", "", self.now,
                         current_time=lambda: next(moments))
        self.assertEqual(self.now.isoformat(), record["markdown_attempt_at"])
        self.assertEqual(self.now.replace(minute=1).isoformat(), record["first_published_at"])
        self.assertEqual("written", record["index_status"])

    def test_partial_window_closure_notifies_once_and_preserves_exposure(self):
        self.ready()
        self.store.channel_success("2026-10-09", self.now, email_status="sent")
        self.config.write_text(json.dumps({"db_path": str(self.db), "vault_path": str(self.root / "vault")}))
        args = ["daily", "--config", str(self.config), "--activate-email", "--activate-vault"]
        late = self.now.replace(hour=12, minute=1)
        self.assertEqual(1, self.invoke(args, late))
        self.assertEqual(1, self.notifications)
        self.assertIn("partial", self.stdout.getvalue())
        self.assertEqual(0, self.invoke(args, late))
        self.assertEqual(0, self.notifications)
        self.assertEqual(self.now.isoformat(), self.store.get("2026-10-09")["first_published_at"])
        self.assertEqual(6, len(self.store.review_items()))
        self.assertFalse((self.root / "vault").exists())

    def test_index_attempt_does_not_start_after_late_lesson_completion(self):
        self.ready()
        before, after = self.now.replace(hour=11, minute=59), self.now.replace(hour=12, minute=1)
        moments = iter([before, before, after, after, after])
        outcome = daily(self.store, None, None, self.root / "vault", "", "", before, current_time=lambda: next(moments))
        self.assertEqual("partial", outcome)
        self.assertEqual("written", self.store.get("2026-10-09")["markdown_status"])
        self.assertEqual("pending", self.store.get("2026-10-09")["index_status"])
        self.assertFalse((self.root / "vault/Index.md").exists())

    def test_late_email_completion_preserves_success_and_skips_new_vault_attempt(self):
        self.ready()
        before = self.now.replace(hour=11, minute=59)
        after = self.now.replace(hour=12, minute=1)
        moments = iter([before, before, after, after, after, after, after])
        sender = FakeSender()
        outcome = daily(self.store, None, sender, self.root / "vault", "a@example.test", "b@example.test", before,
                        current_time=lambda: next(moments))
        self.assertEqual("partial", outcome)
        self.assertEqual("sent", self.store.get("2026-10-09")["email_status"])
        self.assertFalse((self.root / "vault").exists())
        self.assertEqual(after.isoformat(), self.store.get("2026-10-09")["first_published_at"])
        self.assertEqual("closed", daily(self.store, None, sender, self.root / "vault", "a@example.test", "b@example.test", after))
        self.assertEqual(1, len(sender.messages))

    def test_publication_after_midnight_uses_observed_date_for_review_delay(self):
        self.ready()
        before = self.now.replace(hour=23, minute=59)
        after = datetime.fromisoformat("2026-10-10T00:01:00+08:00")
        moments = iter([before, after])
        record = deliver(self.store, "2026-10-09", FakeSender(), None, "a@example.test", "b@example.test", before,
                         current_time=lambda: next(moments), cutoff=before.time())
        self.assertEqual(after.isoformat(), record["first_published_at"])
        self.assertEqual({"2026-10-11"}, {item["due_date"] for item in self.store.review_items()})

    def test_manual_generation_reuses_ready_content_at_any_time_and_rejects_overlap(self):
        self.ready()
        args = ["generate", "--config", str(self.config)]
        for clock in (self.now.replace(hour=7, minute=44), self.now.replace(hour=12, minute=1)):
            self.assertEqual(0, self.invoke(args, clock))
        with run_lock(self.db):
            self.assertEqual(2, self.invoke(args))
            self.assertIn("active", self.stderr.getvalue())

    def test_review_ties_use_question_id_before_lesson_id_and_skip_incompatible(self):
        for lesson_id, question_id, subject in (("a", "q6", "vocabulary"), ("b", "q1", "kanji"), ("c", "q2", "unsupported")):
            self.store.conn.execute("INSERT INTO review_items(lesson_id, question_id, subject, target, original, due_date) VALUES (?, ?, ?, ?, ?, ?)",
                                    (lesson_id, question_id, subject, "target", "{}", "2026-10-09"))
        self.store.conn.commit()
        refs = [slot["review_of"] for slot in self.store.assignment("2026-10-09")["slots"] if slot["review_of"]]
        self.assertEqual(["q1", "q6"], [ref["question_id"] for ref in refs])
        self.assertEqual(3, len(self.store.review_items()))

    def test_review_item_sort_and_cap_preserve_postponed_due_items(self):
        for lesson_id, question_id, subject in (("a", "q6", "kanji"), ("b", "q1", "vocabulary"), ("c", "q3", "grammar")):
            self.store.conn.execute("INSERT INTO review_items(lesson_id, question_id, subject, target, original, due_date) VALUES (?, ?, ?, ?, ?, ?)",
                                    (lesson_id, question_id, subject, "target", "{}", "2026-10-09"))
        self.store.conn.commit()
        chosen = [slot["review_of"]["question_id"] for slot in self.store.assignment("2026-10-09")["slots"] if slot["review_of"]]
        self.assertEqual({"q1", "q3"}, set(chosen))
        self.assertEqual(["q1", "q3", "q6"], [item["question_id"] for item in self.store.review_items()])

    def test_unknown_email_resolution_requires_mailbox_and_aware_acceptance_time(self):
        self.ready()
        self.store.update("2026-10-09", email_status="unknown", message_bytes=b"saved message")
        base = ["resolve-email", "--db", str(self.db), "--date", "2026-10-09"]
        self.assertEqual(2, self.invoke(base + ["--retry"]))
        self.assertEqual(2, self.invoke(base + ["--mailbox-checked", "--accepted-id", "id", "--accepted-at", "2026-10-09T08:00:00"]))
        self.assertEqual(0, self.invoke(base + ["--mailbox-checked", "--accepted-id", "id", "--accepted-at", "2026-10-09T00:03:00+00:00"]))
        record = self.store.get("2026-10-09")
        self.assertEqual("sent", record["email_status"])
        self.assertEqual("2026-10-09T08:03:00+08:00", record["first_published_at"])
        self.assertEqual(b"saved message", record["message_bytes"])

    def test_unknown_retry_reuses_saved_bytes_and_does_not_send_during_resolution(self):
        self.ready()
        self.store.update("2026-10-09", email_status="sending", message_bytes=b"saved message")
        self.assertEqual(0, self.invoke(["resolve-email", "--db", str(self.db), "--mailbox-checked", "--retry"]))
        sender = FakeSender()
        deliver(self.store, "2026-10-09", sender, None, "a@example.test", "b@example.test", self.now)
        self.assertEqual([b"saved message"], sender.messages)


if __name__ == "__main__":
    unittest.main()
