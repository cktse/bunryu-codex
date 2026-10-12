"""Stateless Responses requests; errors retain provider explanations."""
from pathlib import Path

from .content import CRITERIA, canonical
from .errors import ModelOutputError, ServiceError
from .http import details, request_json, sanitize
from .jsonio import dumps, loads

# The approved contract is supplied directly, avoiding a second drifting schema.
SPEC_PATH = Path(__file__).resolve().parent.parent / 'n1-daily-email-design.md'


def parse_response(envelope, secrets=()):
    if not isinstance(envelope, dict):
        raise ModelOutputError('LLM response must be an object')
    status = envelope.get('status')
    if status == 'failed' or envelope.get('error'):
        raise ServiceError('LLM execution failed: ' + (details(envelope, secrets) or 'no provider explanation'))
    if status != 'completed':
        raise ModelOutputError('LLM response not completed: ' + (details(envelope, secrets) or sanitize(status)))
    output = envelope.get('output')
    if not isinstance(output, list):
        raise ModelOutputError('LLM response has no typed output')
    chunks = []
    for item in output:
        if not isinstance(item, dict) or item.get('type') != 'message' or item.get('role') != 'assistant':
            continue
        if item.get('phase') not in (None, 'final_answer') or item.get('status') != 'completed':
            continue
        content = item.get('content')
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get('type') == 'refusal':
                raise ModelOutputError('LLM refusal: ' + sanitize(part.get('refusal', 'No explanation'), secrets))
            if part.get('type') == 'output_text' and isinstance(part.get('text'), str):
                chunks.append(part['text'])
    try:
        result = loads(''.join(chunks))
        if not isinstance(result, dict):
            raise ValueError('not an object')
        return result
    except ValueError as exc:
        raise ModelOutputError('LLM final output must be one unfenced strict JSON object') from exc


def contract():
    spec = SPEC_PATH.read_text(encoding='utf-8')
    return spec.split('## 5. Assignment and content contract\n', 1)[1].split('## 7. Publication', 1)[0]


class Model:
    def __init__(self, config, secrets):
        self.config, self.secrets = config, secrets
        self.generation_model, self.review_model = config.generation_model, config.review_model
        self._prepared_phase = None
        self._key = None

    def prepare(self, phase):
        model_field = 'generation_model' if phase == 'generating' else 'review_model'
        self.config.require('llm_endpoint', model_field)
        self._key = self.secrets.require('llm_api_key')['llm_api_key']
        self._prepared_phase = phase

    def request(self, operation, context, content):
        generation = operation == 'draft'
        phase = 'generating' if generation else 'reviewing'
        if self._prepared_phase != phase:
            self.prepare(phase)
        key = self._key
        self._prepared_phase = None
        system = ('You are a careful JLPT N1 author and reviewer. Produce original N1 practice for 10–15 minutes. '
                  'Write natural Japanese explanations and optional idiomatic English or Cantonese supplements. '
                  'Use traditional Chinese characters for Cantonese. Follow this content contract:\n' + contract())
        if generation:
            system += '\nReturn only a JSON lesson with lesson_id,date,theme,estimated_minutes,questions. No application metadata.'
        else:
            system += ('\nReturn only JSON {"questions":[{"id":"q1","options":{"A":{"fits":true,"reason":"..."},'
                       '"B":{"fits":false,"reason":"..."},"C":{"fits":false,"reason":"..."},'
                       '"D":{"fits":false,"reason":"..."}},"criteria":{...}}]} for all six questions. '
                       'Each criterion has status and reason; failures also have offending_text and change. '
                       'Criterion keys: ' + ', '.join(CRITERIA) + '. Assess all options rigorously; do not rubber-stamp.')
        visible_context = {key: value for key, value in context.items() if key != 'history'}
        payload = {'model': self.generation_model if generation else self.review_model,
                   'input': [{'role': 'system', 'content': system},
                             {'role': 'user', 'content': dumps({'context': visible_context, **content})}],
                   'text': {'format': {'type': 'json_object'}},
                   'reasoning': {'effort': self.config.generation_reasoning_effort if generation else self.config.review_reasoning_effort},
                   'store': False}
        result = request_json('LLM ' + operation, self.config.llm_endpoint, payload,
                              {'Authorization': 'Bearer ' + key}, self.config.timeout_seconds, self.secrets.redactions())
        return parse_response(result, self.secrets.redactions())

    def draft(self, context, feedback, previous):
        return self.request('draft', context, {'correction_feedback': feedback, 'previous_draft': canonical(previous) if isinstance(previous, dict) else None})

    def review(self, lesson, context):
        return self.request('review', context, {'lesson': canonical(lesson)})
