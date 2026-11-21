import json
from typing import Any

from app.kubernetes.kubectl_executor import KubectlExecutor


class DeploymentInspector:
    FAILURE_REASONS = {"ProgressDeadlineExceeded", "ReplicaFailure"}

    def __init__(self, executor: KubectlExecutor) -> None:
        self.executor = executor

    def inspect(self) -> dict[str, Any]:
        result = self.executor.execute(
            ["get", "deployments", "-A", "-o", "json"]
        )
        if not result.success:
            return {
                "healthy": False,
                "deployments": [],
                "error": result.stderr.strip() or "Unable to retrieve deployments.",
            }
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return {
                "healthy": False,
                "deployments": [],
                "error": "kubectl returned invalid deployment JSON.",
            }
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            return {
                "healthy": False,
                "deployments": [],
                "error": "kubectl returned an invalid deployment list.",
            }

        deployments = []
        for deployment in payload.get("items", []):
            metadata = deployment.get("metadata") or {}
            spec = deployment.get("spec") or {}
            status = deployment.get("status") or {}
            desired = spec.get("replicas", 1)
            available = status.get("availableReplicas", 0)
            unavailable = status.get(
                "unavailableReplicas", max(desired - available, 0)
            )
            conditions = status.get("conditions") or []
            failures = [
                {
                    "type": condition.get("type"),
                    "reason": condition.get("reason", ""),
                    "message": (condition.get("message") or "")[:500],
                }
                for condition in conditions
                if condition.get("status") == "False"
                and (
                    condition.get("reason") in self.FAILURE_REASONS
                    or condition.get("type") in {"Available", "Progressing"}
                )
            ]
            deployments.append(
                {
                    "name": metadata.get("name", "unknown"),
                    "namespace": metadata.get("namespace", "default"),
                    "desired_replicas": desired,
                    "available_replicas": available,
                    "unavailable_replicas": unavailable,
                    "healthy": available >= desired and unavailable == 0 and not failures,
                    "failures": failures,
                }
            )
        return {
            "healthy": all(deployment["healthy"] for deployment in deployments),
            "deployments": deployments,
        }
