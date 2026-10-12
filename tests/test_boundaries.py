from contextlib import redirect_stdout, redirect_stderr
from io import BytesIO, StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from helpers import Model, lesson
from n1_daily.cli import main
from n1_daily.config import Config
from n1_daily.content import check_lesson
from n1_daily.errors import InputError
from n1_daily.files import write_file
from n1_daily.http import diagnostic
from n1_daily.store import Store
from n1_daily.workflow import generate, publish
from test_workflow import DAY, NOW


class BoundaryTests(unittest.TestCase):
    def test_http_body_limit_does_not_expose_split_secret_or_mask_read_error(self):
        secret = 'PRIVATE_TOKEN_LONG'
        prefix = b'x' * (65536 - 5)
        error = HTTPError('https://example.com', 429, 'bad', {}, BytesIO(prefix + secret.encode()))
        result = diagnostic('LLM draft', error, (secret,))
        self.assertNotIn('PRIVA', result)
        self.assertIn('truncated', result)
        error = HTTPError('https://example.com', 503, 'bad', {}, BytesIO(b'<html>capacity exhausted</html>'))
        self.assertIn('capacity exhausted', diagnostic('LLM draft', error))

    def test_every_renderer_field_rejects_wrong_types_without_crash(self):
        for field in ('id', 'type', 'subject', 'target', 'review_of', 'prompt_ja', 'options', 'correct_option_id',
                      'answer_ja', 'explanation_ja', 'distractors_ja', 'supplement_en', 'supplement_yue', 'ruby'):
            for value in (None, True, 1, [], {}, ['bad']):
                if field == 'review_of' and value is None or field == 'ruby' and value == []:
                    continue
                with self.subTest(field=field, value=value):
                    content = lesson()
                    content['questions'][0][field] = value
                    self.assertTrue(check_lesson(content))

    def test_untracked_notes_symlinks_and_edited_index_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            vault = root / 'vault'
            vault.mkdir()
            outside = root / 'outside'
            outside.mkdir()
            store = Store(root / 'db.sqlite3')
            try:
                path = vault / 'Index.md'
                path.write_text('untracked')
                with self.assertRaises(InputError):
                    write_file(store, vault, path, 'replacement')
                path.unlink()
                write_file(store, vault, path, 'tracked')
                path.write_text('my edit')
                with self.assertRaises(InputError):
                    write_file(store, vault, path, 'replacement')
                (vault / 'Lessons').symlink_to(outside)
                with self.assertRaises(InputError):
                    write_file(store, vault, vault / 'Lessons' / 'lesson.md', 'test')
                self.assertFalse((outside / 'lesson.md').exists())
                self.assertEqual('my edit', path.read_text())
            finally:
                store.close()

    def test_unchanged_daily_failure_notifies_once(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.json'
            config.write_text(json.dumps({'db_path': str(Path(directory) / 'db.sqlite3')}))
            args = ['daily', '--config', str(config), '--activate-email']
            with patch('n1_daily.cli.clock', return_value=NOW), patch('n1_daily.cli.notification') as notify, redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(1, main(args))
                self.assertEqual(1, main(args))
                self.assertEqual(1, notify.call_count)

    def test_resolution_argument_errors_do_not_recover_sending_or_migrate(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / 'db.sqlite3'
            store = Store(db)
            generate(store, DAY, Model())
            store.update(DAY, email_status='sending', message_bytes=b'MIME', email_attempt_at=NOW.isoformat())
            store.close()
            original = db.read_bytes()
            args = ['resolve-email', '--db', str(db), '--date', DAY, '--mailbox-checked', '--retry', '--accepted-at', NOW.isoformat()]
            with patch('n1_daily.cli.clock', return_value=NOW), redirect_stderr(StringIO()):
                self.assertEqual(2, main(args))
            self.assertEqual(original, db.read_bytes())

    def test_completed_channels_require_no_settings_or_service(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'db.sqlite3')
            try:
                generate(store, DAY, Model())
                store.update(DAY, email_status='sent', markdown_status='written', index_status='written')
                record = publish(store, DAY, Config(), None, email=True, vault=True, now=lambda: NOW)
                self.assertEqual('sent', record['email_status'])
            finally:
                store.close()

    def test_corrupt_index_history_remains_a_storage_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / 'vault'
            vault.mkdir()
            store = Store(root / 'db.sqlite3')
            try:
                store.ensure('2026-10-11')
                store.update('2026-10-11', markdown_status='written')
                generate(store, DAY, Model())
                with store.conn:
                    store.conn.execute("UPDATE lessons SET lesson='invalid JSON' WHERE date='2026-10-11'")
                with self.assertRaisesRegex(InputError, 'invalid JSON'):
                    publish(store, DAY, Config(vault_path=vault), None, vault=True, now=lambda: NOW)
                self.assertEqual('written', store.get(DAY)['markdown_status'])
            finally:
                store.close()
