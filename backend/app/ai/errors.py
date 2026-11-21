class AIError(Exception):
    """Base error for AI diagnosis failures safe to report through the API."""


class LLMClientError(AIError):
    """OpenRouter request or response failure."""


class DiagnosisFormatError(AIError):
    """The model did not return a valid diagnosis."""
