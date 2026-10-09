"""Acceptance tests for the approved JSON configuration contract."""

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, time
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from n1_daily.cli import main
from n1_daily.config import Config, SecretSource
from n1_daily.store import Store
from n1_daily.workflow import generate
from test_workflow import FakeModel


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.config_path = self.root / "config.json"
        self.secrets_path = self.root / "secrets.json"

    def tearDown(self):
        self.tmp.cleanup()

    def config(self, values=None):
        self.config_path.write_text(json.dumps(values or {}))
        return Config.from_file(self.config_path)

    def secrets(self, values, mode=0o600):
        self.secrets_path.write_text(json.dumps(values))
        self.secrets_path.chmod(mode)
        return SecretSource(self.secrets_path, Config.from_file())

    def test_defaults_match_the_specification(self):
        config = Config.from_file()
        self.assertEqual(Path("~/Library/Application Support/N1Daily/lessons.sqlite3").expanduser(), config.db_path)
        self.assertEqual(Path(tempfile.gettempdir()).resolve() / "n1-daily-preview", config.preview_path)
        self.assertEqual(time(7, 45), config.generate_time)
        self.assertEqual(time(8), config.publish_time)
        self.assertEqual(time(12), config.cutoff_time)
        self.assertEqual(30, config.timeout_seconds)
        self.assertEqual("html", config.ruby_mode)
        self.assertIsNone(config.vault_path)

    def test_relative_data_paths_use_config_directory(self):
        config = self.config({"db_path": "data/lessons.sqlite3", "preview_path": "preview", "vault_path": "vault"})
        self.assertEqual(self.root / "data/lessons.sqlite3", config.db_path)
        self.assertEqual(self.root / "preview", config.preview_path)
        self.assertEqual(self.root / "vault", config.vault_path)

    def test_environment_has_no_effect_and_is_not_modified(self):
        with patch.dict(os.environ, {"N1_LLM_API_KEY": "inherited-key", "N1_TIMEOUT_SECONDS": "0", "N1_DB_PATH": "/wrong/path"}):
            before = dict(os.environ)
            config = self.config({"timeout_seconds": 45})
            credentials = self.secrets({"llm_api_key": "json-only-secret"}).require("llm_api_key")
            self.assertEqual(45, config.timeout_seconds)
            self.assertEqual("json-only-secret", credentials.llm_api_key)
            self.assertEqual(before, dict(os.environ))
            child = subprocess.run([sys.executable, "-c", "import os; print('json-only-secret' in os.environ.values())"], capture_output=True, text=True, check=True)
            self.assertEqual("False", child.stdout.strip())

    def test_invalid_settings_report_fields_without_values(self):
        for field, value in (("timeout_seconds", 0), ("timeout_seconds", 301),
                             ("timeout_seconds", True), ("timeout_seconds", "DO_NOT_LOG"),
                             ("timeout_seconds", float("inf")), ("publish_time", "07:30"),
                             ("timeout_seconds", 10 ** 1000), ("timeout_seconds", float("nan")),
                             ("publish_time", "8:00"), ("generate_time", "07:45:00"),
                             ("timezone", "DO_NOT_LOG"), ("ruby_mode", "DO_NOT_LOG"),
                             ("db_path", ""), ("vault_path", None), ("recipient", 12)):
            with self.subTest(field=field, value=value):
                with self.assertRaises(ValueError) as caught:
                    self.config({field: value})
                self.assertIn("config.json", str(caught.exception))
                self.assertIn(field, str(caught.exception))
                self.assertNotIn("DO_NOT_LOG", str(caught.exception))

    def test_malformed_nonobject_duplicate_and_unknown_fields(self):
        for raw in ('{"ruby_mode":"html","ruby_mode":"parentheses"}',
                    '{"typo":"DO_NOT_LOG"}', '{"llm_api_key":"DO_NOT_LOG"}',
                    '[]', '{"sender":"DO_NOT_LOG", invalid}'):
            with self.subTest(raw=raw):
                self.config_path.write_text(raw)
                with self.assertRaises(ValueError) as caught:
                    Config.from_file(self.config_path)
                self.assertNotIn("DO_NOT_LOG", str(caught.exception))

    def test_private_files_cannot_be_in_icloud_or_a_vault(self):
        for name in ("iCloud Drive", "Mobile Documents", "vault"):
            directory = self.root / name
            directory.mkdir()
            if name == "vault":
                (directory / ".obsidian").mkdir()
            path = directory / "config.json"
            path.write_text("{}")
            with self.subTest(name=name), self.assertRaises(ValueError):
                Config.from_file(path)
        config = self.config({"vault_path": str(self.root / "configured-vault")})
        with self.assertRaises(ValueError):
            SecretSource(self.root / "configured-vault/secrets.json", config).require("llm_api_key")

    def test_secret_permissions_and_ownership_are_enforced(self):
        for mode in (0o600, 0o400):
            self.assertEqual("fixture-key", self.secrets({"llm_api_key": "fixture-key"}, mode).require("llm_api_key").llm_api_key)
            self.secrets_path.chmod(0o600)
        for mode in (0o644, 0o660, 0o700):
            with self.subTest(mode=oct(mode)), self.assertRaises(ValueError):
                self.secrets({"llm_api_key": "fixture-key"}, mode).require("llm_api_key")
        source = self.secrets({"llm_api_key": "fixture-key"})
        with patch("n1_daily.config.os.getuid", return_value=os.getuid() + 1):
            with self.assertRaises(ValueError):
                source.require("llm_api_key")

    def test_secret_validation_and_repr_never_echo_credentials(self):
        source = self.secrets({"llm_api_key": "DO_NOT_LOG"})
        credentials = source.require("llm_api_key")
        self.assertNotIn("DO_NOT_LOG", repr(credentials))
        self.assertNotIn("DO_NOT_LOG", repr(source))
        for values in ({"llm_api_key": ["DO_NOT_LOG"]}, {"unexpected": "DO_NOT_LOG"}, {"llm_api_key": None}):
            with self.subTest(values=values), self.assertRaises(ValueError) as caught:
                self.secrets(values).require("llm_api_key")
            self.assertNotIn("DO_NOT_LOG", str(caught.exception))

    def test_unused_credentials_can_be_absent_or_empty(self):
        source = self.secrets({"google_client_secret": "gmail-secret", "google_refresh_token": "gmail-refresh", "llm_api_key": ""})
        credentials = source.require("google_client_secret", "google_refresh_token")
        self.assertEqual("gmail-refresh", credentials.google_refresh_token)
        with self.assertRaisesRegex(ValueError, "llm_api_key"):
            source.require("llm_api_key")
        self.assertEqual("llm-key", self.secrets({"llm_api_key": "llm-key"}).require("llm_api_key").llm_api_key)

    def test_bad_secret_json_reports_no_content(self):
        for raw in ('{"llm_api_key":"DO_NOT_LOG", invalid}',
                    '{"llm_api_key":"DO_NOT_LOG","llm_api_key":"duplicate"}',
                    '{"llm_api_key": NaN}', '[]'):
            with self.subTest(raw=raw):
                self.secrets_path.write_text(raw)
                self.secrets_path.chmod(0o600)
                with self.assertRaises(ValueError) as caught:
                    SecretSource(self.secrets_path, Config.from_file()).require("llm_api_key")
                self.assertIn("secrets.json", str(caught.exception))
                self.assertNotIn("DO_NOT_LOG", str(caught.exception))

    def test_example_files_use_valid_defaults_and_empty_credentials(self):
        import shutil
        repository = Path(__file__).parents[1]
        shutil.copyfile(repository / "config.example.json", self.config_path)
        shutil.copyfile(repository / "secrets.example.json", self.secrets_path)
        self.secrets_path.chmod(0o600)
        config = Config.from_file(self.config_path)
        self.assertEqual(time(7, 45), config.generate_time)
        self.assertEqual(30, config.timeout_seconds)
        with self.assertRaisesRegex(ValueError, "llm_api_key"):
            SecretSource(self.secrets_path, config).require("llm_api_key")


class JsonCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.config_path = self.root / "config.json"
        self.config_path.write_text(json.dumps({"db_path": "lessons.sqlite3"}))
        self.db = self.root / "lessons.sqlite3"
        self.now = datetime.fromisoformat("2026-10-09T08:00:00+08:00")

    def tearDown(self):
        self.tmp.cleanup()

    def invoke(self, args):
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(self.stdout), redirect_stderr(self.stderr):
            return main(args)

    def ready(self):
        store = Store(self.db)
        try:
            generate(store, "2026-10-09", FakeModel())
        finally:
            store.close()

    def test_live_commands_require_explicit_config(self):
        for command in ("generate", "publish", "daily", "schedule"):
            with self.subTest(command=command):
                self.assertEqual(2, self.invoke([command, "--db", str(self.db)]))
                self.assertIn("--config", self.stderr.getvalue())
        self.assertFalse(self.db.exists())

    def test_preview_never_reads_secrets_even_if_supplied(self):
        self.assertEqual(0, self.invoke(["preview", "--config", str(self.config_path), "--secrets", str(self.root / "missing-secrets.json"), "--output", str(self.root / "preview")]))
        self.assertTrue((self.root / "preview/lesson.html").exists())
        self.assertFalse(self.db.exists())

    def test_no_private_file_discovery_or_environment_fallback(self):
        (self.root / "config.json").write_text('{"llm_api_key":"DO_NOT_LOG"}')
        (self.root / "secrets.json").write_text("invalid JSON")
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            with patch.dict(os.environ, {"N1_TIMEOUT_SECONDS": "0", "N1_PREVIEW_PATH": str(self.root / "wrong")}):
                self.assertEqual(0, self.invoke(["dry-run", "--output", str(self.root / "preview")]))
        finally:
            os.chdir(previous)

    def test_explicit_overrides_take_precedence(self):
        self.ready()
        self.config_path.write_text(json.dumps({"db_path": "unused.sqlite3", "preview_path": "unused-preview", "ruby_mode": "html"}))
        result = self.invoke(["dry-run", "--config", str(self.config_path), "--db", str(self.db), "--date", "2026-10-09", "--output", str(self.root / "chosen"), "--ruby-mode", "parentheses"])
        self.assertEqual(0, result)
        self.assertFalse((self.root / "unused-preview").exists())
        self.assertIn("脆弱（ぜいじゃく）", (self.root / "chosen/lesson.md").read_text())

    def test_relative_cli_paths_use_working_directory(self):
        self.ready()
        work = self.root / "work"
        work.mkdir()
        self.config_path.write_text(json.dumps({"db_path": "unused.sqlite3", "preview_path": "unused-preview"}))
        previous = Path.cwd()
        try:
            os.chdir(work)
            self.assertEqual(0, self.invoke(["dry-run", "--config", "../config.json", "--db", "../lessons.sqlite3", "--date", "2026-10-09", "--output", "preview"]))
        finally:
            os.chdir(previous)
        self.assertTrue((work / "preview/lesson.md").exists())
        self.assertFalse((self.root / "unused-preview").exists())

    def test_missing_or_bad_secrets_block_generation_before_network(self):
        self.config_path.write_text(json.dumps({"db_path": "lessons.sqlite3", "llm_endpoint": "https://provider.example/chat/completions", "generation_model": "gen", "review_model": "review"}))
        secrets = self.root / "secrets.json"
        secrets.write_text('{"llm_api_key":["DO_NOT_LOG"]}')
        secrets.chmod(0o600)
        for extra in ([], ["--secrets", str(secrets)]):
            with self.subTest(extra=extra), patch("n1_daily.services.urlopen", side_effect=AssertionError("no network")):
                self.assertEqual(2, self.invoke(["generate", "--config", str(self.config_path), "--date", "2026-10-09", *extra]))
                self.assertNotIn("DO_NOT_LOG", self.stderr.getvalue())

    def test_json_config_drives_generation_and_review_without_secret_leaks(self):
        from test_content import passing_review, sample
        lesson = sample()
        self.config_path.write_text(json.dumps({"db_path": "lessons.sqlite3", "llm_endpoint": "https://provider.example/chat/completions", "generation_model": "fixture-generator", "review_model": "fixture-reviewer", "timeout_seconds": 9}))
        secrets = self.root / "secrets.json"
        secrets.write_text('{"llm_api_key":"DO_NOT_LOG_KEY"}')
        secrets.chmod(0o600)
        responses = [{"choices": [{"message": {"content": json.dumps(value)}}]}
                     for value in (lesson, passing_review(lesson))]
        before = dict(os.environ)
        with patch("n1_daily.llm.post_json", side_effect=responses) as request:
            self.assertEqual(0, self.invoke(["generate", "--config", str(self.config_path), "--secrets", str(secrets), "--date", "2026-10-09"]))
        self.assertEqual(2, request.call_count)
        self.assertEqual(["fixture-generator", "fixture-reviewer"], [call.args[1]["model"] for call in request.call_args_list])
        for call in request.call_args_list:
            self.assertEqual({"Authorization": "Bearer DO_NOT_LOG_KEY"}, call.args[2])
            self.assertEqual(9, call.args[3])
            self.assertNotIn("DO_NOT_LOG_KEY", json.dumps(call.args[1]))
        self.assertEqual(before, dict(os.environ))
        store = Store(self.db)
        try:
            record = store.get("2026-10-09")
            self.assertEqual("ready", record["status"])
            self.assertNotIn("DO_NOT_LOG_KEY", repr(record) + self.stdout.getvalue() + self.stderr.getvalue())
        finally:
            store.close()

    def test_generation_reuses_ready_lesson_without_reading_secret_file(self):
        self.ready()
        self.assertEqual(0, self.invoke(["generate", "--config", str(self.config_path), "--date", "2026-10-09", "--secrets", str(self.root / "missing.json")]))

    def test_ready_lesson_vault_delivery_needs_no_secrets(self):
        self.ready()
        vault = self.root / "temporary-vault"
        self.config_path.write_text(json.dumps({"db_path": "lessons.sqlite3", "vault_path": str(vault)}))
        with patch("n1_daily.cli.datetime") as clock:
            clock.now.return_value = self.now
            self.assertEqual(0, self.invoke(["daily", "--config", str(self.config_path), "--activate-vault", "--secrets", str(self.root / "missing.json")]))
        self.assertTrue((vault / "Lessons/2026/10/2026-10-09.md").exists())

    def test_ready_email_uses_only_gmail_credentials_and_completed_channel_loads_none(self):
        self.ready()
        self.config_path.write_text(json.dumps({"db_path": "lessons.sqlite3", "sender": "sender@example.com", "recipient": "reader@example.com", "google_client_id": "fixture-client"}))
        secrets = self.root / "secrets.json"
        secrets.write_text(json.dumps({"google_client_secret": "DO_NOT_LOG_SECRET", "google_refresh_token": "DO_NOT_LOG_REFRESH"}))
        secrets.chmod(0o600)
        args = ["publish", "--config", str(self.config_path), "--activate-email", "--secrets", str(secrets)]
        with patch("n1_daily.cli.datetime") as clock, patch("n1_daily.cli.GmailSender") as sender:
            clock.now.return_value = self.now
            sender.return_value.send.return_value = "gmail-fixture-id"
            self.assertEqual(0, self.invoke(args))
            sender.assert_called_once_with("fixture-client", "DO_NOT_LOG_SECRET", "DO_NOT_LOG_REFRESH", 30)
            secrets.unlink()
            self.assertEqual(0, self.invoke(args))
            self.assertEqual(1, sender.return_value.send.call_count)
        store = Store(self.db)
        try:
            record = store.get("2026-10-09")
            output = self.stdout.getvalue() + self.stderr.getvalue() + repr(record)
            self.assertNotIn("DO_NOT_LOG_SECRET", output)
            self.assertNotIn("DO_NOT_LOG_REFRESH", output)
        finally:
            store.close()

    def test_invalid_email_secrets_keep_vault_delivery_independent(self):
        self.ready()
        vault = self.root / "temporary-vault"
        self.config_path.write_text(json.dumps({"db_path": "lessons.sqlite3", "vault_path": str(vault), "sender": "sender@example.com", "recipient": "reader@example.com", "google_client_id": "fixture-client"}))
        secrets = self.root / "secrets.json"
        secrets.write_text('{"google_client_secret":"DO_NOT_LOG"}')
        secrets.chmod(0o644)
        with patch("n1_daily.cli.datetime") as clock, patch("n1_daily.cli.notification"), patch("n1_daily.services.urlopen", side_effect=AssertionError("no network")):
            clock.now.return_value = self.now
            self.assertEqual(1, self.invoke(["publish", "--config", str(self.config_path), "--secrets", str(secrets), "--activate-email", "--activate-vault"]))
        store = Store(self.db)
        try:
            record = store.get("2026-10-09")
            self.assertEqual("failed", record["email_status"])
            self.assertEqual("written", record["markdown_status"])
            self.assertNotIn("DO_NOT_LOG", repr(record))
        finally:
            store.close()

    def test_invalid_endpoint_reports_field_without_url_or_credentials(self):
        self.config_path.write_text(json.dumps({"llm_endpoint": "https://DO_NOT_LOG@provider.example/chat", "generation_model": "gen", "review_model": "review", "db_path": "lessons.sqlite3"}))
        self.assertEqual(2, self.invoke(["generate", "--config", str(self.config_path)]))
        self.assertIn("llm_endpoint", self.stderr.getvalue())
        self.assertNotIn("DO_NOT_LOG", self.stderr.getvalue())

    def test_unknown_send_does_not_read_secrets_or_retry(self):
        self.ready()
        store = Store(self.db)
        store.update("2026-10-09", email_status="unknown")
        store.close()
        with patch("n1_daily.cli.datetime") as clock, patch("n1_daily.cli.notification"), patch("n1_daily.cli.GmailSender") as sender:
            clock.now.return_value = self.now
            self.assertEqual(1, self.invoke(["publish", "--config", str(self.config_path), "--activate-email", "--secrets", str(self.root / "missing.json")]))
            sender.assert_not_called()
