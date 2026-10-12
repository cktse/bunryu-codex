from contextlib import redirect_stdout, redirect_stderr
from datetime import timedelta
from io import StringIO
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch

from helpers import Model, Sender, lesson
from n1_daily.cli import main
from n1_daily.config import Config
from n1_daily.store import Store, run_lock
from n1_daily.workflow import generate
from test_workflow import DAY, NOW


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.db = self.root / 'lessons.sqlite3'
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps({'db_path': str(self.db)}))
        self.patches = [patch('n1_daily.cli.clock', return_value=NOW), patch('n1_daily.cli.notification')]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def call(self, *args):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(args))
        return code, out.getvalue() + err.getvalue()

    def test_sample_preview_never_opens_database_or_secrets(self):
        with patch('n1_daily.cli.Store', side_effect=AssertionError('database opened')):
            code, output = self.call('preview', '--config', str(self.config), '--secrets', '/missing/private.json',
                                     '--output', str(self.root / 'preview'))
        self.assertEqual(0, code, output)
        self.assertIn('not checked', output)
        self.assertEqual({'lesson.html', 'lesson.eml', 'lesson.md', 'lesson.txt', 'lesson.json'},
                         {p.name for p in (self.root / 'preview').iterdir()})
        self.assertFalse(self.db.exists())

    def test_readonly_status_and_preview_preserve_permissions_and_sending(self):
        store = Store(self.db)
        generate(store, DAY, Model())
        store.update(DAY, email_status='sending')
        store.close()
        self.db.chmod(0o400)
        original = self.db.read_bytes()
        code, output = self.call('status', '--db', str(self.db), '--date', DAY)
        self.assertEqual(0, code, output)
        self.assertEqual('ready', json.loads(output)['phase'])
        self.assertEqual('sending', json.loads(output)['email_status'])
        code, output = self.call('validate', '--db', str(self.db), '--date', DAY)
        self.assertEqual(0, code, output)
        self.assertEqual(original, self.db.read_bytes())
        self.assertEqual(0o400, self.db.stat().st_mode & 0o777)

    def test_invalid_inputs_missing_records_and_overlap_exit_two(self):
        cases = [('generate',), ('publish', '--config', str(self.config)),
                 ('publish', '--config', str(self.config), '--activate-email', '--date', '2026-10-11'),
                 ('validate', '--date', DAY, '--db', str(self.db)),
                 ('resolve-email', '--db', str(self.db), '--retry', '--mailbox-checked'),
                 ('validate', '--date', '20261012'), ('dry-run',)]
        for args in cases:
            with self.subTest(args=args):
                self.assertEqual(2, self.call(*args)[0])
        self.assertFalse(self.db.exists())
        with run_lock(self.db), patch('n1_daily.cli.Model', side_effect=AssertionError('model created')):
            code, output = self.call('generate', '--config', str(self.config))
        self.assertEqual(2, code)
        self.assertIn('active', output)

    def test_generate_reports_provider_failure_directly(self):
        from n1_daily.errors import ServiceError
        model = Model()
        model.draft_error = ServiceError('LLM draft HTTP 429: insufficient_quota; request-id: req-123')
        with patch('n1_daily.cli.Model', return_value=model):
            code, output = self.call('generate', '--config', str(self.config), '--date', DAY)
        self.assertEqual(1, code)
        self.assertIn('insufficient_quota', output)
        self.assertIn('req-123', output)

    def test_schedule_is_inactive_does_not_read_secrets_or_create_storage(self):
        output = self.root / 'job.plist'
        code, detail = self.call('schedule', '--config', str(self.config), '--secrets', str(self.root / 'missing.json'), '--output', str(output))
        self.assertEqual(0, code, detail)
        template = plistlib.loads(output.read_bytes())
        self.assertTrue(template['RunAtLoad'])
        self.assertEqual(60, template['StartInterval'])
        self.assertIn('--activate-email', template['ProgramArguments'])
        self.assertIn('--activate-vault', template['ProgramArguments'])
        self.assertFalse(self.db.exists())

    def test_daily_does_not_deliver_generated_lesson_after_rollover(self):
        current = [NOW]
        model = Model()
        original = model.review
        def review(*args):
            verdict = original(*args)
            current[0] += timedelta(days=1)
            return verdict
        model.review = review
        sender = Sender()
        with patch('n1_daily.cli.clock', side_effect=lambda: current[0]), patch('n1_daily.cli.Model', return_value=model), patch('n1_daily.cli.Sender', return_value=sender):
            code, output = self.call('daily', '--config', str(self.config), '--activate-email')
        self.assertEqual(1, code, output)
        self.assertEqual([], sender.messages)
        store = Store(self.db, readonly=True)
        try:
            self.assertEqual('ready', store.get(DAY)['phase'])
            self.assertIsNone(store.get('2026-10-13'))
        finally:
            store.close()

    def test_explicit_database_override_precedes_configured_path_validation(self):
        vault = self.root / 'vault'
        vault.mkdir()
        (vault / '.obsidian').mkdir()
        self.config.write_text(json.dumps({'db_path': str(vault / 'unsafe.sqlite3')}))
        with patch('n1_daily.cli.Model', return_value=Model()):
            code, output = self.call('generate', '--config', str(self.config), '--db', str(self.db))
        self.assertEqual(0, code, output)
        self.assertFalse((vault / 'unsafe.sqlite3').exists())
