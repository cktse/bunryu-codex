from copy import deepcopy
from email import policy
from email.parser import BytesParser
import unittest

from helpers import lesson, review
from n1_daily.content import assignment, check_lesson, check_review, fingerprint
from n1_daily.render import email_message, render_html, render_markdown, render_text


class ContentTests(unittest.TestCase):
    def test_sample_and_format_rotation(self):
        content = lesson()
        self.assertEqual([], check_lesson(content, assignment(content["date"]), []))
        self.assertEqual(
            ["usage", "sentence_ordering", "reading_comprehension"],
            [slot["type"] for slot in assignment(content["date"], 1)["slots"][3:]],
        )

    def test_malformed_content_reports_fields_instead_of_crashing(self):
        cases = [
            ("options", lambda q: q["options"].pop("D")),
            ("correct_option_id", lambda q: q.update(correct_option_id=[])),
            ("supplement_en", lambda q: q.update(supplement_en=False)),
            ("passage_ja", lambda q: q.update(passage_ja={"unexpected": "object"})),
            ("ruby", lambda q: q["ruby"][0].update(field="distractors_ja.B.extra")),
            ("ruby", lambda q: q["ruby"][0].update(start=True)),
            ("review_of", lambda q: q.update(review_of={"lesson_id": "", "question_id": ""})),
        ]
        for field, change in cases:
            with self.subTest(field=field):
                content = lesson()
                change(content["questions"][0])
                self.assertTrue(any(field in reason for reason in check_lesson(content)))
        for value in (None, [], {"questions": None}, {"questions": [None] * 6}):
            self.assertTrue(check_lesson(value))

    def test_duplicate_targets_and_visible_wording(self):
        content = lesson()
        content["questions"][3]["target"] = "  脆弱  "
        self.assertTrue(any("target" in error for error in check_lesson(content)))
        old = lesson("2026-10-11")
        old["first_published_at"] = "2026-10-11T08:00:00+08:00"
        self.assertTrue(any("wording" in error for error in check_lesson(lesson(), history=[old])))
        self.assertTrue(any("target" in error for error in check_lesson(lesson(), history=[old])))

    def test_ordering_and_answer_only_ruby(self):
        content = lesson()
        content["questions"][5]["reconstructed_sentence_ja"] += "別の文。"
        self.assertTrue(any("reconstructed_sentence_ja" in error for error in check_lesson(content)))
        content = lesson()
        content["questions"][0]["ruby"][0]["field"] = "prompt_ja"
        self.assertTrue(any("ruby" in error for error in check_lesson(content)))

    def test_reviews_require_all_options_criteria_and_failure_details(self):
        content = lesson()
        good = review(content)
        self.assertEqual([], check_review(content, good))
        for change in (
            lambda r: r["questions"][0]["options"]["B"].update(fits=True),
            lambda r: r["questions"][0]["criteria"].pop("ruby_quality"),
            lambda r: r["questions"][0]["criteria"]["unique_answer"].update(status="fail"),
            lambda r: r["questions"][0]["criteria"]["supplement_accuracy"].update(status="inapplicable"),
        ):
            bad = deepcopy(good)
            change(bad)
            self.assertTrue(check_review(content, bad))
        good["questions"][1]["criteria"]["supplement_accuracy"]["status"] = "inapplicable"
        self.assertTrue(check_review(content, good))  # English is still present.
        content["questions"][1]["supplement_en"] = ""
        self.assertEqual([], check_review(content, good))

    def test_fingerprint_binds_review_and_context_but_not_mutable_status(self):
        content = lesson()
        context = {"history": []}
        verdict = review(content)
        before = fingerprint(content, assignment(content["date"]), context, verdict)
        content["status"] = "sent"
        self.assertEqual(before, fingerprint(content, assignment(content["date"]), context, verdict))
        content["questions"][0]["answer_ja"] += "変更"
        self.assertNotEqual(before, fingerprint(content, assignment(content["date"]), context, verdict))

    def test_renderers_keep_answers_after_controls_and_plain_text_questions(self):
        content = lesson()
        html = render_html(content)
        markdown = render_markdown(content)
        text = render_text(content)
        self.assertEqual(6, html.count('type="checkbox"'))
        self.assertEqual(6, markdown.count("> [!answer]-"))
        self.assertLess(html.index('id="answer-q1"'), html.index("<ruby>"))
        self.assertLess(text.index("問6"), text.index("解答・解説"))
        self.assertIn("正解: A", text)
        self.assertIn("斟酌（しんしゃく）", render_markdown(content, "parentheses"))
        self.assertNotIn("&amp;", render_text(content))
        message = BytesParser(policy=policy.default).parsebytes(
            email_message(content, "a@example.com", "b@example.com")
        )
        self.assertTrue(message["Date"])
        self.assertTrue(message["Message-ID"])
        self.assertEqual(["text/plain", "text/html"], [p.get_content_type() for p in message.iter_parts()])

    def test_literal_markdown_cannot_escape_answer_callout(self):
        content = lesson()
        content["questions"][0]["supplement_en"] = "Literal *text*\n# title\n[[link]]"
        output = render_markdown(content)
        self.assertIn("> \\# title", output)
        self.assertIn("> \\[\\[link\\]\\]", output)
