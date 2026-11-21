import json
import re
from typing import Any

from app.kubernetes.kubectl_executor import KubectlExecutor


class NetworkInspector:
    DNS_PATTERN = re.compile(
        r"nxdomain|no such host|name resolution|temporary failure in name "
        r"resolution|could not resolve|dns lookup|dns.*(?:fail|error|timeout)|"
        r"enotfound|eai_again|servfail",
        re.IGNORECASE,
    )

    def __init__(self, executor: KubectlExecutor) -> None:
        self.executor = executor

    def inspect(
        self,
        pods: dict[str, Any],
        logs: dict[str, Any],
        events: dict[str, Any],
    ) -> dict[str, Any]:
        service_result = self.executor.execute(["get", "services", "-A", "-o", "json"])
        endpoint_result = self.executor.execute(
            ["get", "endpoints", "-A", "-o", "json"]
        )
        errors = []
        dns_issues = self._find_dns_issues(logs, events)
        if not service_result.success:
            errors.append(
                service_result.stderr.strip() or "Unable to retrieve services."
            )
        if not endpoint_result.success:
            errors.append(
                endpoint_result.stderr.strip() or "Unable to retrieve endpoints."
            )
        if errors:
            return {
                "healthy": False,
                "services": [],
                "issues": [],
                "dns_issues": dns_issues,
                "errors": errors,
            }
        try:
            services = json.loads(service_result.stdout).get("items", [])
            endpoints = json.loads(endpoint_result.stdout).get("items", [])
        except json.JSONDecodeError:
            return {
                "healthy": False,
                "services": [],
                "issues": [],
                "dns_issues": dns_issues,
                "errors": ["kubectl returned invalid service or endpoint JSON."],
            }
        if (
            not isinstance(services, list)
            or not isinstance(endpoints, list)
        ):
            return {
                "healthy": False,
                "services": [],
                "issues": [],
                "dns_issues": dns_issues,
                "errors": ["kubectl returned an invalid service or endpoint list."],
            }

        pod_items = pods.get("pods", [])
        endpoint_map = {
            (
                (endpoint.get("metadata") or {}).get("namespace", "default"),
                (endpoint.get("metadata") or {}).get("name"),
            ): endpoint
            for endpoint in endpoints
        }
        service_summaries = []
        issues = []
        for service in services:
            metadata = service.get("metadata") or {}
            namespace = metadata.get("namespace", "default")
            name = metadata.get("name", "unknown")
            selector = (service.get("spec") or {}).get("selector") or {}
            matching_pods = [
                pod
                for pod in pod_items
                if pod.get("namespace") == namespace
                and all(pod.get("labels", {}).get(key) == value for key, value in selector.items())
            ]
            endpoint = endpoint_map.get((namespace, name))
            ready_addresses = [
                address
                for subset in (endpoint or {}).get("subsets", [])
                for address in subset.get("addresses", [])
            ]
            summary = {
                "name": name,
                "namespace": namespace,
                "type": (service.get("spec") or {}).get("type", "ClusterIP"),
                "cluster_ip": (service.get("spec") or {}).get("clusterIP"),
                "selector": selector,
                "endpoint_count": len(ready_addresses),
            }
            service_summaries.append(summary)

            if selector and not matching_pods:
                issues.append(
                    {
                        "type": "selector_mismatch",
                        "service": name,
                        "namespace": namespace,
                        "message": "Service selector does not match any pods.",
                    }
                )
            elif selector and matching_pods and not ready_addresses:
                issues.append(
                    {
                        "type": "missing_endpoints",
                        "service": name,
                        "namespace": namespace,
                        "message": "Matching pods exist, but the service has no ready endpoints.",
                    }
                )

        return {
            "healthy": not issues and not dns_issues,
            "services": service_summaries,
            "issues": issues,
            "dns_issues": dns_issues,
            "errors": [],
        }

    def _find_dns_issues(
        self, logs: dict[str, Any], events: dict[str, Any]
    ) -> list[dict[str, str]]:
        findings = []
        for entry in logs.get("logs", []):
            for line in entry.get("lines", []):
                if self.DNS_PATTERN.search(line):
                    findings.append(
                        {
                            "source": "log",
                            "pod": entry["pod"],
                            "namespace": entry["namespace"],
                            "message": line[:500],
                        }
                    )
        for event in events.get("findings", []):
            message = event.get("message", "")
            if self.DNS_PATTERN.search(message):
                findings.append(
                    {
                        "source": "event",
                        "pod": event.get("object", "unknown"),
                        "namespace": event.get("namespace", "default"),
                        "message": message[:500],
                    }
                )
        return findings
