import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ai.errors import DiagnosisFormatError
from app.ai.llm_client import OpenAICompatibleClient
from app.ai.prompt_builder import PromptBuilder


class Diagnosis(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    root_cause: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    fix: str = Field(min_length=1)
    kubectl_commands: list[str] = Field(default_factory=list, max_length=5)
    prevention_recommendation: str = Field(min_length=1)
    confidence: int = Field(ge=0, le=100)
    confidence_reasoning: str = Field(min_length=1)


class RootCauseAnalyzer:
    def __init__(
        self,
        llm_client: OpenAICompatibleClient | None = None,
        prompt_builder: PromptBuilder | None = None,
    ) -> None:
        self.llm_client = llm_client or OpenAICompatibleClient()
        self.prompt_builder = prompt_builder or PromptBuilder()

    def analyze(self, investigation: dict[str, Any]) -> Diagnosis:
        messages = self.prompt_builder.build_messages(investigation)
        content = self.llm_client.complete(messages)
        try:
            return self._parse_diagnosis(content)
        except DiagnosisFormatError:
            correction_messages = [
                *messages,
                {
                    "role": "user",
                    "content": (
                        "Your previous response did not match the required diagnosis schema. "
                        "Return one JSON object with the exact required fields, valid values, "
                        "and no surrounding text."
                    ),
                },
            ]
            return self._parse_diagnosis(self.llm_client.complete(correction_messages))

    @staticmethod
    def _parse_diagnosis(content: str) -> Diagnosis:
        start = content.find("{")
        end = content.rfind("}")
        if start < 0 or end < start:
            raise DiagnosisFormatError("The AI response did not contain a JSON diagnosis.")
        try:
            payload = json.loads(content[start : end + 1])
            if isinstance(payload, dict) and isinstance(payload.get("diagnosis"), dict):
                payload = payload["diagnosis"]
            return Diagnosis.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise DiagnosisFormatError(
                "The AI response did not match the required diagnosis format."
            ) from exc


class FixRecommendationEngine:
    ALLOWED_KUBECTL_VERBS = {
        "get",
        "describe",
        "logs",
        "edit",
        "set",
        "rollout",
        "scale",
        "apply",
        "patch",
    }
    UNSAFE_COMMAND_CHARACTERS = re.compile(r"[;&|`$<>*\\\r\n]")
    SECRET_RESOURCE = re.compile(r"\bsecrets?\b", re.IGNORECASE)
    SECRET_ASSIGNMENT = re.compile(
        r"\b[A-Z0-9_.-]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API[_-]?KEY|"
        r"ACCESS[_-]?KEY|CREDENTIAL)[A-Z0-9_.-]*\s*[:=]\s*\S+",
        re.IGNORECASE,
    )
    URI_CREDENTIALS = re.compile(r"://[^:/@\s]+:[^/@\s]+@")

    def apply(self, diagnosis: Diagnosis) -> Diagnosis:
        safe_commands = []
        for command in diagnosis.kubectl_commands:
            cleaned = command.strip()
            parts = cleaned.split()
            if (
                len(parts) >= 2
                and parts[0] == "kubectl"
                and parts[1] in self.ALLOWED_KUBECTL_VERBS
                and not self.UNSAFE_COMMAND_CHARACTERS.search(cleaned)
                and not self.SECRET_RESOURCE.search(cleaned)
                and not self.SECRET_ASSIGNMENT.search(cleaned)
                and not self.URI_CREDENTIALS.search(cleaned)
            ):
                safe_commands.append(cleaned)
        return diagnosis.model_copy(update={"kubectl_commands": safe_commands[:5]})


class ConfidenceEngine:
    EVIDENCE_LIMITS = {0: 25, 1: 70, 2: 85}

    def apply(
        self,
        diagnosis: Diagnosis,
        investigation: dict[str, Any],
    ) -> Diagnosis:
        sources = self._evidence_sources(investigation)
        limit = self.EVIDENCE_LIMITS.get(len(sources), 95)
        confidence = min(diagnosis.confidence, limit)

        if sources:
            reasoning = "Corroborated by: " + ", ".join(sources) + "."
        else:
            reasoning = "No explicit failure indicators were found in the evidence."
        if confidence < diagnosis.confidence:
            reasoning += (
                f" Confidence was capped at {limit}% because only "
                f"{len(sources)} evidence categories support the diagnosis."
            )
        elif diagnosis.confidence < limit:
            reasoning += (
                f" The model assigned a conservative confidence of {confidence}%."
            )

        return diagnosis.model_copy(
            update={
                "confidence": confidence,
                "confidence_reasoning": reasoning,
            }
        )

    @staticmethod
    def _evidence_sources(investigation: dict[str, Any]) -> list[str]:
        sources = []
        pods = investigation.get("pods") or {}
        if pods.get("problematic_pods"):
            sources.append("pod status")

        logs = investigation.get("logs") or {}
        if logs.get("error") or any(
            entry.get("relevant_failures_found")
            for entry in logs.get("logs", [])
        ):
            sources.append("application logs")

        events = investigation.get("events") or {}
        if events.get("findings"):
            sources.append("Kubernetes events")

        deployments = investigation.get("deployments") or {}
        deployment_items = deployments.get("deployments") or []
        if any(not item.get("healthy", True) for item in deployment_items):
            sources.append("deployment health")

        network = investigation.get("network") or {}
        if network.get("issues") or network.get("dns_issues"):
            sources.append("network findings")
        return sources
