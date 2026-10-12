from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from helpers import Model, Sender, lesson, review
from n1_daily.config import Config
from n1_daily.errors import DeliveryError, InputError, ModelOutputError, ServiceError
from n1_daily.store import Store, run_lock
from n1_daily.workflow import generate, publish, resolve_email

NOW = datetime(2026, 10, 12, 8, tzinfo=ZoneInfo('Asia/Hong_Kong'))
DAY = NOW.date().isoformat()


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / 'lessons.sqlite3'
        self.store = Store(self.path)
        self.addCleanup(lambda: self.store.close())
        self.vault = self.root / 'vault'
        self.vault.mkdir()
        self.config = Config(db_path=self.path, vault_path=self.vault, sender='a@example.com', recipient='b@example.com')
        self.sender = Sender()

    def ready(self, day=DAY):
        return generate(self.store, day, Model(lesson(day)))

    def publish(self, **kwargs):
        return publish(self.store, DAY, self.config, self.sender, now=lambda: NOW, **kwargs)

    def restart(self):
        self.store.close()
        self.store = Store(self.path)

    def test_ready_reuse_and_independent_delivery(self):
        record = self.ready()
        self.assertEqual('ready', record['phase'])
        self.assertEqual(1, record['attempt_count'])
        model = Model()
        generate(self.store, DAY, model)
        self.assertEqual([], model.calls)
        self.sender.prepare_error = ServiceError('OAuth rejected: invalid_grant')
        record = self.publish(email=True, vault=True)
        self.assertEqual('failed', record['email_status'])
        self.assertEqual('written', record['markdown_status'])
        self.assertEqual('written', record['index_status'])
        self.assertEqual(6, len(self.store.review_items()))
        note = Path(record['markdown_path'])
        before = note.stat().st_mtime_ns
        self.sender.prepare_error = None
        record = self.publish(email=True, vault=True)
        self.assertEqual('sent', record['email_status'])
        self.assertEqual(before, note.stat().st_mtime_ns)
        self.assertEqual(6, len(self.store.review_items()))
        self.assertEqual(NOW.isoformat(), record['first_published_at'])

    def test_setup_failure_before_actual_draft_and_service_retry_budget(self):
        model = Model()
        model.prepare = lambda phase: (_ for _ in ()).throw(InputError('Missing llm_api_key'))
        record = generate(self.store, DAY, model)
        self.assertEqual(0, record['attempt_count'])
        self.assertEqual('generating', record['phase'])
        model = Model()
        model.draft_error = ServiceError('HTTP 429: insufficient_quota')
        record = generate(self.store, DAY, model)
        self.assertEqual(1, record['attempt_count'])
        self.assertIn('insufficient_quota', record['failure_reasons'][0])
        self.restart()
        record = self.ready()
        self.assertEqual('ready', record['phase'])
        self.assertEqual(1, record['attempt_count'])

    def test_one_correction_survives_interrupted_corrected_request(self):
        model = Model()
        bad = lesson()
        bad['questions'][0]['options'].pop('D')
        results = iter([bad, ServiceError('HTTP 429: quota')])
        def draft(*args):
            value = next(results)
            if isinstance(value, Exception):
                raise value
            return value
        model.draft = draft
        record = generate(self.store, DAY, model)
        self.assertEqual(2, record['attempt_count'])
        self.assertEqual('generating', record['phase'])
        feedback = record['correction_feedback']
        self.assertTrue(feedback)
        self.restart()
        model = Model()
        record = generate(self.store, DAY, model)
        self.assertEqual(feedback, model.calls[0][2])
        self.assertEqual(2, record['attempt_count'])
        self.assertEqual('ready', record['phase'])

    def test_second_content_failure_is_terminal_and_saved_review_decision_is_atomic(self):
        model = Model()
        model.verdict = {'questions': []}
        record = generate(self.store, DAY, model)
        self.assertEqual('failed', record['phase'])
        self.assertEqual(2, record['attempt_count'])
        self.assertEqual({'questions': []}, record['review_result'])
        self.restart()
        model = Model()
        self.assertEqual('failed', generate(self.store, DAY, model)['phase'])
        self.assertEqual([], model.calls)

    def test_review_service_failure_resumes_without_redrafting(self):
        model = Model()
        model.review_error = ServiceError('review HTTP 503: capacity')
        record = generate(self.store, DAY, model)
        self.assertEqual('reviewing', record['phase'])
        self.restart()
        model = Model()
        record = generate(self.store, DAY, model)
        self.assertEqual(['review'], [call[0] for call in model.calls])
        self.assertEqual('ready', record['phase'])

    def test_saved_content_edit_blocks_all_unfinished_delivery(self):
        record = self.ready()
        changed = record['lesson']
        changed['questions'][0]['answer_ja'] += ' changed'
        self.store.update(DAY, lesson=changed)
        record = self.publish(email=True, vault=True)
        self.assertTrue(any('binding' in text for text in record['failure_reasons']))
        self.assertEqual([], self.sender.messages)
        self.assertEqual([], list(self.vault.iterdir()))

    def test_unknown_email_requires_resolution_and_preserves_bytes(self):
        self.ready()
        self.sender.send_error = DeliveryError('Gmail read lost', uncertain=True)
        record = self.publish(email=True)
        self.assertEqual('unknown', record['email_status'])
        self.assertIsNone(record['first_published_at'])
        frozen = record['message_bytes']
        self.sender.send_error = None
        self.publish(email=True)
        self.assertEqual(1, len(self.sender.messages))
        resolve_email(self.store, DAY, mailbox_checked=True, retry=True, now=NOW)
        record = self.publish(email=True)
        self.assertEqual([frozen, frozen], self.sender.messages)
        self.assertEqual('sent', record['email_status'])

    def test_frozen_email_destination_mismatch_before_service(self):
        self.ready()
        self.sender.send_error = DeliveryError('Gmail HTTP 429: rate limit')
        record = self.publish(email=True)
        self.assertEqual('failed', record['email_status'])
        self.config.recipient = 'changed@example.com'
        self.sender.send_error = None
        record = self.publish(email=True)
        self.assertIn('recipient', record['email_error'])
        self.assertEqual(1, self.sender.preparations)
        self.assertEqual(1, len(self.sender.messages))

    def test_invalid_resolution_is_read_only_and_late_confirmation_keeps_anchor(self):
        self.ready()
        self.sender.send_error = DeliveryError('lost', uncertain=True)
        self.publish(email=True)
        for args in ({'retry': True, 'accepted_at': NOW}, {'accepted_id': ' ', 'accepted_at': NOW},
                     {'accepted_id': 'id', 'accepted_at': NOW - timedelta(seconds=1)},
                     {'accepted_id': 'id', 'accepted_at': NOW + timedelta(seconds=1)},
                     {'accepted_id': 'id', 'accepted_at': NOW.replace(tzinfo=None)}):
            with self.subTest(args=args), self.assertRaises(InputError):
                resolve_email(self.store, DAY, mailbox_checked=True, now=NOW, **args)
        later = NOW + timedelta(hours=1)
        record = publish(self.store, DAY, self.config, self.sender, vault=True, now=lambda: later)
        due_dates = [item['due_date'] for item in self.store.review_items()]
        resolve_email(self.store, DAY, mailbox_checked=True, accepted_id='confirmed', accepted_at=NOW, now=later)
        record = self.store.get(DAY)
        self.assertEqual(later.isoformat(), record['first_published_at'])
        self.assertEqual(NOW.isoformat(), record['email_completed_at'])
        self.assertEqual(due_dates, [item['due_date'] for item in self.store.review_items()])

    def test_vault_setup_repair_binding_and_edited_index(self):
        self.ready()
        self.config.vault_path = self.root / 'missing'
        record = self.publish(vault=True)
        self.assertFalse(self.config.vault_path.exists())
        self.assertIsNone(record['vault_root'])
        self.config.vault_path = self.vault
        (self.vault / 'Index.md').write_text('personal index')
        record = self.publish(vault=True)
        self.assertEqual('written', record['markdown_status'])
        self.assertEqual('failed', record['index_status'])
        self.assertEqual('personal index', (self.vault / 'Index.md').read_text())
        second = self.root / 'second'
        second.mkdir()
        self.config.vault_path = second
        record = self.publish(vault=True)
        self.assertIn('destination', record['index_error'])
        self.assertEqual([], list(second.iterdir()))

    def test_file_write_checkpoint_recovers_without_overwriting_an_edit(self):
        self.ready()
        from n1_daily.files import write_file
        original = self.store.finish_file
        def crash(*args):
            raise KeyboardInterrupt()
        self.store.finish_file = crash
        with self.assertRaises(KeyboardInterrupt):
            self.publish(vault=True)
        self.store.finish_file = original
        record = self.publish(vault=True)
        self.assertEqual('written', record['markdown_status'])
        note = Path(record['markdown_path'])
        note.write_text('my edits')
        self.publish(vault=True)
        self.assertEqual('my edits', note.read_text())

    def test_midnight_stops_next_channel_but_preserves_started_send(self):
        self.ready()
        current = [NOW]
        original = self.sender.send
        def send(message):
            result = original(message)
            current[0] += timedelta(days=1)
            return result
        self.sender.send = send
        record = publish(self.store, DAY, self.config, self.sender, email=True, vault=True, now=lambda: current[0])
        self.assertEqual('sent', record['email_status'])
        self.assertEqual('pending', record['markdown_status'])
        self.assertTrue(any('date' in reason for reason in record['failure_reasons']))

    def test_lock_blocks_other_dates_and_readonly_does_not_recover(self):
        self.ready()
        self.store.update(DAY, email_status='sending')
        with run_lock(self.path):
            with self.assertRaises(InputError):
                with run_lock(self.path):
                    self.fail('overlap was permitted')
            reader = Store(self.path, readonly=True)
            try:
                self.assertEqual('sending', reader.get(DAY)['email_status'])
            finally:
                reader.close()
        with run_lock(self.path):
            self.store.recover_inflight()
        self.assertEqual('unknown', self.store.get(DAY)['email_status'])

    def test_model_refusal_consumes_one_correction_then_stops(self):
        model = Model()
        model.draft_error = ModelOutputError('LLM refusal: unable to supply this lesson')
        record = generate(self.store, DAY, model)
        self.assertEqual(2, len(model.calls))
        self.assertEqual(2, record['attempt_count'])
        self.assertEqual('failed', record['phase'])
        self.assertIn('unable to supply', record['failure_reasons'][0])

    def test_index_retry_does_not_contact_gmail_or_rewrite_note(self):
        self.ready()
        from n1_daily.workflow import write_file
        def fail_index(store, root, path, text):
            if path.name == 'Index.md':
                raise OSError('temporary index failure')
            return write_file(store, root, path, text)
        with patch('n1_daily.workflow.write_file', side_effect=fail_index):
            record = self.publish(email=True, vault=True)
        note = Path(record['markdown_path'])
        before = note.stat().st_mtime_ns
        self.assertEqual('failed', record['index_status'])
        self.config.sender = self.config.recipient = ''
        self.sender.prepare_error = AssertionError('completed email contacted')
        record = self.publish(email=True, vault=True)
        self.assertEqual('written', record['index_status'])
        self.assertEqual(before, note.stat().st_mtime_ns)
        self.assertEqual(1, len(self.sender.messages))

    def test_saved_review_stage_snapshot_is_bound_to_readiness(self):
        self.ready()
        self.store.update(DAY, review_stages={'tampered/q1': 2})
        record = self.publish(email=True)
        self.assertTrue(any('binding' in reason for reason in record['failure_reasons']))
        self.assertEqual([], self.sender.messages)
