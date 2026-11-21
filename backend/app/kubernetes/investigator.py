from collections.abc import Iterator
from typing import Any

from app.kubernetes.deployment_inspector import DeploymentInspector
from app.kubernetes.events_analyzer import EventsAnalyzer
from app.kubernetes.kubectl_executor import KubectlExecutor
from app.kubernetes.logs_collector import LogsCollector
from app.kubernetes.network_inspector import NetworkInspector
from app.kubernetes.pod_inspector import PodInspector


class InvestigationService:
    def __init__(
        self,
        executor: KubectlExecutor | None = None,
        context_name: str | None = None,
    ) -> None:
        self.executor = executor or KubectlExecutor(context_name=context_name)
        self.pod_inspector = PodInspector(self.executor)
        self.logs_collector = LogsCollector(self.executor)
        self.events_analyzer = EventsAnalyzer(self.executor)
        self.deployment_inspector = DeploymentInspector(self.executor)
        self.network_inspector = NetworkInspector(self.executor)

    def investigate(self) -> dict[str, Any]:
        investigation = {}
        for event in self.iter_investigate():
            if event["type"] == "investigation":
                investigation = event["investigation"]
        return investigation

    def iter_investigate(self) -> Iterator[dict[str, Any]]:
        yield _progress(1, "Checking Pods", "running")
        pods = self.pod_inspector.inspect()
        yield _progress(1, "Checking Pods", "complete")

        yield _progress(2, "Reading Logs", "running")
        logs = self.logs_collector.collect(pods["problematic_pods"])
        yield _progress(2, "Reading Logs", "complete")

        yield _progress(3, "Analyzing Events", "running")
        events = self.events_analyzer.analyze()
        yield _progress(3, "Analyzing Events", "complete")

        yield _progress(4, "Inspecting Deployments", "running")
        deployments = self.deployment_inspector.inspect()
        yield _progress(4, "Inspecting Deployments", "complete")

        yield _progress(5, "Checking Networking", "running")
        network = self.network_inspector.inspect(pods, logs, events)
        yield _progress(5, "Checking Networking", "complete")
        yield {
            "type": "investigation",
            "investigation": {
                "pods": pods,
                "logs": logs,
                "events": events,
                "deployments": deployments,
                "network": network,
            },
        }


def _progress(sequence: int, step: str, status: str) -> dict[str, Any]:
    return {
        "type": "progress",
        "sequence": sequence,
        "step": step,
        "status": status,
        "message": f"{step} {status}.",
    }
