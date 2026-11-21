import json
import os
import subprocess
from dataclasses import dataclass
from typing import Sequence
from urllib.parse import urlsplit, urlunsplit

from loguru import logger

from app.core.config import get_settings


class KubernetesConfigurationError(Exception):
    """A user-actionable kubeconfig or kubectl setup error."""


@dataclass(frozen=True)
class KubernetesContext:
    name: str
    cluster: str
    namespace: str | None
    current: bool
    server: str


@dataclass
class KubectlResult:
    command: list[str]
    success: bool
    stdout: str
    stderr: str
    returncode: int | None

    def to_dict(self) -> dict[str, object]:
        return {
            "command": self.command,
            "success": self.success,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "returncode": self.returncode,
        }


class KubectlExecutor:
    """Run only read-only kubectl commands without invoking a shell."""

    ALLOWED_VERBS = {"get", "logs", "describe"}

    def __init__(
        self,
        timeout_seconds: int = 15,
        context_name: str | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.context_name = context_name
        self._context_flags: list[str] | None = None

    def list_contexts(self) -> list[KubernetesContext]:
        try:
            process = subprocess.run(
                ["kubectl", "config", "view", "-o", "json"],
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout_seconds,
                env=self._environment(),
                shell=False,
            )
        except FileNotFoundError as exc:
            raise KubernetesConfigurationError(
                "kubectl is not installed in the backend environment."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise KubernetesConfigurationError(
                "Reading the kubeconfig timed out. Check that the kubeconfig file is accessible."
            ) from exc
        except OSError as exc:
            raise KubernetesConfigurationError(
                "The kubeconfig could not be read. Check the configured kubeconfig path."
            ) from exc

        if process.returncode != 0:
            raise KubernetesConfigurationError(_friendly_kubectl_error(process.stderr))

        try:
            config = json.loads(process.stdout)
        except json.JSONDecodeError as exc:
            raise KubernetesConfigurationError(
                "kubectl returned an invalid kubeconfig response."
            ) from exc
        if not isinstance(config, dict):
            raise KubernetesConfigurationError(
                "kubectl returned an invalid kubeconfig response."
            )

        cluster_entries = config.get("clusters") or []
        context_entries = config.get("contexts") or []
        if not isinstance(cluster_entries, list) or not isinstance(context_entries, list):
            raise KubernetesConfigurationError(
                "The kubeconfig contains invalid cluster or context entries."
            )

        clusters = {
            entry.get("name"): (
                entry.get("cluster")
                if isinstance(entry.get("cluster"), dict)
                else {}
            )
            for entry in cluster_entries
            if isinstance(entry, dict) and isinstance(entry.get("name"), str)
        }
        current_context = config.get("current-context")
        contexts = []
        for entry in context_entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
                continue
            context = entry.get("context") or {}
            if not isinstance(context, dict):
                continue
            cluster_name = context.get("cluster")
            cluster = clusters.get(cluster_name, {})
            if not isinstance(cluster_name, str):
                cluster_name = ""
            namespace = context.get("namespace")
            contexts.append(
                KubernetesContext(
                    name=entry["name"],
                    cluster=cluster_name or "",
                    namespace=namespace if isinstance(namespace, str) else None,
                    current=entry["name"] == current_context,
                    server=cluster.get("server", ""),
                )
            )

        if not contexts:
            raise KubernetesConfigurationError(
                "No Kubernetes contexts were found. Configure a kubeconfig file and restart the backend."
            )
        return contexts

    def execute(self, args: Sequence[str]) -> KubectlResult:
        if not args or not isinstance(args[0], str) or args[0] not in self.ALLOWED_VERBS:
            raise ValueError("Only kubectl get, logs, and describe commands are allowed.")
        if any(not isinstance(arg, str) or "\0" in arg for arg in args):
            raise ValueError("kubectl arguments must be valid strings.")

        command = [
            "kubectl",
            *self._get_context_flags(),
            "--request-timeout=15s",
            *args,
        ]

        logger.info("Executing read-only Kubernetes command: {}", command)
        try:
            process = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout_seconds,
                env=self._environment(),
                shell=False,
            )
        except FileNotFoundError as exc:
            message = "kubectl is not installed in the backend environment."
            logger.error(message)
            return KubectlResult(command, False, "", message, None)
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode(errors="replace")
            message = (
                "Kubernetes request timed out. Verify that the selected cluster is running "
                "and reachable."
            )
            logger.error("{} Command: {}", message, command)
            return KubectlResult(command, False, stdout, message, None)
        except OSError as exc:
            message = "Unable to run kubectl. Check the backend installation and configuration."
            logger.error("{} Command: {} Error: {}", message, command, exc)
            return KubectlResult(command, False, "", message, None)

        result = KubectlResult(
            command=command,
            success=process.returncode == 0,
            stdout=process.stdout,
            stderr=(
                ""
                if process.returncode == 0
                else _friendly_kubectl_error(process.stderr)
            ),
            returncode=process.returncode,
        )
        if result.success:
            logger.debug("kubectl command completed successfully: {}", command)
        else:
            logger.warning(
                "kubectl command failed ({}): {}",
                process.returncode,
                process.stderr.strip(),
            )
        return result

    def _get_context_flags(self) -> list[str]:
        if self._context_flags is not None:
            return self._context_flags
        if not self.context_name:
            self._context_flags = []
            return self._context_flags

        context = next(
            (item for item in self.list_contexts() if item.name == self.context_name),
            None,
        )
        if context is None:
            raise KubernetesConfigurationError(
                "The selected Kubernetes context is no longer available. Reload the cluster list and try again."
            )

        flags = ["--context", context.name]
        host_gateway = get_settings().kubernetes_host_gateway.strip()
        if host_gateway and context.server:
            server = urlsplit(context.server)
            if server.hostname and server.hostname.lower() in {
                "localhost",
                "127.0.0.1",
                "::1",
            }:
                netloc = host_gateway
                if server.port:
                    netloc = f"{host_gateway}:{server.port}"
                flags.extend(
                    [
                        "--server",
                        urlunsplit(
                            (
                                server.scheme,
                                netloc,
                                server.path,
                                server.query,
                                server.fragment,
                            )
                        ),
                        "--tls-server-name",
                        server.hostname,
                    ]
                )

        self._context_flags = flags
        return flags

    @staticmethod
    def _environment() -> dict[str, str] | None:
        kubeconfig_path = get_settings().kubeconfig_path.strip()
        if not kubeconfig_path:
            return None
        environment = os.environ.copy()
        environment["KUBECONFIG"] = kubeconfig_path
        return environment


def _friendly_kubectl_error(message: str) -> str:
    lower_message = message.lower()
    if "current-context is not set" in lower_message or "no configuration has been provided" in lower_message:
        return "No active Kubernetes context was found. Select a context from your kubeconfig."
    if "connection refused" in lower_message or "unable to connect" in lower_message or "i/o timeout" in lower_message:
        return (
            "Unable to connect to the selected Kubernetes cluster. Verify the kubeconfig path, "
            "cluster access, and that the cluster is running."
        )
    if "forbidden" in lower_message or "unauthorized" in lower_message:
        return (
            "The selected Kubernetes context does not have permission to read the cluster "
            "resources needed for troubleshooting."
        )
    if "kubeconfig" in lower_message or "config file" in lower_message:
        return "The kubeconfig could not be loaded. Verify the configured kubeconfig path."
    return message.strip() or "kubectl could not complete the request."
