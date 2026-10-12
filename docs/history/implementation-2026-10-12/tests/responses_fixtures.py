"""Synthetic Responses envelopes for offline protocol tests."""

import json


def completed_response(value):
    return {"status": "completed", "output": [{
        "type": "message", "role": "assistant", "status": "completed",
        "content": [{"type": "output_text", "text": json.dumps(value, ensure_ascii=False)}],
    }]}
