from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

from test_content import sample
from n1_daily.cli import main
from n1_daily.config import Config
from n1_daily.files import checksum, write_generated
from n1_daily.llm import JsonModel
from n1_daily.services import GmailSender, DefiniteFailure, UncertainSend
from n1_daily.store import Store


class ServiceTests(unittest.TestCase):
    def sender(self):
        return GmailSender("client", "secret", "refresh", timeout=9)

    def response(self, payload):
        class Response(io.BytesIO):
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.close()
        return Response(json.dumps(payload).encode())

    def test_gmail_oauth_refresh_and_base64_mime(self):
        with patch("n1_daily.services.urlopen", return_value=self.response({"access_token": "access"})) as refresh:
            with patch("n1_daily.services.post_json", return_value={"id": "gmail-id"}) as send:
                self.assertEqual("gmail-id", self.sender().send(b"MIME bytes"))
                self.assertEqual(9, refresh.call_args.kwargs["timeout"])
                import base64
                self.assertEqual(b"MIME bytes", base64.urlsafe_b64decode(send.call_args.args[1]["raw"]))

    def test_gmail_rejection_and_ambiguous_server_error(self):
        for code, exception in ((403, DefiniteFailure), (429, DefiniteFailure), (503, UncertainSend)):
            with self.subTest(code=code):
                with patch("n1_daily.services.urlopen", return_value=self.response({"access_token": "access"})):
                    with patch("n1_daily.services.post_json", side_effect=HTTPError("https://example.test", code, "error", {}, None)):
                        with self.assertRaises(exception):
                            self.sender().send(b"test")

    def test_refresh_failure_cannot_send_email(self):
        with patch("n1_daily.services.urlopen", side_effect=TimeoutError):
            with patch("n1_daily.services.post_json") as send:
                with self.assertRaises(DefiniteFailure):
                    self.sender().send(b"test")
                send.assert_not_called()

    def test_model_prompt_contains_history_requirements_and_review_criteria(self):
        model = JsonModel("https://model.example.test/chat/completions", "secret", "gen", "review", timeout=7)
        context = {"assignment": {"date": "2026-10-09"}, "previous_seven_published_lessons": [],
                   "all_earlier_subject_target_pairs": [["kanji", "旧語"]], "review_originals": []}
        with patch("n1_daily.llm.post_json", return_value={"choices": [{"message": {"content": json.dumps(sample(), ensure_ascii=False)}}]}) as post:
            model.draft(context, ["q1.options missing D"], sample())
            body = post.call_args.args[1]
            user = json.loads(body["messages"][1]["content"])
            self.assertEqual(context, user["context"])
            self.assertIn("q1.options missing D", user["failures"])
            self.assertEqual(7, post.call_args.args[3])
            model.review(sample(), context)
            prompt = post.call_args.args[1]["messages"][0]["content"]
            self.assertIn("unique_answer", prompt)
            self.assertIn("all four", prompt)
            self.assertIn("offending_text", prompt)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def invoke(self, arguments):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return main(arguments)

    def config_file(self, values=None):
        path = self.root / "config.json"
        path.write_text(json.dumps(values or {}))
        return str(path)

    def test_preview_and_dry_run_produce_both_outputs_without_services_or_vault(self):
        vault = self.root / "real-vault"
        vault.mkdir()
        config = self.config_file({"vault_path": str(vault)})
        for command in ("preview", "dry-run"):
            with patch("n1_daily.services.urlopen", side_effect=AssertionError("network forbidden")):
                output = self.root / command
                self.assertEqual(0, self.invoke([command, "--config", config, "--output", str(output)]))
                self.assertTrue((output / "lesson.html").exists())
                self.assertTrue((output / "lesson.md").exists())
                self.assertTrue((output / "lesson.eml").exists())
            self.assertEqual([], list(vault.iterdir()))

    def test_preview_rejects_vault_output_directory(self):
        vault = self.root / "vault"
        config = self.config_file({"vault_path": str(vault)})
        self.assertEqual(2, self.invoke(["preview", "--config", config, "--output", str(vault / "previews")]))
        self.assertFalse(vault.exists())

    def test_daily_requires_explicit_activation(self):
        db = self.root / "lessons.sqlite3"
        self.assertEqual(2, self.invoke(["daily", "--config", self.config_file(), "--db", str(db)]))
        self.assertFalse(db.exists())

    def test_configuration_requires_valid_time_order_and_finite_timeout(self):
        for values in ({"timeout_seconds": 0}, {"publish_time": "07:30"}, {"timezone": "Europe/London"}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                Config.from_file(self.config_file(values))

    def test_preview_saved_lesson_has_no_llm_dependency(self):
        from test_workflow import FakeModel
        from n1_daily.workflow import generate
        db = self.root / "lessons.sqlite3"
        store = Store(db)
        generate(store, "2026-10-09", FakeModel())
        store.close()
        self.assertEqual(0, self.invoke(["dry-run", "--date", "2026-10-09", "--db", str(db), "--output", str(self.root / "saved")]))

    def test_schedule_export_never_installs_a_job(self):
        import plistlib
        path = self.root / "schedule.plist"
        with patch("subprocess.run", side_effect=AssertionError("must not install")):
            self.assertEqual(0, self.invoke(["schedule", "--output", str(path), "--config", self.config_file(), "--secrets", str(self.root / "missing-secrets.json")]))
        data = plistlib.loads(path.read_bytes())
        self.assertEqual(60, data["StartInterval"])
        self.assertIn("daily", data["ProgramArguments"])
        self.assertIn("--config", data["ProgramArguments"])
        self.assertIn("--secrets", data["ProgramArguments"])
        self.assertNotIn("EnvironmentVariables", data)
        self.assertNotIn("--env-file", data["ProgramArguments"])

    def test_schedule_export_rejects_a_launchd_job_directory(self):
        destination = self.root / "LaunchAgents/n1.plist"
        self.assertEqual(2, self.invoke(["schedule", "--output", str(destination), "--config", self.config_file(), "--secrets", str(self.root / "secrets.json")]))
        self.assertFalse(destination.exists())

    def test_atomic_file_recovers_a_completed_write_before_status_commit(self):
        store = Store(self.root / "lessons.sqlite3")
        path = self.root / "lesson.md"
        try:
            digest = checksum(b"complete lesson")
            store.prepare_file(path, digest)
            path.write_bytes(b"complete lesson")
            self.assertEqual(digest, write_generated(store, path, "complete lesson"))
            self.assertEqual(digest, store.file_record(path)["checksum"])
            path.write_text("user changes")
            with self.assertRaisesRegex(ValueError, "Preserved unexpected edits"):
                write_generated(store, path, "updated generated lesson")
            self.assertEqual("user changes", path.read_text())
        finally:
            store.close()
