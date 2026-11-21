import json
from datetime import datetime, timezone
from typing import Any

from app.kubernetes.kubectl_executor import KubectlExecutor


class PodInspector:
    CONTAINER_CREATING_STUCK_SECONDS = 300

    def __init__(self, executor: KubectlExecutor) -> None:
        self.executor = executor

    def inspect(self) -> dict[str, Any]:
        result = self.executor.execute(["get", "pods", "-A", "-o", "json"])
        if not result.success:
            return {
                "healthy": False,
                "pods": [],
                "problematic_pods": [],
                "historical_warnings": [],
                "error": result.stderr.strip() or "Unable to retrieve pods.",
            }

        try:
            items = _parse_items(result.stdout)
        except ValueError as exc:
            return {
                "healthy": False,
                "pods": [],
                "problematic_pods": [],
                "historical_warnings": [],
                "error": str(exc),
            }

        pods = []
        problematic_pods = []
        historical_warnings = []
        for item in items:
            metadata = item.get("metadata") or {}
            status = item.get("status") or {}
            name = metadata.get("name", "unknown")
            namespace = metadata.get("namespace", "default")
            problem = _pod_problem(item)
            pod = {
                "name": name,
                "namespace": namespace,
                "phase": status.get("phase", "Unknown"),
                "labels": metadata.get("labels") or {},
                "current_health": "unhealthy" if problem else "healthy",
                "current_issue": problem["status"] if problem else None,
            }
            pods.append(pod)
            if problem:
                problematic_pods.append({**pod, **problem})
            historical_warnings.extend(
                _historical_warnings(item, name=name, namespace=namespace)
            )

        return {
            "healthy": not problematic_pods,
            "pods": pods,
            "problematic_pods": problematic_pods,
            "historical_warnings": historical_warnings,
        }


def _pod_problem(pod: dict[str, Any]) -> dict[str, Any] | None:
    status = pod.get("status") or {}
    metadata = pod.get("metadata") or {}
    phase = status.get("phase", "")
    container_statuses = (
        (status.get("containerStatuses") or [])
        + (status.get("initContainerStatuses") or [])
    )
    container_names = []

    for container in container_statuses:
        name = container.get("name")
        if name:
            container_names.append(name)
        state = container.get("state") or {}
        waiting_reason = (state.get("waiting") or {}).get("reason")
        terminated_reason = (state.get("terminated") or {}).get("reason")
        reason = waiting_reason or terminated_reason
        if (
            reason == "Error"
            and container.get("restartCount", 0) > 0
            and container.get("ready") is False
        ):
            return {"status": "CrashLoopBackOff", "containers": [name] if name else []}
        if reason in {
            "CrashLoopBackOff",
            "ImagePullBackOff",
            "ErrImagePull",
            "OOMKilled",
            "Error",
        }:
            return {"status": reason, "containers": [name] if name else []}
        if reason == "ContainerCreating" and _is_stuck(metadata.get("creationTimestamp")):
            return {"status": reason, "containers": [name] if name else []}

    not_ready_containers = [
        container.get("name")
        for container in (status.get("containerStatuses") or [])
        if container.get("ready") is False and container.get("name")
    ]
    if not_ready_containers:
        return {"status": "NotReady", "containers": not_ready_containers}
    if phase == "Pending":
        return {"status": "Pending", "containers": container_names}
    if phase == "Failed":
        return {"status": "Error", "containers": container_names}
    return None


def _historical_warnings(
    pod: dict[str, Any],
    name: str,
    namespace: str,
) -> list[dict[str, Any]]:
    status = pod.get("status") or {}
    container_statuses = (
        (status.get("containerStatuses") or [])
        + (status.get("initContainerStatuses") or [])
    )
    warnings = []
    for container in container_statuses:
        restart_count = container.get("restartCount", 0)
        if not isinstance(restart_count, int) or restart_count <= 0:
            continue

        terminated = (
            (container.get("lastState") or {}).get("terminated") or {}
        )
        previous_termination = {
            "reason": terminated.get("reason"),
            "exit_code": terminated.get("exitCode"),
            "started_at": terminated.get("startedAt"),
            "finished_at": terminated.get("finishedAt"),
        }
        if not any(value is not None for value in previous_termination.values()):
            previous_termination = None

        warnings.append(
            {
                "pod": name,
                "namespace": namespace,
                "container": container.get("name"),
                "restart_count": restart_count,
                "previous_termination": previous_termination,
            }
        )
    return warnings


def _is_stuck(created_at: str | None) -> bool:
    if not created_at:
        return False
    try:
        timestamp = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - timestamp).total_seconds()
    return age >= PodInspector.CONTAINER_CREATING_STUCK_SECONDS


def _parse_items(stdout: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("kubectl returned invalid JSON while retrieving pods.") from exc
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ValueError("kubectl pod response did not contain an items list.")
    return items
