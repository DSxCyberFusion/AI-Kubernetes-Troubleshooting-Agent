import json
import subprocess
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.main import app
from app.ai.errors import LLMClientError
from app.api.routes.investigation import authenticate_request
from app.kubernetes.deployment_inspector import DeploymentInspector
from app.kubernetes.events_analyzer import EventsAnalyzer
from app.kubernetes.kubectl_executor import (
    KubernetesContext,
    KubernetesConfigurationError,
    KubectlExecutor,
    KubectlResult,
)
from app.kubernetes.investigator import InvestigationService
from app.kubernetes.logs_collector import LogsCollector
from app.kubernetes.network_inspector import NetworkInspector
from app.kubernetes.pod_inspector import PodInspector


class FakeExecutor:
    def __init__(self, responses):
        self.responses = responses
        self.commands = []

    def execute(self, args):
        self.commands.append(args)
        return self.responses[tuple(args)]


def success(args, payload):
    return KubectlResult(
        command=["kubectl", *args],
        success=True,
        stdout=json.dumps(payload),
        stderr="",
        returncode=0,
    )


class PodInspectorTests(unittest.TestCase):
    def test_detects_container_failures_and_only_flags_old_container_creating(self):
        old_timestamp = (
            datetime.now(timezone.utc) - timedelta(minutes=10)
        ).isoformat()
        payload = {
            "items": [
                {
                    "metadata": {
                        "name": "crashing",
                        "namespace": "shop",
                        "labels": {"app": "shop"},
                    },
                    "status": {
                        "phase": "Running",
                        "containerStatuses": [
                            {
                                "name": "app",
                                "state": {
                                    "waiting": {"reason": "CrashLoopBackOff"}
                                },
                            }
                        ],
                    },
                },
                {
                    "metadata": {"name": "restarting", "namespace": "shop"},
                    "status": {
                        "phase": "Running",
                        "containerStatuses": [
                            {
                                "name": "app",
                                "ready": False,
                                "restartCount": 2,
                                "state": {
                                    "terminated": {
                                        "reason": "Error",
                                        "exitCode": 1,
                                    }
                                },
                                "lastState": {
                                    "terminated": {
                                        "reason": "Error",
                                        "exitCode": 1,
                                    }
                                },
                            }
                        ],
                    },
                },
                {
                    "metadata": {
                        "name": "stuck",
                        "namespace": "shop",
                        "creationTimestamp": old_timestamp,
                    },
                    "status": {
                        "phase": "Pending",
                        "containerStatuses": [
                            {
                                "name": "app",
                                "state": {
                                    "waiting": {"reason": "ContainerCreating"}
                                },
                            }
                        ],
                    },
                },
                {
                    "metadata": {"name": "not-ready", "namespace": "shop"},
                    "status": {
                        "phase": "Running",
                        "containerStatuses": [
                            {"name": "app", "ready": False, "state": {"running": {}}}
                        ],
                    },
                },
            ]
        }
        executor = FakeExecutor(
            {("get", "pods", "-A", "-o", "json"): success(
                ["get", "pods", "-A", "-o", "json"], payload
            )}
        )

        result = PodInspector(executor).inspect()

        self.assertFalse(result["healthy"])
        self.assertEqual(
            [pod["status"] for pod in result["problematic_pods"]],
            ["CrashLoopBackOff", "CrashLoopBackOff", "ContainerCreating", "NotReady"],
        )
        self.assertEqual(result["pods"][0]["labels"], {"app": "shop"})

    def test_detects_image_pull_backoff_and_oom_killed(self):
        args = ["get", "pods", "-A", "-o", "json"]
        executor = FakeExecutor(
            {
                tuple(args): success(
                    args,
                    {
                        "items": [
                            {
                                "metadata": {"name": "bad-image", "namespace": "shop"},
                                "status": {
                                    "containerStatuses": [
                                        {
                                            "name": "app",
                                            "state": {
                                                "waiting": {"reason": "ImagePullBackOff"}
                                            },
                                        }
                                    ]
                                },
                            },
                            {
                                "metadata": {"name": "out-of-memory", "namespace": "shop"},
                                "status": {
                                    "phase": "Failed",
                                    "containerStatuses": [
                                        {
                                            "name": "app",
                                            "ready": False,
                                            "lastState": {
                                                "terminated": {"reason": "OOMKilled"}
                                            },
                                            "state": {
                                                "terminated": {
                                                    "reason": "OOMKilled",
                                                    "exitCode": 137,
                                                }
                                            },
                                        }
                                    ]
                                },
                            },
                        ]
                    },
                )
            }
        )

        result = PodInspector(executor).inspect()

        self.assertEqual(
            [pod["status"] for pod in result["problematic_pods"]],
            ["ImagePullBackOff", "OOMKilled"],
        )

    def test_running_ready_pod_with_previous_error_is_historical_only(self):
        args = ["get", "pods", "-A", "-o", "json"]
        executor = FakeExecutor(
            {
                tuple(args): success(
                    args,
                    {
                        "items": [
                            {
                                "metadata": {
                                    "name": "recovered",
                                    "namespace": "local-path-storage",
                                },
                                "status": {
                                    "phase": "Running",
                                    "containerStatuses": [
                                        {
                                            "name": "app",
                                            "ready": True,
                                            "restartCount": 15,
                                            "state": {"running": {}},
                                            "lastState": {
                                                "terminated": {
                                                    "reason": "Error",
                                                    "exitCode": 1,
                                                    "finishedAt": "2026-11-21T09:00:00Z",
                                                }
                                            },
                                        }
                                    ],
                                },
                            }
                        ]
                    },
                )
            }
        )

        result = PodInspector(executor).inspect()

        self.assertTrue(result["healthy"])
        self.assertEqual(result["problematic_pods"], [])
        self.assertEqual(result["pods"][0]["current_health"], "healthy")
        self.assertIsNone(result["pods"][0]["current_issue"])
        self.assertEqual(result["historical_warnings"][0]["restart_count"], 15)
        self.assertEqual(
            result["historical_warnings"][0]["previous_termination"]["reason"],
            "Error",
        )

    def test_crashloop_backoff_remains_a_current_problem(self):
        args = ["get", "pods", "-A", "-o", "json"]
        executor = FakeExecutor(
            {
                tuple(args): success(
                    args,
                    {
                        "items": [
                            {
                                "metadata": {"name": "crashing", "namespace": "shop"},
                                "status": {
                                    "phase": "Running",
                                    "containerStatuses": [
                                        {
                                            "name": "app",
                                            "ready": False,
                                            "restartCount": 4,
                                            "state": {
                                                "waiting": {
                                                    "reason": "CrashLoopBackOff"
                                                }
                                            },
                                        }
                                    ],
                                },
                            }
                        ]
                    },
                )
            }
        )

        result = PodInspector(executor).inspect()

        self.assertFalse(result["healthy"])
        self.assertEqual(result["problematic_pods"][0]["status"], "CrashLoopBackOff")
        self.assertEqual(result["pods"][0]["current_health"], "unhealthy")


class NetworkInspectorTests(unittest.TestCase):
    def test_reports_selector_mismatch_missing_endpoints_and_dns_evidence(self):
        service_args = ["get", "services", "-A", "-o", "json"]
        endpoint_args = ["get", "endpoints", "-A", "-o", "json"]
        services = {
            "items": [
                {
                    "metadata": {"name": "shop", "namespace": "default"},
                    "spec": {"selector": {"app": "shop"}},
                },
                {
                    "metadata": {"name": "missing", "namespace": "default"},
                    "spec": {"selector": {"app": "missing"}},
                },
            ]
        }
        endpoints = {
            "items": [
                {
                    "metadata": {"name": "shop", "namespace": "default"},
                    "subsets": [],
                }
            ]
        }
        executor = FakeExecutor(
            {
                tuple(service_args): success(service_args, services),
                tuple(endpoint_args): success(endpoint_args, endpoints),
            }
        )

        result = NetworkInspector(executor).inspect(
            pods={
                "pods": [
                    {
                        "name": "shop-0",
                        "namespace": "default",
                        "labels": {"app": "shop"},
                    }
                ]
            },
            logs={
                "logs": [
                    {
                        "pod": "shop-0",
                        "namespace": "default",
                        "lines": ["lookup db: no such host"],
                    }
                ]
            },
            events={"findings": []},
        )

        self.assertFalse(result["healthy"])
        self.assertEqual(
            [issue["type"] for issue in result["issues"]],
            ["missing_endpoints", "selector_mismatch"],
        )
        self.assertEqual(result["dns_issues"][0]["source"], "log")


class LogsCollectorTests(unittest.TestCase):
    def test_returns_a_bounded_set_of_relevant_lines_per_failed_container(self):
        args = ["logs", "payment-0", "-n", "default", "--tail=100", "-c", "app"]
        output = "\n".join(
            ["startup complete"] + [f"ERROR failure {index}" for index in range(25)]
        )
        executor = FakeExecutor(
            {tuple(args): KubectlResult(["kubectl", *args], True, output, "", 0)}
        )

        result = LogsCollector(executor).collect(
            [
                {
                    "name": "payment-0",
                    "namespace": "default",
                    "containers": ["app"],
                }
            ]
        )

        self.assertTrue(result["logs"][0]["relevant_failures_found"])
        self.assertEqual(len(result["logs"][0]["lines"]), 20)
        self.assertEqual(result["logs"][0]["lines"][0], "ERROR failure 5")


class EventsAnalyzerTests(unittest.TestCase):
    def test_summarizes_only_relevant_warning_events(self):
        args = ["get", "events", "-A", "-o", "json"]
        payload = {
            "items": [
                {
                    "reason": "FailedScheduling",
                    "message": "Insufficient CPU",
                    "count": 3,
                    "involvedObject": {"kind": "Pod", "name": "worker", "namespace": "jobs"},
                    "type": "Warning",
                },
                {"reason": "Pulled", "message": "Image pulled"},
            ]
        }
        executor = FakeExecutor({tuple(args): success(args, payload)})

        result = EventsAnalyzer(executor).analyze()

        self.assertFalse(result["healthy"])
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(result["findings"][0]["count"], 3)
        self.assertEqual(result["findings"][0]["namespace"], "jobs")


class DeploymentInspectorTests(unittest.TestCase):
    def test_reports_replica_shortfall_and_rollout_failure(self):
        args = ["get", "deployments", "-A", "-o", "json"]
        payload = {
            "items": [
                {
                    "metadata": {"name": "payments", "namespace": "default"},
                    "spec": {"replicas": 3},
                    "status": {
                        "availableReplicas": 2,
                        "unavailableReplicas": 1,
                        "conditions": [
                            {
                                "type": "Progressing",
                                "status": "False",
                                "reason": "ProgressDeadlineExceeded",
                                "message": "rollout timed out",
                            }
                        ],
                    },
                }
            ]
        }
        executor = FakeExecutor({tuple(args): success(args, payload)})

        result = DeploymentInspector(executor).inspect()

        self.assertFalse(result["healthy"])
        deployment = result["deployments"][0]
        self.assertEqual(deployment["available_replicas"], 2)
        self.assertEqual(deployment["unavailable_replicas"], 1)
        self.assertEqual(deployment["failures"][0]["reason"], "ProgressDeadlineExceeded")


class KubectlExecutorTests(unittest.TestCase):
    def test_rejects_non_read_only_commands(self):
        with self.assertRaises(ValueError):
            KubectlExecutor().execute(["delete", "pods", "--all"])

    def test_rejects_null_bytes_in_arguments(self):
        with self.assertRaises(ValueError):
            KubectlExecutor().execute(["get", "pods\0"])

    def test_selected_context_is_added_to_kubectl_commands(self):
        config = {
            "current-context": "default",
            "clusters": [{"name": "cluster-a", "cluster": {"server": "https://api.test"}}],
            "contexts": [
                {"name": "default", "context": {"cluster": "cluster-a"}},
                {"name": "selected", "context": {"cluster": "cluster-a", "namespace": "shop"}},
            ],
        }
        with patch(
            "app.kubernetes.kubectl_executor.get_settings",
            return_value=SimpleNamespace(kubeconfig_path="", kubernetes_host_gateway=""),
        ), patch("app.kubernetes.kubectl_executor.subprocess.run") as run:
            run.side_effect = [
                subprocess.CompletedProcess([], 0, json.dumps(config), ""),
                subprocess.CompletedProcess([], 0, "{}", ""),
            ]
            result = KubectlExecutor(context_name="selected").execute(["get", "pods"])

        self.assertTrue(result.success)
        command = run.call_args_list[1].args[0]
        self.assertIn("--context", command)
        self.assertEqual(command[command.index("--context") + 1], "selected")

    def test_loopback_cluster_server_uses_docker_host_gateway(self):
        config = {
            "current-context": "local",
            "clusters": [
                {
                    "name": "kind",
                    "cluster": {"server": "https://127.0.0.1:56981"},
                }
            ],
            "contexts": [{"name": "local", "context": {"cluster": "kind"}}],
        }
        with patch(
            "app.kubernetes.kubectl_executor.get_settings",
            return_value=SimpleNamespace(kubeconfig_path="", kubernetes_host_gateway="host.docker.internal"),
        ), patch("app.kubernetes.kubectl_executor.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, json.dumps(config), "")
            flags = KubectlExecutor(context_name="local")._get_context_flags()

        self.assertEqual(
            flags[flags.index("--server") + 1],
            "https://host.docker.internal:56981",
        )
        self.assertEqual(flags[flags.index("--tls-server-name") + 1], "127.0.0.1")

    def test_rejects_invalid_kubeconfig_context_shapes(self):
        with patch(
            "app.kubernetes.kubectl_executor.get_settings",
            return_value=SimpleNamespace(kubeconfig_path="", kubernetes_host_gateway=""),
        ), patch(
            "app.kubernetes.kubectl_executor.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, '{"contexts": null}', ""),
        ):
            with self.assertRaises(KubernetesConfigurationError):
                KubectlExecutor().list_contexts()


class InvestigationRouteTests(unittest.TestCase):
    def authenticated_post(self, path="/investigate", payload=None):
        app.dependency_overrides[authenticate_request] = lambda: None
        try:
            with patch(
                "app.api.routes.investigation._list_contexts",
                return_value=[
                    KubernetesContext(
                        name="test-context",
                        cluster="test-cluster",
                        namespace="default",
                        current=True,
                        server="https://api.test",
                    )
                ],
            ):
                return TestClient(app).post(
                path,
                headers={"Authorization": "Bearer test-access-token"},
                json=payload or {"context_name": "test-context"},
            )
        finally:
            app.dependency_overrides.pop(authenticate_request, None)

    def test_post_investigate_rejects_missing_authentication(self):
        response = TestClient(app).post("/investigate")

        self.assertEqual(response.status_code, 401)

    def test_invalid_insforge_session_is_rejected(self):
        with patch(
            "app.api.routes.investigation.get_settings",
            return_value=type("Settings", (), {"insforge_url": "https://insforge.test"})(),
        ), patch(
            "app.api.routes.investigation.httpx.get",
            return_value=type("Response", (), {"status_code": 401})(),
        ):
            with self.assertRaises(HTTPException) as raised:
                authenticate_request(
                    HTTPAuthorizationCredentials(
                        scheme="Bearer",
                        credentials="invalid-token",
                    )
                )

        self.assertEqual(raised.exception.status_code, 401)

    def test_post_investigate_returns_the_structured_evidence_payload(self):
        investigation = {
            "pods": {
                "healthy": False,
                "problematic_pods": [{"name": "broken", "namespace": "default"}],
            },
            "logs": {"logs": [], "errors": []},
            "events": {"findings": []},
            "deployments": {"healthy": True, "deployments": []},
            "network": {"healthy": True, "services": [], "issues": []},
        }
        with patch(
            "app.api.routes.investigation.InvestigationService"
        ) as service_class, patch(
            "app.api.routes.investigation.KubernetesAgent"
        ) as agent_class:
            service_class.return_value.investigate.return_value = investigation
            agent_class.return_value.diagnose.return_value = {
                "root_cause": "test cause",
                "confidence": 80,
            }
            response = self.authenticated_post()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "success",
                "context_name": "test-context",
                "investigation": investigation,
                "diagnosis": {"root_cause": "test cause", "confidence": 80},
            },
        )

    def test_openrouter_failure_returns_partial_status_and_preserves_evidence(self):
        investigation = {
            "pods": {
                "healthy": False,
                "problematic_pods": [{"name": "broken", "namespace": "default"}],
            },
            "logs": {"logs": [], "errors": []},
            "events": {"findings": []},
            "deployments": {"healthy": True, "deployments": []},
            "network": {"healthy": True, "services": [], "issues": []},
        }
        with patch(
            "app.api.routes.investigation.InvestigationService"
        ) as service_class, patch(
            "app.api.routes.investigation.KubernetesAgent"
        ) as agent_class:
            service_class.return_value.investigate.return_value = investigation
            agent_class.return_value.diagnose.side_effect = LLMClientError(
                "OpenRouter could not be reached."
            )
            response = self.authenticated_post()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "partial")
        self.assertEqual(response.json()["context_name"], "test-context")
        self.assertEqual(response.json()["investigation"], investigation)
        self.assertIsNone(response.json()["diagnosis"])
        self.assertIn("could not be reached", response.json()["error"])

    def test_unknown_context_is_rejected_before_investigation(self):
        response = self.authenticated_post(payload={"context_name": "not-configured"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("not in the current kubeconfig", response.json()["detail"])

    def test_healthy_cluster_returns_empty_state_without_calling_ai(self):
        investigation = {
            "pods": {"healthy": True, "problematic_pods": []},
            "logs": {"logs": [], "errors": []},
            "events": {"findings": []},
            "deployments": {"healthy": True, "deployments": []},
            "network": {"healthy": True, "services": [], "issues": []},
        }
        with patch(
            "app.api.routes.investigation.InvestigationService"
        ) as service_class, patch(
            "app.api.routes.investigation.KubernetesAgent"
        ) as agent_class:
            service_class.return_value.investigate.return_value = investigation
            response = self.authenticated_post()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["diagnosis"]["root_cause"],
            "No critical Kubernetes issues detected.",
        )
        agent_class.return_value.diagnose.assert_not_called()

    def test_stream_emits_progress_and_result_for_selected_context(self):
        investigation = {
            "pods": {"healthy": True, "problematic_pods": []},
            "logs": {"logs": [], "errors": []},
            "events": {"findings": []},
            "deployments": {"healthy": True, "deployments": []},
            "network": {"healthy": True, "services": [], "issues": []},
        }
        events = [
            {"type": "progress", "sequence": 1, "step": "Checking Pods", "status": "running"},
            {"type": "investigation", "investigation": investigation},
        ]
        app.dependency_overrides[authenticate_request] = lambda: None
        try:
            with patch(
                "app.api.routes.investigation._list_contexts",
                return_value=[
                    KubernetesContext("test-context", "test-cluster", None, True, "https://api.test")
                ],
            ), patch(
                "app.api.routes.investigation.InvestigationService"
            ) as service_class:
                service_class.return_value.iter_investigate.return_value = iter(events)
                response = TestClient(app).post(
                    "/investigate/stream",
                    headers={"Authorization": "Bearer test-access-token"},
                    json={"context_name": "test-context"},
                )
        finally:
            app.dependency_overrides.pop(authenticate_request, None)

        stream_events = [
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(stream_events[0]["step"], "Checking Pods")
        self.assertEqual(stream_events[-1]["type"], "result")
        self.assertEqual(stream_events[-1]["response"]["context_name"], "test-context")


class InvestigationServiceTests(unittest.TestCase):
    def test_runs_evidence_collectors_in_order_and_returns_each_component(self):
        pod_args = ["get", "pods", "-A", "-o", "json"]
        event_args = ["get", "events", "-A", "-o", "json"]
        deployment_args = ["get", "deployments", "-A", "-o", "json"]
        service_args = ["get", "services", "-A", "-o", "json"]
        endpoint_args = ["get", "endpoints", "-A", "-o", "json"]
        empty_list = {"items": []}
        executor = FakeExecutor(
            {
                tuple(pod_args): success(pod_args, empty_list),
                tuple(event_args): success(event_args, empty_list),
                tuple(deployment_args): success(deployment_args, empty_list),
                tuple(service_args): success(service_args, empty_list),
                tuple(endpoint_args): success(endpoint_args, empty_list),
            }
        )

        result = InvestigationService(executor).investigate()

        self.assertEqual(
            executor.commands,
            [pod_args, event_args, deployment_args, service_args, endpoint_args],
        )
        self.assertEqual(
            set(result),
            {"pods", "logs", "events", "deployments", "network"},
        )


if __name__ == "__main__":
    unittest.main()
