from contextlib import redirect_stderr
from copy import deepcopy
from datetime import timedelta
from io import StringIO
from pathlib import Path
import sqlite3
import tempfile
import unittest

from helpers import Model, lesson, review
from n1_daily.content import assignment, check_lesson
from n1_daily.errors import InputError, ServiceError
from n1_daily.jsonio import dumps
from n1_daily.store import Store
from n1_daily.workflow import generate, publication_checks
from test_workflow import DAY, NOW


def content_for(record):
    content = lesson(record['date'])
    content['theme'] = record['assignment']['theme']
    for q, slot in zip(content['questions'], record['assignment']['slots']):
        q['type'], q['subject'], q['review_of'] = slot['type'], slot['subject'], deepcopy(slot['review_of'])
        # Keep this fixture structurally valid across rotation, with clearly distinct exercises.
        for field in ('prefix_ja', 'suffix_ja', 'correct_order', 'starred_position', 'reconstructed_sentence_ja'):
            q.pop(field, None)
        q['prompt_ja'] += '\n' + record['date']
        q['target'] += ' ' + record['date']
        if q['review_of']:
            original = next(item['question'] for item in record['context']['review_originals'] if item['reference'] == q['review_of'])
            q['target'] = original['target']
        if q['type'] == 'sentence_ordering':
            q.update(prefix_ja='', suffix_ja='', correct_order=list('ABCD'),
                     starred_position='ABCD'.index(q['correct_option_id']) + 1,
                     reconstructed_sentence_ja=''.join(q['options'][key] for key in 'ABCD'))
        if q['type'] == 'reading_comprehension':
            q['passage_ja'] = 'これはテスト用の文章です。'
    return content


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'lessons.sqlite3'
        self.store = Store(self.path)
        self.addCleanup(self.store.close)

    def ready(self, day):
        record = self.store.ensure(day)
        content = content_for(record)
        self.assertEqual([], check_lesson(content, record['assignment'], record['context']['history']))
        result = generate(self.store, day, Model(content))
        self.assertEqual('ready', result['phase'], result['failure_reasons'])
        return result

    def test_assignments_use_only_due_earlier_published_originals_and_remain_frozen(self):
        self.ready(DAY)
        self.store.channel_success(DAY, NOW, markdown_status='written')
        past = self.store.ensure('2026-10-11')
        self.assertFalse(any(slot['review_of'] for slot in past['assignment']['slots']))
        future = self.store.ensure('2026-10-13')
        references = [slot['review_of'] for slot in future['assignment']['slots'] if slot['review_of']]
        self.assertEqual(['q1', 'q2'], [ref['question_id'] for ref in references])
        self.assertEqual(2, len(future['review_stages']))
        self.assertEqual('社会', future['assignment']['theme'])
        saved = deepcopy(future)
        self.ready('2026-10-14')
        self.store.channel_success('2026-10-14', NOW + timedelta(days=2), markdown_status='written')
        future = self.store.ensure('2026-10-13')
        self.assertEqual(saved['assignment'], future['assignment'])
        self.assertEqual(saved['context'], future['context'])
        self.assertTrue(self.store.stale_reviews(future))

    def test_intervening_publication_blocks_new_target_collision(self):
        future = self.ready('2026-10-13')
        earlier = lesson(DAY)
        earlier['questions'][0]['target'] = future['lesson']['questions'][0]['target']
        generate(self.store, DAY, Model(earlier))
        self.store.channel_success(DAY, NOW, email_status='sent')
        reasons = publication_checks(self.store, future)
        self.assertTrue(any('target' in reason for reason in reasons), reasons)
        self.assertEqual(future['lesson'], self.store.get('2026-10-13')['lesson'])

    def test_review_intervals_and_late_acceptance_do_not_consume_stage_twice(self):
        self.ready(DAY)
        self.store.channel_success(DAY, NOW, email_status='sent')
        next_day = '2026-10-13'
        record = self.ready(next_day)
        self.ready('2026-10-14')  # Both select stage zero before either is published.
        self.store.channel_success(next_day, NOW + timedelta(days=1), email_status='sent')
        first = next(item for item in self.store.review_items() if item['lesson_id'] == 'n1-' + DAY and item['question_id'] == 'q1')
        self.assertEqual((1, '2026-10-16'), (first['stage'], first['due_date']))
        self.store.channel_success('2026-10-14', NOW + timedelta(days=2), email_status='sent')
        current = next(item for item in self.store.review_items() if item['lesson_id'] == 'n1-' + DAY and item['question_id'] == 'q1')
        self.assertEqual(first, current)
        with self.store.conn:
            self.store.conn.execute("UPDATE review_items SET due_date=NULL WHERE NOT (lesson_id=? AND question_id='q1')", ('n1-' + DAY,))
        for selected, stage, expected_due in (('2026-10-16', 2, '2026-10-23'), ('2026-10-23', 3, '2026-11-06'), ('2026-11-06', 4, None)):
            self.ready(selected)
            when = NOW.replace(year=int(selected[:4]), month=int(selected[5:7]), day=int(selected[8:]))
            self.store.channel_success(selected, when, email_status='sent')
            item = next(item for item in self.store.review_items() if item['lesson_id'] == 'n1-' + DAY and item['question_id'] == 'q1')
            self.assertEqual((stage, expected_due), (item['stage'], item['due_date']))
            with self.store.conn:
                self.store.conn.execute("UPDATE review_items SET due_date=NULL WHERE NOT (lesson_id=? AND question_id='q1')", ('n1-' + DAY,))

    def test_malformed_history_warns_but_invalid_storage_fails(self):
        self.ready(DAY)
        self.store.channel_success(DAY, NOW, email_status='sent')
        broken = lesson(DAY)
        broken['questions'][0] = None
        self.store.update(DAY, lesson=broken)
        output = StringIO()
        with redirect_stderr(output):
            history = self.store.history('2026-10-13')
        self.assertEqual(5, len(history[0]['questions']))
        self.assertIn('q1', output.getvalue())
        with self.store.conn:
            self.store.conn.execute("UPDATE lessons SET lesson='invalid JSON' WHERE date=?", (DAY,))
        with self.assertRaisesRegex(InputError, 'invalid JSON'):
            self.store.ensure('2026-10-13')

    def test_crash_after_committed_review_does_not_request_it_again(self):
        original = self.store.update
        def update(day, **values):
            original(day, **values)
            if values.get('phase') == 'ready':
                raise KeyboardInterrupt()
        self.store.update = update
        with self.assertRaises(KeyboardInterrupt):
            generate(self.store, DAY, Model())
        self.store.update = original
        model = Model()
        self.assertEqual('ready', generate(self.store, DAY, model)['phase'])
        self.assertEqual([], model.calls)

    def test_crash_after_draft_checkpoint_resumes_checks_without_redrafting(self):
        original = self.store.update
        def update(day, **values):
            original(day, **values)
            if values.get('phase') == 'checking':
                raise KeyboardInterrupt()
        self.store.update = update
        with self.assertRaises(KeyboardInterrupt):
            generate(self.store, DAY, Model())
        self.store.update = original
        model = Model()
        record = generate(self.store, DAY, model)
        self.assertEqual(['review'], [call[0] for call in model.calls])
        self.assertEqual(1, record['attempt_count'])


class MigrationTests(unittest.TestCase):
    def test_legacy_ready_migrates_without_llm_and_readonly_does_not_migrate(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'legacy.sqlite3'
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE lessons(date TEXT PRIMARY KEY, assignment TEXT NOT NULL, lesson TEXT, status TEXT DEFAULT 'draft', phase TEXT DEFAULT 'generating', attempt_count INTEGER DEFAULT 0, review_result TEXT, failure_reasons TEXT DEFAULT '[]', first_published_at TEXT)")
            content = lesson()
            conn.execute('INSERT INTO lessons(date, assignment, lesson, status, phase, attempt_count, review_result) VALUES (?, ?, ?, ?, ?, ?, ?)',
                         (DAY, dumps(assignment(DAY)), dumps(content), 'ready', 'ready', 2, dumps(review(content))))
            conn.commit()
            conn.close()
            original = path.read_bytes()
            reader = Store(path, readonly=True)
            self.assertIsNone(reader.get(DAY)['binding'])
            reader.close()
            self.assertEqual(original, path.read_bytes())
            store = Store(path, create=False)
            try:
                model = Model()
                migrated = generate(store, DAY, model)
                self.assertEqual([], model.calls)
                self.assertEqual(content, migrated['lesson'])
                self.assertEqual(2, migrated['attempt_count'])
                self.assertEqual(1, migrated['context_reconstructed'])
                self.assertEqual([], publication_checks(store, migrated))
                self.assertTrue(store.get(DAY)['binding'])
                store.update(DAY, binding=None, review_result={'questions': []})
                self.assertTrue(publication_checks(store, store.get(DAY)))
            finally:
                store.close()
