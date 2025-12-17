"""Utility functions for ollama cog"""

import logging
from typing import Optional

from ollama import ResponseError

log = logging.getLogger("red.tin.ollama.utils")


def format_ollama_error(exc: Exception, model: Optional[str] = None, endpoint: str = "Ollama") -> str:
    """Format Ollama errors into user-friendly messages."""
    if isinstance(exc, ResponseError):
        if exc.status_code == 404 or "not found" in str(exc).lower():
            hint = "Ensure the model is available or run `ollama pull` to download it."
            return f"Model '{model}' not found at {endpoint}. {hint}"
        if exc.status_code and exc.status_code >= 500:
            return f"Ollama service error at {endpoint}. Please try again shortly."
        return f"Ollama API error at {endpoint}: {exc}"
    return f"Unexpected error communicating with Ollama at {endpoint}: {exc}"
