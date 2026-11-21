import json
from typing import Any

from app.kubernetes.kubectl_executor import KubectlExecutor


class EventsAnalyzer:
    RELEVANT_REASONS = {
        "FailedScheduling",
        "BackOff",
        "FailedMount",
        "FailedPull",
        "ErrImagePull",
        "Unhealthy",
    }

    def __init__(self, executor: KubectlExecutor) -> None:
        self.executor = executor

    def analyze(self) -> dict[str, Any]:
        result = self.executor.execute(["get", "events", "-A", "-o", "json"])
        if not result.success:
            return {
                "findings": [],
                "error": result.stderr.strip() or "Unable to retrieve events.",
            }
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"findings": [], "error": "kubectl returned invalid event JSON."}
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            return {"findings": [], "error": "kubectl returned an invalid event list."}

        findings = []
        for event in payload.get("items", []):
            reason = event.get("reason", "")
            if reason not in self.RELEVANT_REASONS:
                continue
            metadata = event.get("metadata") or {}
            involved_object = event.get("involvedObject") or {}
            findings.append(
                {
                    "reason": reason,
                    "message": (event.get("message") or "")[:500],
                    "count": event.get("count", 1),
                    "namespace": involved_object.get(
                        "namespace", metadata.get("namespace", "default")
                    ),
                    "object": involved_object.get("name", "unknown"),
                    "type": event.get("type", "Warning"),
                }
            )
        return {"findings": findings, "healthy": not findings}
