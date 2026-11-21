import json
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from loguru import logger
from pydantic import BaseModel

from app.ai.agent import KubernetesAgent
from app.ai.errors import AIError
from app.core.config import get_settings
from app.kubernetes.investigator import InvestigationService
from app.kubernetes.kubectl_executor import (
    KubernetesConfigurationError,
    KubectlExecutor,
)

router = APIRouter()
bearer_scheme = HTTPBearer(auto_error=False)


class InvestigationRequest(BaseModel):
    context_name: str | None = None


def authenticate_request(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> None:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to investigate the cluster.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    base_url = get_settings().insforge_url.rstrip("/")
    if not base_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="InsForge authentication is not configured on the backend.",
        )

    try:
        response = httpx.get(
            f"{base_url}/api/auth/sessions/current",
            headers={"Authorization": f"Bearer {credentials.credentials}"},
            timeout=5.0,
        )
    except httpx.RequestError as exc:
        logger.warning("Could not verify the InsForge session: {}", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not verify the InsForge session. Try again shortly.",
        ) from exc

    if response.status_code in (401, 403):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your InsForge session is invalid or expired. Sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not response.is_success:
        logger.warning("InsForge session verification returned {}", response.status_code)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not verify the InsForge session. Try again shortly.",
        )


@router.get("/clusters")
def list_clusters(
    _authenticated: None = Depends(authenticate_request),
) -> dict[str, Any]:
    contexts = _list_contexts()
    current_context = next((item.name for item in contexts if item.current), None)
    return {
        "current_context": current_context,
        "contexts": [
            {
                "name": item.name,
                "cluster": item.cluster,
                "namespace": item.namespace,
                "current": item.current,
            }
            for item in contexts
        ],
    }


@router.post("/investigate")
def investigate(
    request: InvestigationRequest | None = None,
    _authenticated: None = Depends(authenticate_request),
) -> dict[str, Any]:
    context_name = _resolve_context(request.context_name if request else None)
    logger.info("Starting Kubernetes evidence investigation for context {}", context_name)
    try:
        investigation = InvestigationService(context_name=context_name).investigate()
    except KubernetesConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return _build_result(investigation, context_name)


@router.post("/investigate/stream")
def investigate_stream(
    request: InvestigationRequest,
    _authenticated: None = Depends(authenticate_request),
) -> StreamingResponse:
    context_name = _resolve_context(request.context_name)

    def events():
        investigation: dict[str, Any] | None = None
        service = InvestigationService(context_name=context_name)
        try:
            for event in service.iter_investigate():
                if event["type"] == "investigation":
                    investigation = event["investigation"]
                else:
                    yield _encode_event(event)
        except KubernetesConfigurationError as exc:
            yield _encode_event({"type": "error", "message": str(exc)})
            return

        if investigation is None:
            yield _encode_event(
                {
                    "type": "error",
                    "message": "The investigation completed without returning Kubernetes evidence.",
                }
            )
            return

        collection_error = _collection_error(investigation)
        if _has_findings(investigation):
            yield _encode_event(
                {
                    "type": "progress",
                    "sequence": 6,
                    "step": "AI Reasoning",
                    "status": "running",
                    "message": "Correlating the collected Kubernetes evidence.",
                }
            )
        elif collection_error:
            yield _encode_event(
                {
                    "type": "progress",
                    "sequence": 6,
                    "step": "AI Reasoning",
                    "status": "failed",
                    "message": "AI analysis was skipped because cluster evidence could not be collected.",
                }
            )
        else:
            yield _encode_event(
                {
                    "type": "progress",
                    "sequence": 6,
                    "step": "AI Reasoning",
                    "status": "complete",
                    "message": "No critical findings; AI analysis was not needed.",
                }
            )

        result = _build_result(investigation, context_name)
        if _has_findings(investigation):
            ai_status = "complete" if result["diagnosis"] is not None else "failed"
            yield _encode_event(
                {
                    "type": "progress",
                    "sequence": 6,
                    "step": "AI Reasoning",
                    "status": ai_status,
                    "message": (
                        "Diagnosis generated."
                        if ai_status == "complete"
                        else result.get("error", "AI diagnosis could not be generated.")
                    ),
                }
            )
        yield _encode_event({"type": "result", "response": result})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _list_contexts():
    try:
        return KubectlExecutor().list_contexts()
    except KubernetesConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


def _resolve_context(requested_context: str | None) -> str:
    contexts = _list_contexts()
    if requested_context:
        if any(item.name == requested_context for item in contexts):
            return requested_context
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The selected Kubernetes context is not in the current kubeconfig. Reload the cluster list.",
        )

    current = next((item.name for item in contexts if item.current), None)
    return current or contexts[0].name


def _build_result(
    investigation: dict[str, Any],
    context_name: str,
) -> dict[str, Any]:
    collection_error = _collection_error(investigation)
    if not _has_findings(investigation):
        if collection_error:
            return {
                "status": "partial",
                "context_name": context_name,
                "investigation": investigation,
                "diagnosis": None,
                "error": collection_error,
            }
        return {
            "status": "success",
            "context_name": context_name,
            "investigation": investigation,
            "diagnosis": {
                "root_cause": "No critical Kubernetes issues detected.",
                "explanation": "The selected cluster appears healthy based on the collected pod, event, deployment, and networking evidence.",
                "fix": "No immediate fix is required. Continue normal cluster monitoring.",
                "kubectl_commands": [],
                "prevention_recommendation": "Continue monitoring cluster health and deployment rollouts.",
                "confidence": 100,
                "confidence_reasoning": "No unhealthy resources or warning signals were found.",
            },
        }

    try:
        diagnosis = KubernetesAgent().diagnose(investigation)
    except AIError as exc:
        logger.warning("AI diagnosis unavailable: {}", exc)
        message = str(exc)
        if collection_error:
            message = f"{message} Some cluster evidence was unavailable: {collection_error}"
        return {
            "status": "partial",
            "context_name": context_name,
            "investigation": investigation,
            "diagnosis": None,
            "error": message,
        }

    result_status = "partial" if collection_error else "success"
    logger.info(
        "Kubernetes investigation and diagnosis completed with status {}",
        result_status,
    )
    return {
        "status": result_status,
        "context_name": context_name,
        "investigation": investigation,
        "diagnosis": diagnosis,
        **({"error": collection_error} if collection_error else {}),
    }


def _collection_error(investigation: dict[str, Any]) -> str | None:
    messages = []
    for component in (
        investigation.get("pods") or {},
        investigation.get("logs") or {},
        investigation.get("events") or {},
        investigation.get("deployments") or {},
        investigation.get("network") or {},
    ):
        error = component.get("error")
        if isinstance(error, str) and error:
            messages.append(error)
        errors = component.get("errors") or []
        for item in errors:
            message = item.get("error") if isinstance(item, dict) else item
            if isinstance(message, str) and message:
                messages.append(message)
    return messages[0] if messages else None


def _has_findings(investigation: dict[str, Any]) -> bool:
    pods = investigation.get("pods") or {}
    logs = investigation.get("logs") or {}
    events = investigation.get("events") or {}
    deployments = investigation.get("deployments") or {}
    network = investigation.get("network") or {}
    return bool(
        pods.get("problematic_pods")
        or events.get("findings")
        or any(
            not deployment.get("healthy", True)
            for deployment in deployments.get("deployments") or []
        )
        or network.get("issues")
        or network.get("dns_issues")
        or any(
            entry.get("relevant_failures_found")
            for entry in logs.get("logs") or []
        )
    )


def _encode_event(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
