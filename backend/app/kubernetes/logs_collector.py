import re
from typing import Any

from app.kubernetes.kubectl_executor import KubectlExecutor


class LogsCollector:
    MAX_LOG_LINES = 20
    MAX_LINE_LENGTH = 500
    FAILURE_PATTERN = re.compile(
        r"exception|traceback|error|failed|failure|refused|timeout|"
        r"connection|econnreset|econnrefused|etimedout|ehostunreach|"
        r"enetunreach|eai_again|missing|not set|not found|image|startup|"
        r"no such host|nxdomain|name resolution",
        re.IGNORECASE,
    )

    def __init__(self, executor: KubectlExecutor) -> None:
        self.executor = executor

    def collect(self, problematic_pods: list[dict[str, Any]]) -> dict[str, Any]:
        collected = []
        errors = []
        for pod in problematic_pods:
            pod_name = pod["name"]
            namespace = pod["namespace"]
            containers = pod.get("containers") or [None]
            for container_name in containers:
                args = ["logs", pod_name, "-n", namespace, "--tail=100"]
                if container_name:
                    args.extend(["-c", container_name])
                result = self.executor.execute(args)
                entry = {
                    "pod": pod_name,
                    "namespace": namespace,
                    "container": container_name,
                }
                if not result.success:
                    errors.append(
                        {
                            **entry,
                            "error": result.stderr.strip() or "Unable to retrieve logs.",
                        }
                    )
                    continue
                lines = result.stdout.splitlines()
                relevant = [line for line in lines if self.FAILURE_PATTERN.search(line)]
                selected = (relevant or lines[-10:])[-self.MAX_LOG_LINES :]
                entry["relevant_failures_found"] = bool(relevant)
                entry["lines"] = [
                    line[: self.MAX_LINE_LENGTH] for line in selected
                ]
                collected.append(entry)

        return {"logs": collected, "errors": errors}
