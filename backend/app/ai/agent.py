from typing import Any

from app.ai.diagnosis import (
    ConfidenceEngine,
    FixRecommendationEngine,
    RootCauseAnalyzer,
)
from app.ai.llm_client import OpenAICompatibleClient


class KubernetesAgent:
    def __init__(self, llm_client: OpenAICompatibleClient | None = None) -> None:
        self.root_cause_analyzer = RootCauseAnalyzer(llm_client=llm_client)
        self.fix_recommendation_engine = FixRecommendationEngine()
        self.confidence_engine = ConfidenceEngine()

    def diagnose(self, investigation: dict[str, Any]) -> dict[str, Any]:
        diagnosis = self.root_cause_analyzer.analyze(investigation)
        diagnosis = self.fix_recommendation_engine.apply(diagnosis)
        diagnosis = self.confidence_engine.apply(diagnosis, investigation)
        return diagnosis.model_dump()
