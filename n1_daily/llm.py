"""A configurable JSON chat endpoint with explicit history on every call."""

import json
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError

from .config import valid_endpoint
from .content import CRITERIA
from .services import InvalidResponse, ServiceUnavailable, post_json

REQUIREMENTS = """Write original JLPT N1 practice, never claim these are past exam questions.
Use six questions, four distinct options A-D, exactly one answer, 10-15 minutes,
Japanese explanations of the answer and each distractor, and short optional
English/Cantonese supplements. Follow the assigned types, subjects, theme, and
review references exactly. Move from recognition to contextual distinctions.
For reviews preserve the original subject/target in a meaningfully new context.
New subject/target pairs must differ from every earlier pair supplied.
All text is plain NFC Unicode. Add no reading hints to questions. Add selective
hiragana ruby only to answer_ja, explanation_ja, distractors_ja.A/B/C/D, and
reconstructed_sentence_ja. Ruby uses half-open Unicode code-point start/end
offsets against the final text, selected text, and hiragana reading. Keep
okurigana outside ruby. Annotate useful difficult words once per answer panel.
Reading questions require passage_ja. Ordering questions require prefix_ja,
suffix_ja, starred_position (1-4), correct_order (a permutation of A-D), and
reconstructed_sentence_ja equal to prefix + ordered fragments + suffix; the
correct_option_id identifies the starred fragment. Do not expose correct order
or the reconstructed sentence in prompt_ja. Return one complete JSON object.
"""


class JsonModel:
    def __init__(self, endpoint, api_key, generation_model, review_model, timeout=30):
        if not valid_endpoint(endpoint):
            raise ValueError("llm_endpoint must have an HTTPS host and path without userinfo, query, or fragment")
        if not all((api_key, generation_model, review_model)):
            raise ValueError("Configure LLM API key and both model names")
        self.endpoint, self.api_key = endpoint, api_key
        self.generation_model, self.review_model, self.timeout = generation_model, review_model, timeout

    def _call(self, model, system, payload):
        body = {"model": model, "response_format": {"type": "json_object"}, "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]}
        try:
            response = post_json(self.endpoint, body, {"Authorization": "Bearer " + self.api_key}, self.timeout)
        except HTTPError as error:
            error.close()
            raise ServiceUnavailable(f"LLM request failed: HTTP {error.code}") from error
        except (OSError, HTTPException) as error:
            raise ServiceUnavailable("LLM request timed out or lost its connection") from error
        except ValueError as error:
            raise InvalidResponse("LLM returned an unreadable response") from error
        try:
            return json.loads(response["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise InvalidResponse("LLM must return an unfenced JSON object in choices[0].message.content") from error

    def draft(self, context, failures, previous):
        shape = json.loads((Path(__file__).parents[1] / "examples/sample-lesson.json").read_text())
        # This sample demonstrates the field shape; its old date and content
        # must never override the assignment or be copied verbatim.
        system = REQUIREMENTS + "\nThe sample defines field names only; replace its date, theme, and all question content to match the assignment.\n" + json.dumps(shape, ensure_ascii=False)
        return self._call(self.generation_model, system, {"context": context, "failures": failures, "previous_draft": previous})

    def review(self, lesson, context):
        system = """Review the entire proposed lesson, including the answer key and every
option. This is one informed review, with no claim of blind or independent
solving. Any uncertainty affecting the answer is a failure. Assess all four
options and all seven criteria for each question. For sentence ordering, an
option fits only if it belongs in the starred position. Return exactly:
{"questions":[{"id":"q1","options":{"A":{"fits":true,"reason":"context-specific reason"},
"B":{"fits":false,"reason":"reason"},"C":{"fits":false,"reason":"reason"},
"D":{"fits":false,"reason":"reason"}},"criteria":{"unique_answer":
{"status":"pass|fail|inapplicable","reason":"concrete assessment",
"offending_text":"required for a failure","change":"required for a failure"},
"...":"provide every criterion below"}}]}
Return q1-q6 in order. Inapplicable is allowed only for supplement_accuracy
when both supplements are absent. For every failure identify the offending
text, explain the defect, and describe the needed change. Do not return a bare
approval or score.\n""" + json.dumps(CRITERIA, ensure_ascii=False)
        return self._call(self.review_model, system, {"lesson": lesson, "context": context})
