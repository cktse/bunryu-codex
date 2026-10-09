import copy
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from test_content import passing_review, sample
from n1_daily.store import Store, run_lock
from n1_daily.workflow import generate, deliver, daily
from n1_daily.services import DefiniteFailure, UncertainSend


class FakeModel:
    generation_model = "test-generator"
    review_model = "test-reviewer"

    def __init__(self, invalid=False, reject=False, outage=False):
        self.calls = []
        self.invalid, self.reject, self.outage = invalid, reject, outage

    def draft(self, context, failures, previous):
        self.calls.append(("draft", copy.deepcopy(context), failures))
        if self.outage:
            raise TimeoutError("model unavailable")
        lesson = sample()
        lesson["date"] = context["assignment"]["date"]
        lesson["lesson_id"] = "n1-" + lesson["date"]
        if self.invalid:
            del lesson["questions"][0]["options"]["D"]
        return lesson

    def review(self, lesson, context):
        self.calls.append(("review", copy.deepcopy(context)))
        if self.outage:
            raise TimeoutError("review unavailable")
        result = passing_review(lesson)
        if self.reject:
            result["questions"][0]["options"]["B"]["fits"] = True
        return result


class FakeSender:
    def __init__(self, outcomes=None):
        self.messages = []
        self.outcomes = list(outcomes or ["gmail-1"])

    def send(self, payload):
        self.messages.append(payload)
        result = self.outcomes.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "lessons.sqlite3")
        self.now = datetime(2026, 10, 9, 8, 0, tzinfo=ZoneInfo("Asia/Hong_Kong"))
        self.day = self.now.date().isoformat()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def ready(self):
        model = FakeModel()
        generate(self.store, self.day, model)
        self.assertEqual("ready", self.store.get(self.day)["status"])
        return model

    def publish(self, sender=None, vault=None):
        return deliver(self.store, self.day, sender, vault, "from@example.test", "to@example.test", self.now)

    def test_normal_generation_is_two_calls_and_saves_matching_review(self):
        model = self.ready()
        self.assertEqual(["draft", "review"], [c[0] for c in model.calls])
        record = self.store.get(self.day)
        self.assertIsNotNone(record["review_result"])
        self.assertEqual(1, record["attempt_count"])
        generate(self.store, self.day, model)
        self.assertEqual(2, len(model.calls))

    def test_one_correction_and_persistent_limit(self):
        model = FakeModel(invalid=True)
        generate(self.store, self.day, model)
        record = self.store.get(self.day)
        self.assertEqual("failed", record["status"])
        self.assertEqual(2, record["attempt_count"])
        self.assertEqual(["draft", "draft"], [c[0] for c in model.calls])
        self.assertTrue(model.calls[1][2])
        self.store.close()
        self.store = Store(self.root / "lessons.sqlite3")
        generate(self.store, self.day, model)
        self.assertEqual(2, len(model.calls))
        with self.assertRaises(ValueError):
            self.publish(FakeSender(), self.root / "vault")

    def test_failed_reviews_allow_only_four_calls(self):
        model = FakeModel(reject=True)
        generate(self.store, self.day, model)
        self.assertEqual(["draft", "review", "draft", "review"], [c[0] for c in model.calls])
        self.assertEqual("failed", self.store.get(self.day)["status"])
        self.assertTrue(any("exactly one" in e for e in self.store.get(self.day)["failure_reasons"]))

    def test_corrected_draft_can_pass_without_further_generation(self):
        from unittest.mock import patch
        model = FakeModel()
        bad = sample()
        del bad["questions"][0]["options"]["D"]
        with patch.object(model, "draft", side_effect=[bad, sample()]) as draft:
            generate(self.store, self.day, model)
            self.assertEqual(2, draft.call_count)
            self.assertTrue(any("q1.options" in reason for reason in draft.call_args.args[1]))
        self.assertEqual("ready", self.store.get(self.day)["status"])
        self.assertEqual(2, self.store.get(self.day)["attempt_count"])

    def test_correction_feedback_survives_an_outage_and_restart(self):
        from unittest.mock import patch
        model = FakeModel()
        bad = sample()
        del bad["questions"][0]["options"]["D"]
        with patch.object(model, "draft", side_effect=[bad, TimeoutError("offline")]):
            generate(self.store, self.day, model)
        self.store.close()
        self.store = Store(self.root / "lessons.sqlite3")
        generate(self.store, self.day, model)
        self.assertEqual("ready", self.store.get(self.day)["status"])
        self.assertEqual(2, self.store.get(self.day)["attempt_count"])
        self.assertTrue(any("q1.options" in reason for reason in model.calls[0][2]))

    def test_review_outage_resumes_saved_draft_and_preserves_model_identity(self):
        from unittest.mock import patch
        model = FakeModel()
        with patch.object(model, "review", side_effect=TimeoutError("offline")):
            generate(self.store, self.day, model)
        self.assertEqual("reviewing", self.store.get(self.day)["phase"])
        model.generation_model = "another-generator"
        generate(self.store, self.day, model)
        self.assertEqual(["draft", "review"], [c[0] for c in model.calls])
        self.assertEqual("test-generator", self.store.get(self.day)["lesson"]["generation_model"])
        self.assertEqual(1, self.store.get(self.day)["attempt_count"])

    def test_model_outage_is_retryable_without_consuming_correction(self):
        model = FakeModel(outage=True)
        generate(self.store, self.day, model)
        self.assertEqual("failed", self.store.get(self.day)["status"])
        self.assertEqual(1, self.store.get(self.day)["attempt_count"])
        model.outage = False
        generate(self.store, self.day, model)
        self.assertEqual("ready", self.store.get(self.day)["status"])
        self.assertEqual(1, self.store.get(self.day)["attempt_count"])

    def test_saved_ready_lesson_delivers_without_model(self):
        self.ready()
        sender = FakeSender()
        self.publish(sender, self.root / "vault")
        record = self.store.get(self.day)
        self.assertEqual("sent", record["email_status"])
        self.assertEqual("written", record["markdown_status"])
        self.assertTrue((self.root / "vault/Lessons/2026/10/2026-10-09.md").exists())
        self.assertIsNotNone(record["first_published_at"])
        self.assertEqual(6, len(self.store.review_items()))
        self.publish(sender, self.root / "vault")
        self.assertEqual(1, len(sender.messages))
        self.assertEqual(6, len(self.store.review_items()))

    def test_channel_retry_keeps_same_bytes_and_skips_success(self):
        self.ready()
        sender = FakeSender([DefiniteFailure("rejected"), "gmail-2"])
        vault = self.root / "vault"
        self.publish(sender, vault)
        self.assertEqual("failed", self.store.get(self.day)["email_status"])
        self.assertEqual("written", self.store.get(self.day)["markdown_status"])
        path = vault / "Lessons/2026/10/2026-10-09.md"
        modified = path.stat().st_mtime_ns
        self.publish(sender, vault)
        self.assertEqual(sender.messages[0], sender.messages[1])
        self.assertEqual(modified, path.stat().st_mtime_ns)
        self.assertEqual(6, len(self.store.review_items()))

    def test_both_failed_channels_do_not_count_as_publication(self):
        self.ready()
        vault = self.root / "vault"
        path = vault / "Lessons/2026/10/2026-10-09.md"
        path.parent.mkdir(parents=True)
        path.write_text("User's existing file")
        self.publish(FakeSender([DefiniteFailure("rejected")]), vault)
        self.assertIsNone(self.store.get(self.day)["first_published_at"])
        self.assertEqual([], self.store.review_items())
        self.assertEqual("仕事", self.store.assignment("2026-10-10")["theme"])

    def test_unknown_send_and_crash_never_auto_resend(self):
        self.ready()
        sender = FakeSender([UncertainSend("connection lost")])
        self.publish(sender)
        self.assertEqual("unknown", self.store.get(self.day)["email_status"])
        self.publish(sender)
        self.assertEqual(1, len(sender.messages))
        self.store.update(self.day, email_status="sending")
        self.store.recover_inflight()
        self.assertEqual("unknown", self.store.get(self.day)["email_status"])

    def test_user_edit_and_personal_notes_are_preserved(self):
        self.ready()
        vault = self.root / "vault"
        path = vault / "Lessons/2026/10/2026-10-09.md"
        path.parent.mkdir(parents=True)
        path.write_text("My existing lesson")
        notes = vault / "Notes/2026-10-09.md"
        notes.parent.mkdir()
        notes.write_text("My private thoughts")
        sender = FakeSender()
        self.publish(sender, vault)
        self.assertEqual("My existing lesson", path.read_text())
        self.assertEqual("My private thoughts", notes.read_text())
        self.assertEqual("failed", self.store.get(self.day)["markdown_status"])
        self.assertEqual("sent", self.store.get(self.day)["email_status"])

    def test_index_failure_retries_without_rewriting_lesson(self):
        self.ready()
        vault = self.root / "vault"
        vault.mkdir()
        index = vault / "Index.md"
        index.write_text("My custom index")
        sender = FakeSender()
        self.publish(sender, vault)
        record = self.store.get(self.day)
        self.assertEqual("written", record["markdown_status"])
        self.assertEqual("failed", record["index_status"])
        lesson_path = Path(record["markdown_path"])
        modified = lesson_path.stat().st_mtime_ns
        index.unlink()  # Explicitly resolve the conflict.
        self.publish(sender, vault)
        self.assertEqual("written", self.store.get(self.day)["index_status"])
        self.assertEqual(modified, lesson_path.stat().st_mtime_ns)
        self.assertEqual(1, len(sender.messages))

    def test_rotation_advances_only_once_on_publication(self):
        self.ready()
        self.assertEqual("仕事", self.store.assignment("2026-10-10")["theme"])
        self.publish(FakeSender())
        assignment = self.store.assignment("2026-10-10")
        self.assertEqual("社会", assignment["theme"])
        self.assertEqual("usage", assignment["slots"][3]["type"])
        refs = [s for s in assignment["slots"] if s["review_of"]]
        self.assertEqual(2, len(refs))
        self.assertEqual(["q1", "q2"], [s["review_of"]["question_id"] for s in refs])

    def test_review_delays_start_at_actual_review_publication(self):
        self.ready()
        self.publish(FakeSender())
        assignment = self.store.assignment("2026-10-11")
        lesson = sample()
        lesson.update(date="2026-10-11", lesson_id="n1-2026-10-11", theme=assignment["theme"])
        for q, slot in zip(lesson["questions"], assignment["slots"]):
            q.update(review_of=slot["review_of"])
        self.store.begin("2026-10-11", assignment)
        self.store.update("2026-10-11", lesson=lesson, status="ready", phase="ready")
        now = datetime(2026, 10, 11, 8, tzinfo=ZoneInfo("Asia/Hong_Kong"))
        deliver(self.store, "2026-10-11", FakeSender(), None, "a@example.test", "b@example.test", now)
        item = next(r for r in self.store.review_items() if r["question_id"] == "q1")
        self.assertEqual(1, item["stage"])
        self.assertEqual("2026-10-14", item["due_date"])

    def test_review_sequence_completes_after_four_publications(self):
        self.ready()
        self.publish(FakeSender())
        for stage, day, next_due in ((1, "2026-10-10", "2026-10-13"),
                                     (2, "2026-10-13", "2026-10-20"),
                                     (3, "2026-10-20", "2026-11-03"),
                                     (4, "2026-11-03", None)):
            lesson = sample()
            lesson.update(date=day, lesson_id="n1-" + day)
            for i, q in enumerate(lesson["questions"]):
                if i < 2:
                    q["review_of"] = {"lesson_id": "n1-2026-10-09", "question_id": q["id"]}
                else:
                    q["target"] += day
            self.store.begin(day, self.store.assignment(day))
            self.store.update(day, lesson=lesson, status="ready", phase="ready")
            moment = datetime.fromisoformat(day + "T08:00:00+08:00")
            self.store.channel_success(day, moment, email_status="sent")
            item = next(r for r in self.store.review_items() if r["question_id"] == "q1")
            self.assertEqual(stage, item["stage"])
            self.assertEqual(next_due, item["due_date"])
            self.store.channel_success(day, moment, markdown_status="written")
            item = next(r for r in self.store.review_items() if r["question_id"] == "q1")
            self.assertEqual(stage, item["stage"])

    def test_window_generates_before_eight_but_does_not_publish(self):
        sender = FakeSender()
        daily(self.store, FakeModel(), sender, self.root / "vault", "a@example.test", "b@example.test", self.now.replace(hour=7, minute=45))
        self.assertEqual("ready", self.store.get(self.day)["status"])
        self.assertEqual([], sender.messages)
        daily(self.store, None, sender, self.root / "vault", "a@example.test", "b@example.test", self.now)
        self.assertEqual(1, len(sender.messages))

    def test_late_run_records_missed_day_without_backlog(self):
        model, sender = FakeModel(), FakeSender()
        result = daily(self.store, model, sender, self.root / "vault", "a@example.test", "b@example.test", self.now.replace(hour=12, minute=1))
        self.assertEqual("missed", result)
        self.assertEqual([], model.calls)
        self.assertEqual([], sender.messages)
        self.assertTrue(self.store.get(self.day)["failure_reasons"])

    def test_finished_day_does_not_become_missed_after_noon(self):
        self.ready()
        sender = FakeSender()
        self.publish(sender, self.root / "vault")
        result = daily(self.store, None, sender, self.root / "vault", "a@example.test", "b@example.test", self.now.replace(hour=13))
        self.assertEqual("published", result)
        self.assertEqual(1, len(sender.messages))

    def test_missed_day_is_recorded_once(self):
        late = self.now.replace(hour=13)
        args = (self.store, FakeModel(), FakeSender(), None, "a@example.test", "b@example.test", late)
        self.assertEqual("missed", daily(*args))
        self.assertEqual("closed", daily(*args))
        self.assertEqual(1, len(self.store.get(self.day)["failure_reasons"]))

    def test_generation_crossing_cutoff_preserves_ready_lesson_without_delivery(self):
        sender = FakeSender()
        before = self.now.replace(hour=11, minute=59)
        after = self.now.replace(hour=12, minute=1)
        result = daily(self.store, FakeModel(), sender, self.root / "vault", "a@example.test", "b@example.test", before,
                       current_time=lambda: after)
        self.assertEqual("missed", result)
        self.assertEqual("ready", self.store.get(self.day)["status"])
        self.assertEqual([], sender.messages)

    def test_lock_rejects_overlapping_run(self):
        with run_lock(self.store.path):
            with self.assertRaises(RuntimeError):
                with run_lock(self.store.path):
                    self.fail("concurrent daily run acquired lock")


if __name__ == "__main__":
    unittest.main()
