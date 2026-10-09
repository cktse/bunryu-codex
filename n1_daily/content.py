"""Mechanical checks and the structured content-review contract."""

from datetime import date
import re
import unicodedata

THEMES = ("仕事", "社会", "文化", "科学", "環境", "旅行", "趣味")
FIXED = ("kanji_reading", "contextual_vocabulary", "grammar_selection")
ROTATION = ("paraphrase", "usage", "sentence_ordering", "reading_comprehension")
SUBJECTS = {"kanji_reading": "kanji", "contextual_vocabulary": "vocabulary",
            "grammar_selection": "grammar", "paraphrase": "vocabulary",
            "usage": "vocabulary", "sentence_ordering": "grammar",
            "reading_comprehension": "vocabulary"}
CRITERIA = {
    "unique_answer": "Evaluate all four options. Exactly one fits and matches the key. Any defensible second answer or missing context fails.",
    "explanation_accuracy": "Explain the actual rule or meaning and valid reasons each distractor fails. Contradictions, invented rules, and bare assertions fail.",
    "japanese_quality": "All Japanese is grammatical and idiomatic for its register. Unnatural wording or changed meaning affecting the answer fails.",
    "learning_target": "Test the stated target at N1 level. Reviews use meaningfully different contexts. Trivial clues, repeated scenarios, or a changed target fail.",
    "format_correctness": "Verify contextual kanji readings, natural ordering and starred fragment, and reading answers supported solely by the passage.",
    "ruby_quality": "Readings are correct and useful for N1/N2 readers. Annotate difficult words once per panel, keep okurigana outside ruby, avoid redundant common-word annotations, and include the tested reading in the kanji answer.",
    "supplement_accuracy": "English and Cantonese support the Japanese accurately. Preserve meaning, grammar, and pronunciation distinctions in character comparisons.",
}


def sample_assignment(day, publication_count=0):
    formats = FIXED + tuple(ROTATION[(publication_count + i) % 4] for i in range(3))
    return {"date": day, "theme": THEMES[publication_count % len(THEMES)],
            "slots": [{"type": fmt, "subject": SUBJECTS[fmt], "review_of": None} for fmt in formats]}


def wording(question):
    # Options and ordering fragments are part of the exercise; the complete
    # answer and reconstructed sentence never participate in visible wording.
    parts = [question.get(name, "") for name in ("prompt_ja", "passage_ja", "prefix_ja", "suffix_ja")]
    options = question.get("options", {})
    parts += [options.get(o, "") for o in "ABCD"] if isinstance(options, dict) else []
    return " ".join(" ".join(str(p) for p in parts).split())


def plain(value):
    return (isinstance(value, str) and bool(value.strip())
            and value == unicodedata.normalize("NFC", value)
            and not re.search(r"<[/!]?[A-Za-z][^>]*>", value))


def check_lesson(lesson, assignment, history):
    errors = []
    if not isinstance(lesson, dict):
        return ["lesson: expected an object"]
    for field in ("lesson_id", "date", "theme"):
        if not plain(lesson.get(field)):
            errors.append(f"lesson.{field}: required normalized plain text")
    try:
        date.fromisoformat(lesson.get("date", ""))
    except (ValueError, TypeError):
        errors.append("lesson.date: expected YYYY-MM-DD")
    if lesson.get("date") != assignment["date"] or lesson.get("lesson_id") != f'n1-{assignment["date"]}':
        errors.append("lesson.date/lesson_id: must match assignment")
    if lesson.get("theme") != assignment["theme"]:
        errors.append("lesson.theme: must match assignment")
    minutes = lesson.get("estimated_minutes")
    if type(minutes) is not int or not 10 <= minutes <= 15:
        errors.append("lesson.estimated_minutes: must be an integer from 10 to 15")
    questions = lesson.get("questions")
    if not isinstance(questions, list) or len(questions) != 6:
        return errors + ["lesson.questions: expected six questions"]
    history = [old for old in history if isinstance(old, dict) and isinstance(old.get("questions"), list)]
    past_questions = [q for old in history for q in old["questions"] if isinstance(q, dict)]
    published = [old for old in history if old.get("first_published_at")]
    originals = {(old["lesson_id"], q["id"]): q for old in published for q in old["questions"]}
    targets = {(q["subject"], q["target"]) for q in past_questions
               if isinstance(q.get("subject"), str) and isinstance(q.get("target"), str)}
    seen_wording = {wording(q) for q in past_questions}
    seen_targets = set()
    reviews = 0
    for i, q in enumerate(questions):
        label = f"q{i + 1}"
        slot = assignment["slots"][i]
        if not isinstance(q, dict):
            errors.append(f"{label}: expected an object")
            continue
        if q.get("id") != label:
            errors.append(f"{label}.id: must match display position")
        for field in ("type", "subject"):
            if q.get(field) != slot[field]:
                errors.append(f"{label}.{field}: must match assignment")
        for field in ("target", "prompt_ja", "answer_ja", "explanation_ja"):
            if not plain(q.get(field)):
                errors.append(f"{label}.{field}: required normalized plain text")
        options = q.get("options")
        if not isinstance(options, dict) or set(options) != set("ABCD"):
            errors.append(f"{label}.options: expected A–D")
            options = options if isinstance(options, dict) else {}
        values = list(options.values())
        if any(not plain(v) for v in values) or len({" ".join(str(v).split()) for v in values}) != 4:
            errors.append(f"{label}.options: four distinct nonempty plain-text options required")
        key = q.get("correct_option_id")
        if key not in tuple("ABCD"):
            errors.append(f"{label}.correct_option_id: expected A–D")
        distractors = q.get("distractors_ja")
        if (not isinstance(distractors, dict) or set(distractors) != set("ABCD") - {str(key)}
                or any(not plain(v) for v in distractors.values())):
            errors.append(f"{label}.distractors_ja: exactly three incorrect-option explanations required")
        for field in ("supplement_en", "supplement_yue"):
            if q.get(field) and not plain(q[field]):
                errors.append(f"{label}.{field}: expected normalized plain text")
        ref = q.get("review_of")
        if ref != slot.get("review_of"):
            errors.append(f"{label}.review_of: must match assigned review slot")
        pair = (str(q.get("subject")), str(q.get("target")))
        if ref:
            reviews += 1
            valid_ref = isinstance(ref, dict) and all(isinstance(ref.get(k), str) for k in ("lesson_id", "question_id"))
            origin = originals.get((ref["lesson_id"], ref["question_id"])) if valid_ref else None
            if not origin:
                errors.append(f"{label}.review_of: must reference an existing published question")
            elif pair != (origin["subject"], origin["target"]):
                errors.append(f"{label}.target: review must preserve original subject/target")
        elif pair in targets or pair in seen_targets:
            errors.append(f"{label}.target: new subject/target already taught or duplicated")
        seen_targets.add(pair)
        if q.get("type") == "reading_comprehension" and not plain(q.get("passage_ja")):
            errors.append(f"{label}.passage_ja: required for reading comprehension")
        if q.get("type") == "sentence_ordering":
            order = q.get("correct_order")
            star = q.get("starred_position")
            valid_order = isinstance(order, list) and len(order) == 4 and sorted(map(str, order)) == list("ABCD")
            if not valid_order:
                errors.append(f"{label}.correct_order: expected a permutation of A–D")
            if type(star) is not int or not 1 <= star <= 4 or (valid_order and order[star - 1] != key):
                errors.append(f"{label}.starred_position: must be 1–4 and match the answer key")
            if any(not isinstance(q.get(f), str) for f in ("prefix_ja", "suffix_ja")):
                errors.append(f"{label}.prefix_ja/suffix_ja: required strings")
            if not plain(q.get("reconstructed_sentence_ja")):
                errors.append(f"{label}.reconstructed_sentence_ja: required")
            elif valid_order and set(options) == set("ABCD"):
                reconstructed = str(q.get("prefix_ja", "")) + "".join(str(options[o]) for o in order) + str(q.get("suffix_ja", ""))
                if reconstructed != q["reconstructed_sentence_ja"]:
                    errors.append(f"{label}.reconstructed_sentence_ja: does not match fragments")
        annotations = q.get("ruby")
        if not isinstance(annotations, list):
            errors.append(f"{label}.ruby: expected an annotation list")
            annotations = []
        occupied = {}
        for annotation in annotations:
            if not isinstance(annotation, dict):
                errors.append(f"{label}.ruby: expected an object")
                continue
            field = annotation.get("field")
            text = answer_field(q, field)
            start, end = annotation.get("start"), annotation.get("end")
            if (text is None or type(start) is not int or type(end) is not int
                    or not 0 <= start < end <= len(text) or text[start:end] != annotation.get("text")):
                errors.append(f"{label}.ruby: invalid field, bounds, or selected text")
                continue
            reading = annotation.get("reading")
            if not isinstance(reading, str) or not re.fullmatch(r"[ぁ-ゖー]+", reading) or not re.search(r"[ぁ-ゖ]", reading):
                errors.append(f"{label}.ruby: reading must be hiragana")
            ranges = occupied.setdefault(field, [])
            if any(start < other_end and end > other_start for other_start, other_end in ranges):
                errors.append(f"{label}.ruby: overlapping annotations")
            ranges.append((start, end))
        if set(options) == set("ABCD"):
            fingerprint = wording({**q, "options": options})
            if fingerprint in seen_wording:
                errors.append(f"{label}.prompt_ja: duplicate wording")
            seen_wording.add(fingerprint)
    if reviews > 2:
        errors.append("lesson.questions: at most two review slots")
    if not {"kanji", "vocabulary", "grammar"} <= {str(q.get("subject")) for q in questions if isinstance(q, dict)}:
        errors.append("lesson.questions: kanji, vocabulary, and grammar required")
    return errors


def answer_field(q, field):
    if field in ("answer_ja", "explanation_ja", "reconstructed_sentence_ja"):
        value = q.get(field)
    elif isinstance(field, str) and field.startswith("distractors_ja."):
        distractors = q.get("distractors_ja", {})
        value = distractors.get(field.split(".")[1]) if isinstance(distractors, dict) else None
    else:
        return None
    return value if isinstance(value, str) else None


def check_review(lesson, review):
    if not isinstance(review, dict) or not isinstance(review.get("questions"), list):
        return ["review.questions: structured per-question review required"]
    entries = review["questions"]
    if len(entries) != 6 or any(not isinstance(e, dict) for e in entries):
        return ["review.questions: expected six question objects"]
    if [e.get("id") for e in entries] != [q["id"] for q in lesson["questions"]]:
        return ["review.questions: IDs must match q1–q6 in order"]
    errors = []
    for q, entry in zip(lesson["questions"], entries):
        label = q["id"]
        verdicts = entry.get("options")
        if (not isinstance(verdicts, dict) or set(verdicts) != set("ABCD") or any(
                not isinstance(v, dict) or type(v.get("fits")) is not bool or not plain(v.get("reason"))
                for v in verdicts.values())):
            errors.append(f"{label}.options: all four reasoned boolean verdicts required")
        elif [o for o, v in verdicts.items() if v["fits"]] != [q["correct_option_id"]]:
            reasons = "; ".join(f'{o}: {v["reason"]}' for o, v in verdicts.items() if v["fits"])
            errors.append(f"{label}.options: exactly one fitting option must match the key; {reasons}")
        criteria = entry.get("criteria")
        if not isinstance(criteria, dict) or set(criteria) != set(CRITERIA):
            errors.append(f"{label}.criteria: all seven content criteria required")
            continue
        for name, result in criteria.items():
            if not isinstance(result, dict) or not plain(result.get("reason")):
                errors.append(f"{label}.{name}: reasoned result required")
                continue
            status = result.get("status")
            absent_supplement = name == "supplement_accuracy" and not q.get("supplement_en") and not q.get("supplement_yue")
            if status == "inapplicable" and absent_supplement:
                continue
            if status != "pass":
                errors.append(f'{label}.{name}: {result["reason"]}; offending text: {result.get("offending_text", "")}; change: {result.get("change", "")}')
    return errors
