"""Offline renderers; no service calls or delivery side effects."""

from email.message import EmailMessage
from email.policy import SMTP
from html import escape
import re

from .content import answer_field


def md_escape(text):
    text = escape(text, quote=False)
    return re.sub(r"([\\`*_[\]{}#|>!])", r"\\\1", text)


def annotated(q, field, mode="html"):
    text = answer_field(q, field) or ""
    annotations = sorted((a for a in q["ruby"] if a["field"] == field), key=lambda a: a["start"])
    encode = md_escape if mode in ("markdown", "parentheses") else escape
    pieces, position = [], 0
    for a in annotations:
        pieces.append(encode(text[position:a["start"]]))
        selected, reading = escape(a["text"]), escape(a["reading"])
        if mode in ("parentheses", "text"):
            # A kanji reading answer may already supply the reading explicitly.
            pieces.append(f"{selected}（{reading}）" if not text[a["end"]:].startswith(f"（{a['reading']}）") else selected)
        else:
            pieces.append(f"<ruby>{selected}<rt>{reading}</rt></ruby>")
        position = a["end"]
    pieces.append(encode(text[position:]))
    return "".join(pieces)


def answer_lines(q, mode):
    lines = [annotated(q, "answer_ja", mode), annotated(q, "explanation_ja", mode)]
    if q["type"] == "sentence_ordering":
        lines += ["順序: " + " → ".join(q["correct_order"]), annotated(q, "reconstructed_sentence_ja", mode)]
    for option in "ABCD":
        if option in q["distractors_ja"]:
            lines.append(f"{option}: " + annotated(q, f"distractors_ja.{option}", mode))
    encode = md_escape if mode in ("markdown", "parentheses") else escape
    for field, label in (("supplement_en", "English"), ("supplement_yue", "廣東話")):
        if q.get(field):
            lines.append(f"{label}: " + encode(q[field]))
    return lines


def exercise_lines(q):
    lines = [q["prompt_ja"]]
    if q.get("passage_ja"):
        lines.append(q["passage_ja"])
    if q["type"] == "sentence_ordering":
        blanks = ["★" if i == q["starred_position"] else "____" for i in range(1, 5)]
        lines.append(q["prefix_ja"] + "　".join(blanks) + q["suffix_ja"])
    return lines + [f"{o}. {q['options'][o]}" for o in "ABCD"]


def title(lesson):
    return f"JLPT N1 オリジナル練習 · {lesson['date']} · {lesson['theme']}"


def render_html(lesson):
    parts = ["<!doctype html><html lang=\"ja\"><head><meta charset=\"utf-8\">",
             '<meta name="viewport" content="width=device-width, initial-scale=1"><style>',
             "body{font-family:-apple-system,sans-serif;line-height:1.8;margin:24px;color:#202a35;background:#fff}"
             "main{max-width:720px;margin:auto}section{padding:16px 0;border-bottom:1px solid #ddd}"
             ".toggle{position:absolute;width:1px;height:1px;opacity:0}"
             ".answer{display:none;padding:16px;background:#f2f5f8;margin-top:12px}"
             "input:checked ~ .answer{display:block}"
             "label{display:inline-block;padding:8px 18px;border:1px solid #345c80;border-radius:6px;color:#234967;cursor:pointer}"
             "input:focus-visible + label{outline:2px solid #345c80}rt{font-size:.65em}p{white-space:pre-wrap}",
             "</style></head><body>",
             '<div style="display:none;max-height:0;overflow:hidden" aria-hidden="true">今日のオリジナル練習。全6問。解答は各問のボタンで確認できます。</div>',
             f"<main><h1>{escape(title(lesson))}</h1><p>目安: {lesson['estimated_minutes']}分 · 全6問</p>"]
    for i, q in enumerate(lesson["questions"], 1):
        parts.append(f"<section><h2>問{i}</h2>")
        parts += [f"<p>{escape(line)}</p>" for line in exercise_lines(q)]
        parts.append(f'<input class="toggle" type="checkbox" id="reveal-{q["id"]}"><label for="reveal-{q["id"]}">解答・解説を表示／非表示</label>')
        parts.append('<div class="answer">')
        parts += [f"<p>{line}</p>" for line in answer_lines(q, "html")]
        parts.append("</div></section>")
    parts.append("</main></body></html>")
    return "\n".join(parts)


def render_markdown(lesson, ruby_mode="html"):
    mode = "parentheses" if ruby_mode == "parentheses" else "markdown"
    lines = [f"# {title(lesson)}", "", f"目安: {lesson['estimated_minutes']}分 · 全6問", "",
             f"[[Notes/{lesson['date']}|自分のメモ]]", ""]
    for i, q in enumerate(lesson["questions"], 1):
        lines += [f"## 問{i}", ""]
        lines += [md_escape(line) + "  " for line in exercise_lines(q)]
        lines += ["", "> [!answer]- 解答・解説"]
        for line in answer_lines(q, mode):
            lines += [">", *["> " + part for part in line.splitlines()]]
        lines.append("")
    return "\n".join(lines)


def render_text(lesson):
    lines = [title(lesson), f"目安: {lesson['estimated_minutes']}分 · 全6問", ""]
    for i, q in enumerate(lesson["questions"], 1):
        lines += [f"{i}.", *exercise_lines(q), ""]
    lines += ["解答・解説", ""]
    for i, q in enumerate(lesson["questions"], 1):
        # The source text is already plain Unicode; plain mail must not contain
        # HTML entities. Only ruby annotations add parentheses.
        from html import unescape
        lines += [f"問{i}", *[unescape(line) for line in answer_lines(q, "text")], ""]
    return "\n".join(lines)


def message_bytes(lesson, sender, recipient):
    msg = EmailMessage(policy=SMTP)
    msg["From"], msg["To"], msg["Subject"] = sender, recipient, title(lesson)
    msg["Message-ID"] = f"<{lesson['lesson_id']}@n1-daily.local>"
    msg.set_content(render_text(lesson))
    msg.add_alternative(render_html(lesson), subtype="html")
    return msg.as_bytes()
