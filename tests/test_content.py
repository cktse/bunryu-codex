import copy
import json
from pathlib import Path
import unittest

from n1_daily.content import CRITERIA, check_lesson, check_review, sample_assignment
from n1_daily.render import render_html, render_markdown, render_text, message_bytes


def sample():
    return json.loads((Path(__file__).parents[1] / "examples/sample-lesson.json").read_text())


def passing_review(lesson):
    """Synthetic gate fixture, never evidence of linguistic correctness."""
    return {"questions": [
        {"id": q["id"], "options": {
            option: {"fits": option == q["correct_option_id"], "reason": "Fixture context assessment"}
            for option in "ABCD"},
         "criteria": {name: {"status": "pass", "reason": "Fixture assessment"} for name in CRITERIA}}
        for q in lesson["questions"]]}


class ContentTests(unittest.TestCase):
    def setUp(self):
        self.lesson = sample()
        self.assignment = sample_assignment(self.lesson["date"])

    def errors(self):
        return check_lesson(self.lesson, self.assignment, [])

    def test_complete_sample_passes_mechanical_checks(self):
        self.assertEqual([], self.errors())

    def test_missing_option_names_question_and_field(self):
        del self.lesson["questions"][0]["options"]["D"]
        self.assertTrue(any("q1.options" in e for e in self.errors()))

    def test_wrong_distractor_set(self):
        self.lesson["questions"][0]["distractors_ja"]["A"] = "incorrect"
        self.assertTrue(any("q1.distractors_ja" in e for e in self.errors()))

    def test_ruby_bounds_overlap_and_reading(self):
        q = self.lesson["questions"][0]
        for replacement in (
            {"end": 999}, {"start": -1}, {"field": "prompt_ja"},
            {"reading": "シンシャク"}, {"text": "別の語"},
        ):
            with self.subTest(replacement=replacement):
                q["ruby"] = [{**sample()["questions"][0]["ruby"][0], **replacement}]
                self.assertTrue(any("q1.ruby" in e for e in self.errors()))
        q["ruby"] = [copy.deepcopy(sample()["questions"][0]["ruby"][0])] * 2
        self.assertTrue(any("overlap" in e for e in self.errors()))

    def test_duplicate_wording_normalizes_whitespace(self):
        self.lesson["questions"][1]["prompt_ja"] = "  " + self.lesson["questions"][0]["prompt_ja"].replace("\n", "   ")
        self.lesson["questions"][1]["options"] = copy.deepcopy(self.lesson["questions"][0]["options"])
        self.assertTrue(any("duplicate" in e for e in self.errors()))

    def test_different_exercises_can_share_an_instruction(self):
        old = sample()
        for q in self.lesson["questions"]:
            q["target"] += "別項目"
            q["prompt_ja"] += "別場面"
        q = self.lesson["questions"][3]
        q["prompt_ja"] = old["questions"][3]["prompt_ja"]
        q["options"]["D"] += "。"
        self.assertEqual([], check_lesson(self.lesson, self.assignment, [old]))

    def test_invalid_earlier_drafts_do_not_crash_checks(self):
        history = [{"questions": [None, {"prompt_ja": "bad", "options": []}]}]
        self.assertEqual([], check_lesson(self.lesson, self.assignment, history))

    def test_history_blocks_new_targets_and_duplicate_wording(self):
        old = sample()
        old["first_published_at"] = "2026-10-08T08:00:00+08:00"
        self.assertTrue(any("target" in e for e in check_lesson(self.lesson, self.assignment, [old])))

    def test_assignment_and_review_reference_must_match(self):
        self.lesson["questions"][0]["review_of"] = {"lesson_id": "missing", "question_id": "q1"}
        self.assertTrue(any("review_of" in e for e in self.errors()))
        self.lesson["questions"][1]["type"] = "usage"
        self.assertTrue(any("q2.type" in e for e in self.errors()))

    def test_review_preserves_original_target_and_requires_published_history(self):
        old = sample()
        old["first_published_at"] = "2026-10-09T08:00:00+08:00"
        ref = {"lesson_id": old["lesson_id"], "question_id": "q1"}
        self.assignment["slots"][0]["review_of"] = ref
        q = self.lesson["questions"][0]
        q["review_of"] = ref
        q["prompt_ja"] = "別の状況で斟酌の読みを選んでください。"
        # Other new targets also need distinct identities for this test.
        for other in self.lesson["questions"][1:]:
            other["target"] += "別項目"
            other["prompt_ja"] += "別場面"
        self.assertEqual([], check_lesson(self.lesson, self.assignment, [old]))
        q["target"] = "changed"
        self.assertTrue(any("preserve" in e for e in check_lesson(self.lesson, self.assignment, [old])))
        old["first_published_at"] = None
        self.assertTrue(any("published" in e for e in check_lesson(self.lesson, self.assignment, [old])))

    def test_ordering_key_and_reconstruction(self):
        q = self.lesson["questions"][5]
        q["starred_position"] = 2
        self.assertTrue(any("starred_position" in e for e in self.errors()))
        q["correct_order"] = ["A"] * 4
        self.assertTrue(any("correct_order" in e for e in self.errors()))

    def test_reading_requires_passage(self):
        self.assignment["slots"][5]["type"] = "reading_comprehension"
        self.assignment["slots"][5]["subject"] = "vocabulary"
        self.lesson["questions"][5].update(type="reading_comprehension", subject="vocabulary")
        self.assertTrue(any("passage_ja" in e for e in self.errors()))

    def test_incomplete_or_ambiguous_review_fails(self):
        review = passing_review(self.lesson)
        self.assertEqual([], check_review(self.lesson, review))
        review["questions"][0]["options"]["B"]["fits"] = True
        self.assertTrue(any("q1.options" in e for e in check_review(self.lesson, review)))
        del review["questions"][1]["criteria"]["ruby_quality"]
        self.assertTrue(any("q2.criteria" in e for e in check_review(self.lesson, review)))
        self.assertTrue(check_review(self.lesson, {"approved": True}))

    def test_failed_distractor_review_blocks_readiness_with_details(self):
        review = passing_review(self.lesson)
        review["questions"][0]["criteria"]["explanation_accuracy"] = {
            "status": "fail", "reason": "Wrong reading rule", "offending_text": "さく",
            "change": "Explain the correct reading"}
        self.assertTrue(any("Wrong reading rule" in e for e in check_review(self.lesson, review)))

    def test_inapplicable_only_for_absent_supplement(self):
        review = passing_review(self.lesson)
        review["questions"][0]["criteria"]["unique_answer"]["status"] = "inapplicable"
        self.assertTrue(check_review(self.lesson, review))

    def test_malformed_json_shapes_return_errors(self):
        for value in (None, [], {"questions": [None] * 6}, {"questions": "bad"}):
            with self.subTest(value=value):
                self.assertTrue(check_lesson(value, self.assignment, []))
        self.lesson["questions"][0]["options"] = ["A", "B"]
        self.assertTrue(self.errors())


class RendererTests(unittest.TestCase):
    def test_six_independent_offline_controls_and_answer_only_ruby(self):
        html = render_html(sample())
        self.assertEqual(6, html.count('type="checkbox"'))
        self.assertEqual(6, html.count('class="answer"'))
        self.assertIn("input:checked ~ .answer", html)
        for i in range(1, 7):
            self.assertIn(f'for="reveal-q{i}"', html)
        self.assertIn("<ruby>斟酌<rt>しんしゃく</rt></ruby>", html)
        self.assertNotIn("<script", html)
        self.assertNotIn("https://", html)

    def test_markdown_collapsed_callouts_order_and_notes_link(self):
        md = render_markdown(sample())
        self.assertEqual(6, md.count("> [!answer]-"))
        self.assertIn("[[Notes/2026-10-09|", md)
        self.assertIn("A → B → C → D", md)
        self.assertIn("★", md)
        self.assertIn("脆弱（ぜいじゃく）", render_markdown(sample(), ruby_mode="parentheses"))

    def test_plaintext_has_all_questions_before_answers(self):
        body = render_text(sample())
        self.assertLess(body.index("6."), body.index("解答・解説"))

    def test_html_and_markdown_escape_untrusted_content(self):
        lesson = sample()
        lesson["questions"][0]["prompt_ja"] = '<script>alert(1)</script> **x** [[Notes/evil]]'
        self.assertNotIn("<script>", render_html(lesson))
        self.assertNotIn("<script>", render_markdown(lesson))
        self.assertNotIn("[[Notes/evil]]", render_markdown(lesson))

    def test_message_has_plain_and_html_parts_and_no_answer_subject(self):
        from email import policy
        from email.parser import BytesParser
        msg = BytesParser(policy=policy.default).parsebytes(message_bytes(sample(), "from@example.test", "to@example.test"))
        self.assertEqual(["text/plain", "text/html"], [p.get_content_type() for p in msg.iter_parts()])
        self.assertNotIn("斟酌", msg["Subject"])


if __name__ == "__main__":
    unittest.main()
