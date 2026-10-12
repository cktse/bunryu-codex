from io import BytesIO
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from helpers import lesson, response
from n1_daily.config import Config
from n1_daily.errors import DeliveryError, ModelOutputError, ServiceError
from n1_daily.http import diagnostic, request_json, sanitize
from n1_daily.llm import Model, parse_response
from n1_daily.gmail import Sender


class Secrets:
    def require(self, *fields):
        return {field: 'PRIVATE_TOKEN' for field in fields}

    def redactions(self):
        return ('PRIVATE_TOKEN',)


class ServicesTests(unittest.TestCase):
    def test_http_error_preserves_provider_details_and_headers_without_credentials(self):
        body = json.dumps({'error': {'message': 'Quota exhausted PRIVATE_TOKEN', 'type': 'quota',
                                    'code': 'insufficient_quota', 'param': 'model'}}).encode()
        error = HTTPError('https://example.com', 429, 'Too many',
                          {'x-request-id': 'req-123', 'Retry-After': '60'}, BytesIO(body))
        with patch('n1_daily.http.urlopen', side_effect=error), self.assertRaises(ServiceError) as caught:
            request_json('LLM draft', 'https://example.com/responses', {}, {}, 30, ('PRIVATE_TOKEN',))
        detail = str(caught.exception)
        for text in ('429', 'Quota exhausted', 'insufficient_quota', 'quota', 'model', 'req-123', '60'):
            self.assertIn(text, detail)
        self.assertNotIn('PRIVATE_TOKEN', detail)

    def test_redaction_truncation_controls_and_unreadable_errors(self):
        text = sanitize('x' * 4090 + 'PRIVATE_TOKEN' + '\x1b[31msecret', ('PRIVATE_TOKEN',))
        self.assertIn('truncated', text)
        self.assertNotIn('PRIV', text)
        text = sanitize('Authorization: Bearer abc123 access_token=hello refresh_token: goodbye')
        for value in ('abc123', 'hello', 'goodbye'):
            self.assertNotIn(value, text)
        error = HTTPError('https://example.com', 500, 'bad', {'x-request-id': 'r'}, None)
        error.read = lambda *args: (_ for _ in ()).throw(OSError('read lost'))
        detail = diagnostic('Gmail send', error)
        self.assertIn('500', detail)
        self.assertIn('r', detail)
        self.assertIn('read', detail.lower())

    def test_gmail_ambiguous_http_and_transport_classification(self):
        for code, unknown in ((400, False), (401, False), (429, False), (408, True), (500, True)):
            with self.subTest(code=code):
                error = HTTPError('https://example.com', code, 'bad', {}, BytesIO(b'{"error":{"message":"detail"}}'))
                with patch('n1_daily.http.urlopen', side_effect=error), self.assertRaises(DeliveryError) as caught:
                    request_json('Gmail send', 'https://example.com', {}, {}, 30, sending=True)
                self.assertEqual(unknown, caught.exception.uncertain)
                self.assertIn('detail', str(caught.exception))
        with patch('n1_daily.http.urlopen', side_effect=URLError('connection lost')), self.assertRaises(DeliveryError) as caught:
            request_json('Gmail send', 'https://example.com', {}, {}, 30, sending=True)
        self.assertTrue(caught.exception.uncertain)

    def test_typed_response_ignores_reasoning_and_requires_completed_final_json(self):
        envelope = response(lesson())
        envelope['output'].insert(0, {'type': 'reasoning', 'summary': [{'text': 'PRIVATE_REASONING'}]})
        envelope['output'].insert(1, {'type': 'message', 'role': 'assistant', 'status': 'completed',
                                     'phase': 'commentary', 'content': [{'type': 'output_text', 'text': 'ignore'}]})
        self.assertEqual(lesson(), parse_response(envelope))
        for malformed in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":1e999}', '```json\n{}\n```', '[]'):
            broken = response(lesson())
            broken['output'][0]['content'][0]['text'] = malformed
            with self.subTest(malformed=malformed), self.assertRaises(ModelOutputError):
                parse_response(broken)
        for status in ('incomplete', 'failed'):
            broken = {'status': status, 'incomplete_details': {'reason': 'max_output_tokens'},
                      'error': {'code': 'server_error', 'message': 'provider execution failed'}}
            if status == 'incomplete':
                broken.pop('error')
            expected = ModelOutputError if status == 'incomplete' else ServiceError
            with self.assertRaises(expected) as caught:
                parse_response(broken)
            self.assertNotIn('PRIVATE_REASONING', str(caught.exception))
        broken = response(lesson())
        broken['output'][0]['content'] = [{'type': 'refusal', 'refusal': 'Cannot supply this lesson'}]
        with self.assertRaisesRegex(ModelOutputError, 'Cannot supply'):
            parse_response(broken)

    def test_requests_use_explicit_responses_contract_and_independent_efforts(self):
        config = Config(llm_endpoint='https://example.com/responses', generation_model='draft', review_model='review',
                        generation_reasoning_effort='high', review_reasoning_effort='low')
        model = Model(config, Secrets())
        with patch('n1_daily.llm.request_json', return_value=response(lesson())) as call:
            model.draft({'assignment': {'date': '2026-10-12'}, 'history': []}, ['repair'], lesson())
            draft = call.call_args.args[2]
            model.review(lesson(), {'assignment': {'date': '2026-10-12'}})
            review = call.call_args.args[2]
        self.assertEqual('high', draft['reasoning']['effort'])
        self.assertEqual('low', review['reasoning']['effort'])
        self.assertEqual({'type': 'json_object'}, draft['text']['format'])
        self.assertIs(False, draft['store'])
        self.assertEqual({'model', 'input', 'text', 'reasoning', 'store'}, set(draft))
        self.assertIn('repair', draft['input'][1]['content'])
        self.assertNotIn('history', json.loads(draft['input'][1]['content'])['context'])

    def test_gmail_oauth_and_acceptance_id(self):
        config = Config(google_client_id='client')
        sender = Sender(config, Secrets())
        with patch('n1_daily.gmail.request_json', side_effect=[{'access_token': 'ACCESS'}, {'id': 'message'}]) as call:
            sender.prepare()
            self.assertEqual('message', sender.send(b'MIME'))
            self.assertTrue(call.call_args.kwargs['sending'])
        with patch('n1_daily.gmail.request_json', return_value={}):
            with self.assertRaises(DeliveryError) as caught:
                sender.send(b'MIME')
        self.assertTrue(caught.exception.uncertain)

    def test_error_body_close_failure_preserves_original_http_error(self):
        error = HTTPError('https://example.com', 429, 'quota', {}, BytesIO(b'{"error":{"message":"quota exhausted"}}'))
        error.close = lambda: (_ for _ in ()).throw(OSError('close failed'))
        result = diagnostic('LLM draft', error)
        self.assertIn('429', result)
        self.assertIn('quota exhausted', result)
