"""Approved section 10: Responses protocol, effort configuration, and recovery."""

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from http.client import IncompleteRead
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from responses_fixtures import completed_response
from test_content import passing_review, sample
from n1_daily.cli import ConfiguredModel, main
from n1_daily.config import Config, ConfigError, SecretSource
from n1_daily.llm import JsonModel
from n1_daily.services import InvalidResponse, ServiceUnavailable
from n1_daily.store import Store
from n1_daily.workflow import generate


class EffortConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.path = self.root / "config.json"
        self.addCleanup(self.tmp.cleanup)

    def config(self, **values):
        self.path.write_text(json.dumps(values))
        return Config.from_file(self.path)

    def test_effort_defaults_and_independent_overrides(self):
        for values, expected in (({}, ("medium", "medium")),
                                 ({"generation_reasoning_effort": "low"}, ("low", "medium")),
                                 ({"review_reasoning_effort": "high"}, ("medium", "high")),
                                 ({"generation_reasoning_effort": "max", "review_reasoning_effort": "xhigh"}, ("max", "xhigh"))):
            with self.subTest(values=values):
                config = self.config(**values)
                self.assertEqual(expected, (config.generation_reasoning_effort, config.review_reasoning_effort))

    def test_every_supported_effort_is_accepted_for_both_phases(self):
        for field in ("generation_reasoning_effort", "review_reasoning_effort"):
            for value in ("low", "medium", "high", "xhigh", "max"):
                with self.subTest(field=field, value=value):
                    self.assertEqual(value, getattr(self.config(**{field: value}), field))

    def test_invalid_effort_is_safe_and_precedes_database_or_credentials(self):
        for field in ("generation_reasoning_effort", "review_reasoning_effort"):
            for value in ("", " ", "HIGH", " high", "high ", "none", "minimal", "DO_NOT_LOG", None, True, 1, [], {}):
                with self.subTest(field=field, value=value):
                    self.path.write_text(json.dumps({"db_path": str(self.root / "unused.sqlite3"), field: value}))
                    with self.assertRaises(ConfigError) as caught:
                        Config.from_file(self.path)
                    self.assertIn(field, str(caught.exception))
                    self.assertNotIn("DO_NOT_LOG", str(caught.exception))
                    stderr = io.StringIO()
                    with redirect_stdout(io.StringIO()), redirect_stderr(stderr), patch("n1_daily.cli.Store") as store, patch("n1_daily.cli.SecretSource") as secrets, patch("n1_daily.services.urlopen") as network:
                        self.assertEqual(2, main(["generate", "--config", str(self.path)]))
                    store.assert_not_called()
                    secrets.assert_not_called()
                    network.assert_not_called()
                    self.assertIn(field, stderr.getvalue())
                    self.assertNotIn("DO_NOT_LOG", stderr.getvalue())

    def test_template_explicitly_records_medium_defaults(self):
        values = json.loads((Path(__file__).parents[1] / "config.example.json").read_text())
        self.assertEqual("medium", values["generation_reasoning_effort"])
        self.assertEqual("medium", values["review_reasoning_effort"])


class ResponsesTests(unittest.TestCase):
    def model(self, **kwargs):
        return JsonModel("https://provider.example/v1/responses", "FIXTURE_KEY", "gen", "review", **kwargs)

    def test_requests_use_responses_with_complete_context_and_medium_defaults(self):
        context = {"assignment": {"date": "2026-10-09"}, "review_originals": ["original"],
                   "previous_seven_published_lessons": [], "all_earlier_subject_target_pairs": [["kanji", "旧語"]]}
        model = self.model(timeout=9)
        with patch("n1_daily.llm.post_json", return_value=completed_response({})) as request:
            model.draft(context, ["q1 failure"], sample())
            model.review(sample(), context)
        self.assertEqual(["gen", "review"], [call.args[1]["model"] for call in request.call_args_list])
        for call in request.call_args_list:
            endpoint, body, headers, timeout = call.args
            self.assertEqual("https://provider.example/v1/responses", endpoint)
            self.assertEqual({"Authorization": "Bearer FIXTURE_KEY"}, headers)
            self.assertEqual(9, timeout)
            self.assertEqual({"format": {"type": "json_object"}}, body["text"])
            self.assertEqual({"effort": "medium"}, body["reasoning"])
            self.assertIs(False, body["store"])
            self.assertEqual({"model", "input", "text", "reasoning", "store"}, set(body))
            self.assertIn("JSON", body["input"][0]["content"])
            self.assertEqual(context, json.loads(body["input"][1]["content"])["context"])
            self.assertNotIn("FIXTURE_KEY", json.dumps(body))
        payload = json.loads(request.call_args_list[0].args[1]["input"][1]["content"])
        self.assertEqual(["q1 failure"], payload["failures"])
        self.assertEqual(sample(), payload["previous_draft"])
        self.assertIn("unique_answer", request.call_args_list[1].args[1]["input"][0]["content"])

    def test_every_effort_reaches_the_correct_request(self):
        for effort in ("low", "medium", "high", "xhigh", "max"):
            with self.subTest(effort=effort), patch("n1_daily.llm.post_json", return_value=completed_response({})) as request:
                model = self.model(generation_reasoning_effort=effort, review_reasoning_effort="high")
                model.draft({}, [], None)
                model.review({}, {})
                self.assertEqual([effort, "high"], [call.args[1]["reasoning"]["effort"] for call in request.call_args_list])

    def test_final_text_can_follow_reasoning_and_span_chunks_and_messages(self):
        response = completed_response({"text": "日本語"})
        response["output"].insert(0, {"type": "reasoning", "summary": [], "encrypted_content": "private"})
        commentary = completed_response({"unwanted": True})["output"][0]
        commentary["phase"] = "commentary"
        response["output"].insert(1, commentary)
        final = response["output"][-1]
        final["phase"] = "final_answer"
        final["content"] = [{"type": "output_text", "text": '{"text":'}, {"type": "output_text", "text": '"日'}]
        response["output"].append({"type": "message", "role": "assistant", "status": "completed",
                                   "content": [{"type": "output_text", "text": '本語"}'}]})
        with patch("n1_daily.llm.post_json", return_value=response):
            self.assertEqual({"text": "日本語"}, self.model().draft({}, [], None))

    def test_malformed_or_missing_output_is_invalid_without_leaking_content(self):
        responses = [None, [], {}, {"status": "completed", "output": None},
                     {"status": "completed", "output": []}, {"status": "completed", "output": [None]},
                     {"choices": [{"message": {"content": "PRIVATE_BODY"}}]}]
        for change in ({"content": None}, {"content": [None]}, {"content": [{"type": "output_text", "text": 42}]},
                       {"content": [{"type": "unknown", "text": "PRIVATE_BODY"}]},
                       {"status": "incomplete"}, {"role": "user"}, {"phase": "commentary"}):
            response = completed_response({})
            response["output"][0].update(change)
            responses.append(response)
        for response in responses:
            with self.subTest(response=response), patch("n1_daily.llm.post_json", return_value=response):
                with self.assertRaises(InvalidResponse) as caught:
                    self.model().draft({}, [], None)
                self.assertNotIn("PRIVATE_BODY", str(caught.exception))

    def test_invalid_or_nonobject_json_is_rejected(self):
        for text in ('```json\n{}\n```', "PRIVATE_BODY", "[]", "null", "true", "42", '"string"'):
            response = completed_response({})
            response["output"][0]["content"][0]["text"] = text
            with self.subTest(text=text), patch("n1_daily.llm.post_json", return_value=response):
                with self.assertRaises(InvalidResponse) as caught:
                    self.model().draft({}, [], None)
                self.assertNotIn("PRIVATE_BODY", str(caught.exception))

    def test_incomplete_or_refused_response_never_accepts_valid_json(self):
        incomplete = completed_response({})
        incomplete.update(status="incomplete", incomplete_details={"reason": "max_output_tokens"})
        refusal = completed_response({})
        refusal["output"][0]["content"].append({"type": "refusal", "refusal": "PRIVATE_BODY"})
        for response in (incomplete, refusal):
            with self.subTest(response=response), patch("n1_daily.llm.post_json", return_value=response):
                with self.assertRaises(InvalidResponse) as caught:
                    self.model().review({}, {})
                self.assertNotIn("PRIVATE_BODY", str(caught.exception))

    def test_provider_service_errors_and_transport_failures_are_safe_and_retryable(self):
        for response in ({"status": "failed", "error": {"message": "PRIVATE_BODY"}},
                         {"status": "completed", "error": {"message": "PRIVATE_BODY"}, "output": []}):
            with self.subTest(response=response), patch("n1_daily.llm.post_json", return_value=response) as request:
                with self.assertRaises(ServiceUnavailable) as caught:
                    self.model().draft({}, [], None)
                self.assertNotIn("PRIVATE_BODY", str(caught.exception))
                self.assertEqual(1, request.call_count)
        for error in (TimeoutError("PRIVATE_BODY"), IncompleteRead(b"PRIVATE_BODY", 20),
                      HTTPError("https://private.example", 400, "PRIVATE_BODY", {}, io.BytesIO(b"PRIVATE_BODY"))):
            with self.subTest(error=error), patch("n1_daily.llm.post_json", side_effect=error) as request:
                with self.assertRaises(ServiceUnavailable) as caught:
                    self.model().draft({}, [], None)
                self.assertNotIn("PRIVATE_BODY", str(caught.exception))
                self.assertEqual(1, request.call_count)

    def test_configured_endpoint_is_used_without_rewriting_or_protocol_fallback(self):
        endpoint = "https://provider.example/v1/chat/completions"
        with patch("n1_daily.llm.post_json", side_effect=HTTPError(endpoint, 400, "rejected", {}, None)) as request:
            with self.assertRaises(ServiceUnavailable):
                JsonModel(endpoint, "fixture", "gen", "review").draft({}, [], None)
        self.assertEqual(1, request.call_count)
        self.assertEqual(endpoint, request.call_args.args[0])
        self.assertIn("input", request.call_args.args[1])
        self.assertNotIn("messages", request.call_args.args[1])


class ResponsesWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.config_path = self.root / "config.json"
        self.secrets_path = self.root / "secrets.json"
        self.secrets_path.write_text('{"llm_api_key":"FIXTURE_KEY"}')
        self.secrets_path.chmod(0o600)
        self.store = Store(self.root / "lessons.sqlite3")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda: self.store.close())

    def model(self, generation="medium", review="medium"):
        self.config_path.write_text(json.dumps({"llm_endpoint": "https://provider.example/v1/responses",
            "generation_model": "gen", "review_model": "review",
            "generation_reasoning_effort": generation, "review_reasoning_effort": review}))
        config = Config.from_file(self.config_path)
        return ConfiguredModel(config, SecretSource(self.secrets_path, config))

    def test_corrected_drafts_and_reviews_use_independent_efforts(self):
        lesson = sample()
        with patch("n1_daily.llm.post_json", side_effect=[completed_response(lesson),
                completed_response({}), completed_response(lesson), completed_response(passing_review(lesson))]) as request:
            record = generate(self.store, "2026-10-09", self.model("low", "high"))
        self.assertEqual("ready", record["status"])
        self.assertEqual(2, record["attempt_count"])
        self.assertEqual(["low", "high", "low", "high"], [call.args[1]["reasoning"]["effort"] for call in request.call_args_list])
        correction = json.loads(request.call_args_list[2].args[1]["input"][1]["content"])
        self.assertTrue(correction["failures"])
        self.assertEqual(record["lesson"]["questions"], correction["previous_draft"]["questions"])

    def test_draft_and_review_outages_resume_with_current_effort(self):
        lesson = sample()
        for phase in ("generating", "reviewing"):
            with self.subTest(phase=phase):
                self.store.conn.execute("DELETE FROM lessons")
                self.store.conn.commit()
                first = [IncompleteRead(b"PRIVATE_BODY", 20)] if phase == "generating" else [completed_response(lesson), IncompleteRead(b"PRIVATE_BODY", 20)]
                with patch("n1_daily.llm.post_json", side_effect=first):
                    record = generate(self.store, "2026-10-09", self.model())
                self.assertEqual(phase, record["phase"])
                self.assertEqual(1, record["attempt_count"])
                self.store.close()
                self.store = Store(self.root / "lessons.sqlite3")
                resumed = [completed_response(lesson), completed_response(passing_review(lesson))] if phase == "generating" else [completed_response(passing_review(lesson))]
                with patch("n1_daily.llm.post_json", side_effect=resumed) as request:
                    ready = generate(self.store, "2026-10-09", self.model("high", "max"))
                self.assertEqual("ready", ready["status"])
                self.assertEqual(1, ready["attempt_count"])
                self.assertEqual(["high", "max"] if phase == "generating" else ["max"], [call.args[1]["reasoning"]["effort"] for call in request.call_args_list])
                self.assertNotIn("PRIVATE_BODY", repr(ready))

    def test_invalid_reviews_exhaust_correction_budget_across_restart(self):
        lesson = sample()
        incomplete = completed_response(passing_review(lesson))
        incomplete["status"] = "incomplete"
        with patch("n1_daily.llm.post_json", side_effect=[completed_response(lesson), incomplete, TimeoutError()]):
            record = generate(self.store, "2026-10-09", self.model("medium", "high"))
        self.assertEqual("generating", record["phase"])
        self.assertEqual(2, record["attempt_count"])
        self.store.close()
        self.store = Store(self.root / "lessons.sqlite3")
        refusal = completed_response({})
        refusal["output"][0]["content"] = [{"type": "refusal", "refusal": "PRIVATE_BODY"}]
        with patch("n1_daily.llm.post_json", side_effect=[completed_response(lesson), refusal]) as request:
            failed = generate(self.store, "2026-10-09", self.model("high", "max"))
            self.assertEqual(failed, generate(self.store, "2026-10-09", self.model()))
        self.assertEqual("failed", failed["phase"])
        self.assertEqual(2, failed["attempt_count"])
        self.assertEqual(2, request.call_count)
        self.assertNotIn("PRIVATE_BODY", repr(failed))

    def test_provider_rejection_has_no_fallback_or_budget_reset(self):
        with patch("n1_daily.llm.post_json", side_effect=HTTPError("https://provider.example", 400, "PRIVATE_BODY", {}, None)) as request:
            record = generate(self.store, "2026-10-09", self.model("high", "max"))
        self.assertEqual(1, request.call_count)
        self.assertEqual("generating", record["phase"])
        self.assertEqual(1, record["attempt_count"])
        self.assertEqual("failed", record["status"])
        self.assertNotIn("PRIVATE_BODY", repr(record))

    def test_ready_lesson_and_preview_need_no_responses_settings_or_credentials(self):
        lesson = sample()
        with patch("n1_daily.llm.post_json", side_effect=[completed_response(lesson), completed_response(passing_review(lesson))]):
            ready = generate(self.store, "2026-10-09", self.model("medium", "high"))
        self.secrets_path.unlink()
        self.config_path.write_text(json.dumps({"db_path": str(self.root / "lessons.sqlite3")}))
        config = Config.from_file(self.config_path)
        with patch("n1_daily.llm.post_json", side_effect=AssertionError("no LLM request")), patch("n1_daily.config.read_object", side_effect=AssertionError("no secret loading")):
            self.assertEqual(ready, generate(self.store, "2026-10-09", ConfiguredModel(config, SecretSource(self.secrets_path, config))))
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), patch("n1_daily.llm.post_json", side_effect=AssertionError("no LLM request")):
            self.assertEqual(0, main(["preview", "--config", str(self.config_path), "--secrets", str(self.secrets_path), "--date", "2026-10-09", "--output", str(self.root / "preview")]))
        self.assertEqual({"lesson.html", "lesson.eml", "lesson.md", "lesson.txt", "lesson.json"}, {p.name for p in (self.root / "preview").iterdir()})

    def test_cli_serializes_responses_requests_through_standard_library_http(self):
        lesson = sample()
        self.model("medium", "high")
        values = json.loads(self.config_path.read_text())
        values["db_path"] = str(self.root / "lessons.sqlite3")
        self.config_path.write_text(json.dumps(values))
        replies = [io.BytesIO(json.dumps(completed_response(value)).encode())
                   for value in (lesson, passing_review(lesson))]
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), patch("n1_daily.cli.datetime") as clock, patch("n1_daily.services.urlopen", side_effect=replies) as network, patch("n1_daily.cli.notification"):
            clock.now.return_value = datetime.fromisoformat("2026-10-09T08:00:00+08:00")
            self.assertEqual(0, main(["generate", "--config", str(self.config_path), "--secrets", str(self.secrets_path)]))
        self.assertEqual(2, network.call_count)
        for call, effort in zip(network.call_args_list, ("medium", "high")):
            request = call.args[0]
            body = json.loads(request.data)
            self.assertEqual("https://provider.example/v1/responses", request.full_url)
            self.assertEqual("POST", request.get_method())
            self.assertEqual("application/json", request.get_header("Content-type"))
            self.assertEqual("Bearer FIXTURE_KEY", request.get_header("Authorization"))
            self.assertEqual({"effort": effort}, body["reasoning"])
            self.assertIs(False, body["store"])
            self.assertEqual(30, call.kwargs["timeout"])
        self.assertEqual("ready", self.store.get("2026-10-09")["status"])
