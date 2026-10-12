"""Render already-validated content without service or storage access."""
from datetime import datetime
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import format_datetime, make_msgid
from html import escape
import re

from .content import answer_field


def markdown_text(value):
    return re.sub(r'([\\`*_\[\]{}#|>!+().-])', r'\\\1', escape(value, quote=False))


def annotated(q, field, mode):
    value = answer_field(q, field)
    encode = escape if mode == 'html' else markdown_text if mode in ('markdown', 'parentheses') else str
    result, offset = [], 0
    for ruby in sorted((r for r in q['ruby'] if r['field'] == field), key=lambda r: r['start']):
        result.append(encode(value[offset:ruby['start']]))
        text, reading = encode(ruby['text']), encode(ruby['reading'])
        if mode in ('html', 'markdown'):
            result.append(f'<ruby>{text}<rp>（</rp><rt>{reading}</rt><rp>）</rp></ruby>')
        else:
            suffix = '' if value[ruby['end']:].startswith('（' + ruby['reading'] + '）') else f'（{reading}）'
            result.append(text + suffix)
        offset = ruby['end']
    result.append(encode(value[offset:]))
    return ''.join(result)


def answers(q, mode):
    result = [f"正解: {q['correct_option_id']}", annotated(q, 'answer_ja', mode), annotated(q, 'explanation_ja', mode)]
    if q['type'] == 'sentence_ordering':
        result += [' → '.join(q['correct_order']), annotated(q, 'reconstructed_sentence_ja', mode)]
    for key in sorted(q['distractors_ja']):
        result.append(key + ': ' + annotated(q, 'distractors_ja.' + key, mode))
    encode = escape if mode == 'html' else markdown_text if mode in ('markdown', 'parentheses') else str
    for field in ('supplement_en', 'supplement_yue'):
        if q.get(field):
            result.append(encode(q[field]))
    return result


def exercise(q):
    result = [q['prompt_ja']]
    if q['type'] == 'reading_comprehension':
        result.append(q['passage_ja'])
    if q['type'] == 'sentence_ordering':
        blanks = ['★' if i == q['starred_position'] else '＿＿' for i in range(1, 5)]
        result.append(q['prefix_ja'] + ' '.join(blanks) + q['suffix_ja'])
    return result + [f'{key}. {q["options"][key]}' for key in 'ABCD']


def title(lesson):
    return f"JLPT N1 · {lesson['date']} · {lesson['theme']}"


def render_html(lesson):
    parts = ['<!doctype html><html lang="ja"><meta charset="utf-8"><style>'
             'body{font-family:system-ui,sans-serif;line-height:1.8;max-width:48em;margin:auto;padding:1em}'
             '.answer{display:none}input:checked~.answer{display:block}label{cursor:pointer;text-decoration:underline}'
             'section{margin:2em 0}rt{font-size:.6em}</style><body>',
             '<div style="display:none">日本語能力試験N1 六問の練習</div>', f'<h1>{escape(title(lesson))}</h1>']
    for i, q in enumerate(lesson['questions'], 1):
        parts.append(f'<section><h2>問{i}</h2>')
        parts.extend('<p>' + escape(line).replace('\n', '<br>') + '</p>' for line in exercise(q))
        control = 'answer-' + q['id']
        parts.append(f'<input type="checkbox" id="{control}"><label for="{control}">解答・解説</label><div class="answer">')
        parts.extend('<p>' + line.replace('\n', '<br>') + '</p>' for line in answers(q, 'html'))
        parts.append('</div></section>')
    return ''.join(parts) + '</body></html>'


def render_text(lesson):
    parts = [title(lesson)]
    for i, q in enumerate(lesson['questions'], 1):
        parts += [f'問{i}', *exercise(q), '']
    parts.append('解答・解説')
    for i, q in enumerate(lesson['questions'], 1):
        parts += [f'問{i}', *answers(q, 'text'), '']
    return '\n\n'.join(parts) + '\n'


def render_markdown(lesson, ruby_mode='html'):
    parts = ['# ' + markdown_text(title(lesson)), f"[[Notes/{lesson['date']}|学習ノート]]"]
    mode = 'markdown' if ruby_mode == 'html' else 'parentheses'
    for i, q in enumerate(lesson['questions'], 1):
        parts += [f'## 問{i}', *(markdown_text(line) for line in exercise(q))]
        lines = [line for block in answers(q, mode) for line in block.split('\n')]
        parts.append('> [!answer]- 解答・解説\n' + '\n'.join('> ' + line for line in lines))
    return '\n\n'.join(parts) + '\n'


def email_message(lesson, sender, recipient, now=None):
    message = EmailMessage(policy=SMTP)
    message['From'], message['To'], message['Subject'] = sender, recipient, title(lesson)
    message['Date'] = format_datetime(now or datetime.now().astimezone())
    message['Message-ID'] = make_msgid(idstring=lesson['lesson_id'], domain='n1-daily.local')
    message.set_content(render_text(lesson))
    message.add_alternative(render_html(lesson), subtype='html')
    return message.as_bytes()
