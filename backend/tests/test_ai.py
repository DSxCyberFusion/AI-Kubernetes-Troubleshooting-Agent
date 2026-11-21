import json
import unittest
from types import SimpleNamespace
from unittest.mock import call, patch

import httpx

from app.ai.agent import KubernetesAgent
from app.ai.diagnosis import (
    ConfidenceEngine,
    Diagnosis,
    RootCauseAnalyzer,
)
from app.ai.errors import DiagnosisFormatError, LLMClientError
from app.ai.llm_client import OpenAICompatibleClient
from app.ai.prompt_builder import PromptBuilder


def model_response(
    commands=None,
    confidence=92,
):
    return {
        "root_cause": "The payment container cannot start because DATABASE_URL is missing.",
        "explanation": (
            "The pod is in CrashLoopBackOff and its logs report the missing "
            "DATABASE_URL variable."
        ),
        "fix": "Add DATABASE_URL to the payment deployment using a Kubernetes Secret.",
        "kubectl_commands": commands or [
            "kubectl edit deployment payment -n shop"
        ],
        "prevention_recommendation": (
            "Validate required environment variables before deploying."
        ),
        "confidence": confidence,
        "confidence_reasoning": "Pod status and logs agree.",
    }


class PromptBuilderTests(unittest.TestCase):
    def test_builds_deterministic_structured_prompt_and_redacts_log_secrets(self):
        investigation = {
            "pods": {
                "problematic_pods": [{"name": "payment-0"}],
                "historical_warnings": [
                    {
                        "pod": "provisioner",
                        "namespace": "local-path-storage",
                        "container": "provisioner",
                        "restart_count": 15,
                        "previous_termination": {
                            "reason": "Error",
                            "exit_code": 1,
                            "finished_at": "2026-11-21T09:00:00Z",
                        },
                    }
                ],
            },
            "logs": {
                "api_key": "do-not-send-this",
                "logs": [
                    {
                        "lines": [
                            "DATABASE_PASSWORD=hunter2",
                            "connect postgres://alice:p%40ss@db:5432",
                            "Authorization: Bearer eyJhbGciOiJabcdefghijk",
                        ]
                    }
                ]
            },
            "events": {"findings": []},
            "deployments": {"healthy": False},
            "network": {"issues": []},
        }

        builder = PromptBuilder()
        messages = builder.build_messages(investigation)
        prompt_text = messages[1]["content"]

        self.assertEqual(messages, builder.build_messages(investigation))
        self.assertIn("senior Kubernetes SRE", messages[0]["content"])
        self.assertIn(
            "Treat restart counts and previous container terminations as",
            messages[0]["content"],
        )
        for section in (
            "Pod Status",
            "Logs",
            "Events",
            "Deployment Health",
            "Networking Findings",
        ):
            self.assertIn(section, prompt_text)
        self.assertIn('"restart_count": 15', prompt_text)
        self.assertIn('"reason": "Error"', prompt_text)
        self.assertIn("[REDACTED]", prompt_text)
        self.assertNotIn("hunter2", prompt_text)
        self.assertNotIn("p%40ss", prompt_text)
        self.assertNotIn("eyJhbGciOiJabcdefghijk", prompt_text)
        self.assertNotIn("do-not-send-this", prompt_text)


class OpenAICompatibleClientTests(unittest.TestCase):
    def test_sends_authenticated_json_request_and_extracts_completion(self):
        response = httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"root_cause":"ok"}'}}]},
        )
        client = OpenAICompatibleClient(api_key="test-key", model="test-model")
        with patch("app.ai.llm_client.httpx.post", return_value=response) as post:
            content = client.complete([{"role": "user", "content": "evidence"}])

        self.assertEqual(content, '{"root_cause":"ok"}')
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(post.call_args.kwargs["json"]["model"], "test-model")
        self.assertEqual(
            post.call_args.kwargs["json"]["response_format"],
            {"type": "json_object"},
        )

    def test_supports_the_gemini_openai_compatible_endpoint(self):
        response = httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"root_cause":"ok"}'}}]},
        )
        client = OpenAICompatibleClient(
            api_key="test-gemini-key",
            model="gemini-flash-lite-latest",
            api_url="https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        )
        with patch("app.ai.llm_client.httpx.post", return_value=response) as post:
            result = client.complete([{"role": "user", "content": "evidence"}])

        self.assertEqual(result, '{"root_cause":"ok"}')
        self.assertEqual(
            post.call_args.args[0],
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        )
        self.assertEqual(
            post.call_args.kwargs["headers"]["Authorization"],
            "Bearer test-gemini-key",
        )
        self.assertEqual(post.call_args.kwargs["json"]["model"], "gemini-flash-lite-latest")

    def test_custom_provider_never_falls_back_to_the_openrouter_key(self):
        settings = SimpleNamespace(
            ai_api_url="https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            ai_api_key="",
            ai_model="gemini-flash-lite-latest",
            ai_timeout_seconds=30,
            openrouter_api_key="openrouter-key-must-not-be-sent",
            openrouter_model="old-model",
        )
        with patch("app.ai.llm_client.get_settings", return_value=settings):
            client = OpenAICompatibleClient()

        with patch("app.ai.llm_client.httpx.post") as post:
            with self.assertRaisesRegex(LLMClientError, "configuration is incomplete"):
                client.complete([{"role": "user", "content": "evidence"}])
        post.assert_not_called()
        self.assertEqual(client.api_key, "")
        self.assertEqual(client.model, "gemini-flash-lite-latest")

    def test_retries_transport_and_server_failures(self):
        success = httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"root_cause":"ok"}'}}]},
        )
        client = OpenAICompatibleClient(api_key="test-key", model="test-model")
        with patch(
            "app.ai.llm_client.httpx.post",
            side_effect=[httpx.ConnectError("offline"), httpx.Response(503), success],
        ) as post, patch("app.ai.llm_client.time.sleep") as sleep:
            result = client.complete([])

        self.assertEqual(result, '{"root_cause":"ok"}')
        self.assertEqual(post.call_count, 3)
        self.assertEqual(sleep.call_args_list, [call(1.0), call(2.0)])

    def test_retry_after_is_respected_and_capped(self):
        success = httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"root_cause":"ok"}'}}]},
        )
        client = OpenAICompatibleClient(api_key="test-key", model="test-model")
        with patch(
            "app.ai.llm_client.httpx.post",
            side_effect=[httpx.Response(503, headers={"Retry-After": "30"}), success],
        ), patch("app.ai.llm_client.time.sleep") as sleep:
            client.complete([])

        sleep.assert_called_once_with(8.0)

    def test_missing_credentials_fail_without_requesting_openrouter(self):
        client = OpenAICompatibleClient(api_key="", model="test-model")
        with patch("app.ai.llm_client.httpx.post") as post:
            with self.assertRaises(LLMClientError):
                client.complete([])
        post.assert_not_called()

    def test_non_retryable_provider_error_is_reported(self):
        client = OpenAICompatibleClient(api_key="test-key", model="test-model")
        with patch(
            "app.ai.llm_client.httpx.post",
            return_value=httpx.Response(401),
        ) as post:
            with self.assertRaisesRegex(LLMClientError, "Verify the configured provider API key"):
                client.complete([])
        post.assert_called_once()

    def test_payment_required_error_explains_plan_or_credit_requirement(self):
        client = OpenAICompatibleClient(api_key="test-key", model="test-model")
        with patch(
            "app.ai.llm_client.httpx.post",
            return_value=httpx.Response(402),
        ), self.assertRaisesRegex(LLMClientError, "plan or credit balance"):
            client.complete([])

    def test_non_text_model_response_recommends_a_chat_model(self):
        response = httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": None, "images": [{"url": "https://example.test/image"}]}}
                ]
            },
        )
        client = OpenAICompatibleClient(api_key="test-key", model="test-model")
        with patch("app.ai.llm_client.httpx.post", return_value=response):
            with self.assertRaisesRegex(LLMClientError, "text-generation chat model"):
                client.complete([])


class DiagnosisEngineTests(unittest.TestCase):
    def test_root_cause_analyzer_accepts_a_nested_diagnosis_object(self):
        content = json.dumps({"diagnosis": model_response()})

        class FakeLLM:
            def complete(self, messages):
                return content

        diagnosis = RootCauseAnalyzer(FakeLLM()).analyze({})

        self.assertEqual(diagnosis.root_cause, model_response()["root_cause"])

    def test_root_cause_analyzer_retries_once_with_format_correction(self):
        class InvalidThenValidLLM:
            def __init__(self):
                self.calls = []

            def complete(self, messages):
                self.calls.append(messages)
                if len(self.calls) == 1:
                    return '{"root_cause":"missing required fields"}'
                return json.dumps(model_response())

        llm = InvalidThenValidLLM()
        diagnosis = RootCauseAnalyzer(llm).analyze({})

        self.assertEqual(diagnosis.root_cause, model_response()["root_cause"])
        self.assertEqual(len(llm.calls), 2)
        self.assertIn("did not match the required diagnosis schema", llm.calls[1][-1]["content"])

    def test_agent_correlates_evidence_caps_confidence_and_filters_commands(self):
        content = json.dumps(
            model_response(
                commands=[
                    "kubectl edit deployment payment -n shop",
                    "kubectl delete pod payment-0 -n shop",
                    "kubectl get secrets -n shop",
                    "kubectl get pods -n shop; whoami",
                    "kubectl set env deployment payment DATABASE_PASSWORD=example -n shop",
                ],
                confidence=99,
            )
        )

        class FakeLLM:
            def complete(self, messages):
                self.messages = messages
                return content

        investigation = {
            "pods": {"problematic_pods": [{"name": "payment-0"}]},
            "logs": {
                "logs": [{"relevant_failures_found": True, "lines": ["missing DATABASE_URL"]}],
                "errors": [],
            },
            "events": {"findings": [{"reason": "BackOff"}]},
            "deployments": {"healthy": True, "deployments": []},
            "network": {"issues": [], "dns_issues": []},
        }

        result = KubernetesAgent(FakeLLM()).diagnose(investigation)

        self.assertIn("CrashLoopBackOff", result["explanation"])
        self.assertEqual(
            result["kubectl_commands"],
            ["kubectl edit deployment payment -n shop"],
        )
        self.assertEqual(result["confidence"], 95)
        self.assertIn("pod status", result["confidence_reasoning"])
        self.assertIn("application logs", result["confidence_reasoning"])
        self.assertIn("Kubernetes events", result["confidence_reasoning"])

    def test_root_cause_analyzer_rejects_malformed_model_output(self):
        class InvalidLLM:
            def complete(self, messages):
                return '{"root_cause":"missing required fields"}'

        with self.assertRaises(DiagnosisFormatError):
            RootCauseAnalyzer(InvalidLLM()).analyze({})

    def test_confidence_is_limited_when_evidence_has_no_failure_signals(self):
        diagnosis = Diagnosis.model_validate(model_response(confidence=90))
        adjusted = ConfidenceEngine().apply(diagnosis, {})

        self.assertEqual(adjusted.confidence, 25)
        self.assertIn("No explicit failure indicators", adjusted.confidence_reasoning)


if __name__ == "__main__":
    unittest.main()
