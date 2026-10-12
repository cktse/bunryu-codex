import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from n1_daily.config import Config, SecretSource
from n1_daily.errors import InputError
from n1_daily.jsonio import loads


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "settings.json"

    def config(self, **values):
        self.path.write_text(json.dumps(values))
        return Config.load(self.path)

    def test_defaults_paths_overrides_and_legacy_times(self):
        with patch.dict(os.environ, {"N1_DB_PATH": "/ignored.sqlite3"}):
            config = self.config(db_path="history.sqlite3", generate_time="23:59",
                                 publish_time="00:01", cutoff_time="00:00")
        self.assertEqual((self.root / "history.sqlite3").resolve(), config.db_path)
        self.assertEqual("medium", config.generation_reasoning_effort)
        self.assertEqual("medium", config.review_reasoning_effort)
        self.assertEqual(30, config.timeout_seconds)

    def test_strict_json_and_config_fields(self):
        for text in ('{"x": 1, "x": 2}', '{"x": NaN}', '{"x": Infinity}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                loads(text)
        for values in (
            {"unknown": "bad"}, {"timeout_seconds": True}, {"timeout_seconds": 0},
            {"timeout_seconds": 301}, {"generation_reasoning_effort": "none"},
            {"review_reasoning_effort": None}, {"recipient": "Name <a@example.com>"},
            {"llm_endpoint": "https://example.com/responses?key=PRIVATE"},
            {"llm_endpoint": "http://example.com/responses"}, {"generate_time": "25:00"},
        ):
            with self.subTest(values=values), self.assertRaises(InputError):
                self.config(**values)
        for effort in ("low", "medium", "high", "xhigh", "max"):
            self.assertEqual(effort, self.config(review_reasoning_effort=effort).review_reasoning_effort)

    def test_secrets_are_deferred_private_and_do_not_change_environment(self):
        config = self.config()
        secret_path = self.root / "private.json"
        source = SecretSource(secret_path, config)
        secret_path.write_text('{"llm_api_key": "PRIVATE_KEY"}')
        secret_path.chmod(0o644)
        with self.assertRaises(InputError) as caught:
            source.require("llm_api_key")
        self.assertNotIn("PRIVATE_KEY", str(caught.exception))
        secret_path.chmod(0o600)
        before = dict(os.environ)
        self.assertEqual("PRIVATE_KEY", source.require("llm_api_key")["llm_api_key"])
        self.assertEqual(before, dict(os.environ))
        self.assertNotIn("PRIVATE_KEY", repr(source))
        with patch("n1_daily.config.os.getuid", return_value=os.getuid() + 1):
            with self.assertRaises(InputError):
                SecretSource(secret_path, config).require("llm_api_key")

    def test_private_paths_and_preview_boundaries(self):
        vault = self.root / "vault"
        vault.mkdir()
        (vault / ".obsidian").mkdir()
        with self.assertRaises(InputError):
            self.config(db_path=str(vault / "history.sqlite3")).check_database()
        config = self.config(vault_path=str(vault))
        for output in (vault / "preview", self.root / "LaunchAgents" / "job.plist"):
            with self.assertRaises(InputError):
                config.check_output(output)
        with self.assertRaises(InputError):
            self.config(db_path=str(self.root / "Mobile Documents" / "history.sqlite3")).check_database()

    def test_huge_timeout_is_an_input_error(self):
        with self.assertRaises(InputError):
            self.config(timeout_seconds=10 ** 1000)
