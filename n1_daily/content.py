"""Mechanical content checks; linguistic judgments belong to the saved review."""
from datetime import date
import hashlib
import re
import unicodedata

from .jsonio import dumps

THEMES = ('仕事', '社会', '文化', '科学', '環境', '旅行', '趣味')
ROTATING = ('paraphrase', 'usage', 'sentence_ordering', 'reading_comprehension')
SUBJECTS = dict(kanji_reading='kanji', contextual_vocabulary='vocabulary',
                grammar_selection='grammar', paraphrase='vocabulary', usage='vocabulary',
                sentence_ordering='grammar', reading_comprehension='vocabulary')
CRITERIA = ('unique_answer', 'explanation_accuracy', 'japanese_quality', 'learning_target',
            'format_correctness', 'ruby_quality', 'supplement_accuracy')
CORE = ('lesson_id', 'date', 'theme', 'estimated_minutes', 'questions')
LETTERS = set('ABCD')
ORDER_FIELDS = {'prefix_ja', 'suffix_ja', 'starred_position', 'correct_order', 'reconstructed_sentence_ja'}


def normalized(text):
    return ' '.join(unicodedata.normalize('NFC', text).split())


def plain(value, empty=False):
    return (isinstance(value, str) and (empty or bool(value.strip()))
            and unicodedata.normalize('NFC', value) == value
            and not re.search(r'</?[a-zA-Z][^>]*>|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', value))


def valid_date(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def assignment(day, publications=0):
    formats = ['kanji_reading', 'contextual_vocabulary', 'grammar_selection']
    formats += [ROTATING[(publications + i) % 4] for i in range(3)]
    return {'date': day, 'theme': THEMES[publications % len(THEMES)],
            'slots': [{'type': kind, 'subject': SUBJECTS[kind], 'review_of': None} for kind in formats]}


def target(question):
    return question['subject'], normalized(question['target'])


def wording(question):
    fields = [question.get(key, '') for key in ('prompt_ja', 'passage_ja', 'prefix_ja', 'suffix_ja')]
    options = question.get('options', {})
    if isinstance(options, dict):
        fields += [options.get(key, '') for key in 'ABCD']
    return tuple(normalized(value) if isinstance(value, str) else '' for value in fields)


def canonical(lesson):
    return {key: lesson[key] for key in CORE if key in lesson}


def fingerprint(lesson, assigned, context, review):
    return hashlib.sha256(dumps([canonical(lesson), assigned, context, review]).encode()).hexdigest()


def answer_field(question, field):
    if isinstance(field, str) and field.startswith('distractors_ja.'):
        return question.get('distractors_ja', {}).get(field.removeprefix('distractors_ja.'))
    return question.get(field) if isinstance(field, str) else None


def check_ruby(question):
    annotations = question.get('ruby')
    if not isinstance(annotations, list):
        return ['ruby must be a list']
    errors, ranges = [], {}
    allowed = {'answer_ja', 'explanation_ja'}
    distractors = question.get('distractors_ja')
    if isinstance(distractors, dict):
        allowed |= {'distractors_ja.' + key for key in distractors if key in LETTERS}
    if question.get('type') == 'sentence_ordering':
        allowed.add('reconstructed_sentence_ja')
    for annotation in annotations:
        if not isinstance(annotation, dict):
            errors.append('ruby annotation must be an object')
            continue
        field, start, end = (annotation.get(key) for key in ('field', 'start', 'end'))
        if not isinstance(field, str) or field not in allowed:
            errors.append('ruby field is not an allowed answer field')
            continue
        value = answer_field(question, field)
        reading = annotation.get('reading')
        if (not isinstance(value, str) or type(start) is not int or type(end) is not int
                or not 0 <= start < end <= len(value) or value[start:end] != annotation.get('text')):
            errors.append('ruby range/text does not match its field')
            continue
        if (not plain(reading) or not re.fullmatch('[ぁ-ゖー]+', reading)
                or not re.search('[ぁ-ゖ]', reading)):
            errors.append('ruby reading must contain hiragana')
        if any(start < right and end > left for left, right in ranges.get(field, [])):
            errors.append('ruby ranges overlap')
        ranges.setdefault(field, []).append((start, end))
    return errors


def check_lesson(lesson, assigned=None, history=None):
    if not isinstance(lesson, dict):
        return ['lesson must be an object']
    errors = []
    day = lesson.get('date')
    if not valid_date(day):
        errors.append('date must use YYYY-MM-DD')
    if lesson.get('lesson_id') != f'n1-{day}':
        errors.append('lesson_id must match date')
    if lesson.get('theme') not in THEMES:
        errors.append('theme is unsupported')
    minutes = lesson.get('estimated_minutes')
    if type(minutes) is not int or not 10 <= minutes <= 15:
        errors.append('estimated_minutes must be an integer from 10 to 15')
    if assigned and (day != assigned['date'] or lesson.get('theme') != assigned['theme']):
        errors.append('date/theme differ from saved assignment')
    questions = lesson.get('questions')
    if not isinstance(questions, list) or len(questions) != 6:
        return errors + ['questions must contain six objects']
    previous = [(old, q) for old in history or [] for q in old.get('questions', []) if isinstance(q, dict)]
    known_targets = {target(q) for _, q in previous if plain(q.get('subject')) and plain(q.get('target'))}
    known_wording = {wording(q) for _, q in previous}
    seen_targets, seen_wording, seen_refs = set(), set(), set()
    types = [q.get('type') if isinstance(q, dict) else None for q in questions]
    valid_mixes = [tuple(slot['type'] for slot in assignment(day, n)['slots']) for n in range(4)]
    if tuple(types) not in valid_mixes:
        errors.append('questions have an unsupported format mix')
    for index, q in enumerate(questions):
        label = f'q{index + 1}'
        if not isinstance(q, dict):
            errors.append(f'{label}: question must be an object')
            continue
        local = []
        kind = q.get('type')
        if q.get('id') != label:
            local.append('id is out of order')
        if not isinstance(kind, str) or kind not in SUBJECTS or q.get('subject') != SUBJECTS.get(kind):
            local.append('type/subject mismatch')
        if assigned:
            slot = assigned['slots'][index]
            if any(q.get(key) != slot[key] for key in ('type', 'subject', 'review_of')):
                local.append('type/subject/review_of differ from assignment')
        for field in ('target', 'prompt_ja', 'answer_ja', 'explanation_ja'):
            if not plain(q.get(field)):
                local.append(f'{field} must be nonempty NFC plain text')
        for field in ('supplement_en', 'supplement_yue'):
            if field in q and not plain(q[field], empty=True):
                local.append(f'{field} must be NFC plain text')
        options = q.get('options')
        options_valid = (isinstance(options, dict) and set(options) == LETTERS
                         and all(plain(v) for v in options.values()))
        if not options_valid or len({normalized(v) for v in options.values()}) != 4:
            local.append('options must be four distinct NFC plain strings A-D')
        key = q.get('correct_option_id')
        key_valid = isinstance(key, str) and key in LETTERS
        if not key_valid:
            local.append('correct_option_id must be A-D')
        wrong = q.get('distractors_ja')
        if (not isinstance(wrong, dict) or not key_valid or set(wrong) != LETTERS - {key}
                or not all(plain(v) for v in wrong.values())):
            local.append('distractors_ja must explain exactly the three incorrect options')
        ref = q.get('review_of')
        ref_valid = ref is None or (isinstance(ref, dict) and set(ref) == {'lesson_id', 'question_id'}
                                  and all(plain(v) for v in ref.values()))
        if 'review_of' not in q or not ref_valid:
            local.append('review_of must be null or an original lesson/question reference')
        elif ref:
            pair = ref['lesson_id'], ref['question_id']
            if pair in seen_refs:
                local.append('review_of repeats an original item')
            seen_refs.add(pair)
            if history is not None:
                original = next((old_q for old, old_q in previous if old.get('lesson_id') == pair[0]
                                 and old_q.get('id') == pair[1] and old.get('date', '') < str(day)
                                 and old.get('first_published_at')), None)
                if original is None or any(q.get(k) != original.get(k) for k in ('subject', 'target')):
                    local.append('review_of must preserve an earlier published subject/target')
        if plain(q.get('subject')) and plain(q.get('target')):
            pair = target(q)
            if pair in seen_targets or (ref is None and pair in known_targets):
                local.append('target duplicates a local or published subject/target')
            seen_targets.add(pair)
        exercise = wording(q)
        if exercise in seen_wording or exercise in known_wording:
            local.append('wording duplicates a local or published exercise')
        seen_wording.add(exercise)
        if kind == 'reading_comprehension':
            if not plain(q.get('passage_ja')):
                local.append('passage_ja is required for reading')
        elif 'passage_ja' in q:
            local.append('passage_ja is only allowed for reading')
        if kind == 'sentence_ordering':
            for field in ('prefix_ja', 'suffix_ja'):
                if not plain(q.get(field), empty=True):
                    local.append(f'{field} must be NFC plain text')
            order, star = q.get('correct_order'), q.get('starred_position')
            order_valid = (isinstance(order, list) and len(order) == 4
                           and all(isinstance(v, str) for v in order) and set(order) == LETTERS)
            if not order_valid:
                local.append('correct_order must be a permutation of A-D')
            if type(star) is not int or not 1 <= star <= 4 or (order_valid and order[star - 1] != key):
                local.append('starred_position must match correct_option_id')
            if options_valid and order_valid and all(isinstance(q.get(k), str) for k in ('prefix_ja', 'suffix_ja')):
                expected = q['prefix_ja'] + ''.join(options[v] for v in order) + q['suffix_ja']
                if q.get('reconstructed_sentence_ja') != expected:
                    local.append('reconstructed_sentence_ja does not match the ordered fragments')
        elif ORDER_FIELDS & q.keys():
            local.append('ordering fields are only allowed for sentence_ordering')
        local += check_ruby(q)
        errors += [f'{label}: {reason}' for reason in local]
    if len(seen_refs) > 2:
        errors.append('review_of permits at most two review slots')
    return errors


def check_review(lesson, report):
    entries = report.get('questions') if isinstance(report, dict) else None
    if not isinstance(entries, list) or len(entries) != 6:
        return ['review must contain six question assessments']
    errors = []
    for q, entry in zip(lesson['questions'], entries):
        label = q['id']
        if not isinstance(entry, dict) or entry.get('id') != label:
            errors.append(f'{label}: review entry missing or out of order')
            continue
        options = entry.get('options')
        if not isinstance(options, dict) or set(options) != LETTERS:
            errors.append(f'{label}: review must assess all four options')
        else:
            for key, verdict in options.items():
                if (not isinstance(verdict, dict) or type(verdict.get('fits')) is not bool
                        or not plain(verdict.get('reason')) or verdict['fits'] != (key == q['correct_option_id'])):
                    errors.append(f'{label}: option {key} review is incomplete or disputes the unique answer')
        criteria = entry.get('criteria')
        if not isinstance(criteria, dict) or set(criteria) != set(CRITERIA):
            errors.append(f'{label}: review must assess all seven criteria')
            continue
        for name, verdict in criteria.items():
            if not isinstance(verdict, dict) or not plain(verdict.get('reason')):
                errors.append(f'{label}.{name}: missing review reason')
                continue
            status = verdict.get('status')
            if status == 'fail':
                detail = verdict['reason']
                if not all(plain(verdict.get(k)) for k in ('offending_text', 'change')):
                    detail += '; missing offending_text/change'
                else:
                    detail += f"; offending_text: {verdict['offending_text']}; change: {verdict['change']}"
                errors.append(f'{label}.{name}: {detail}')
            elif status == 'inapplicable':
                if name != 'supplement_accuracy' or q.get('supplement_en') or q.get('supplement_yue'):
                    errors.append(f'{label}.{name}: inapplicable is not allowed')
            elif status != 'pass':
                errors.append(f'{label}.{name}: invalid criterion status')
    return errors
