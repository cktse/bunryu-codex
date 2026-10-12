from copy import deepcopy
import json
from pathlib import Path


CRITERIA = (
    "unique_answer", "explanation_accuracy", "japanese_quality",
    "learning_target", "format_correctness", "ruby_quality", "supplement_accuracy",
)


def lesson(day="2026-10-12"):
    source = json.loads((Path(__file__).parents[1] / "examples/sample-lesson.json").read_text())
    result = {key: source[key] for key in (
        "lesson_id", "date", "theme", "estimated_minutes", "questions",
    )}
    result.update(date=day, lesson_id=f"n1-{day}")
    return deepcopy(result)


def review(content):
    return {"questions": [
        {
            "id": q["id"],
            "options": {
                option: {"fits": option == q["correct_option_id"], "reason": "Context checked."}
                for option in "ABCD"
            },
            "criteria": {
                name: {"status": "pass", "reason": "Criterion checked."}
                for name in CRITERIA
            },
        }
        for q in content["questions"]
    ]}


def response(content):
    return {"status": "completed", "output": [{
        "type": "message", "role": "assistant", "status": "completed",
        "content": [{"type": "output_text", "text": json.dumps(content, ensure_ascii=False)}],
    }]}


class Model:
    generation_model = "draft-model"
    review_model = "review-model"

    def __init__(self, content=None):
        self.content = content or lesson()
        self.calls = []
        self.draft_error = None
        self.review_error = None
        self.verdict = None

    def draft(self, context, feedback, previous):
        self.calls.append(("draft", deepcopy(context), deepcopy(feedback)))
        if self.draft_error:
            raise self.draft_error
        return deepcopy(self.content)

    def review(self, content, context):
        self.calls.append(("review", deepcopy(content)))
        if self.review_error:
            raise self.review_error
        return deepcopy(self.verdict or review(content))


class Sender:
    def __init__(self):
        self.messages = []
        self.prepare_error = None
        self.send_error = None
        self.preparations = 0

    def prepare(self):
        self.preparations += 1
        if self.prepare_error:
            raise self.prepare_error

    def send(self, message):
        self.messages.append(message)
        if self.send_error:
            raise self.send_error
        return "gmail-message"
