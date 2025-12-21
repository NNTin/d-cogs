"""Utility helpers for OpenRouter provider."""

import logging
from typing import Optional

try:
    from openai import (  # type: ignore
        APIConnectionError,
        APIError,
        APIStatusError,
        AuthenticationError,
        PermissionDeniedError,
        RateLimitError,
    )
except Exception:  # noqa: BLE001
    APIConnectionError = APIError = APIStatusError = AuthenticationError = PermissionDeniedError = RateLimitError = Exception  # type: ignore[assignment]

log = logging.getLogger("red.tin.openrouter.utils")


def format_openrouter_error(exc: Exception, model: Optional[str] = None, endpoint: str = "OpenRouter") -> str:
    """Format OpenRouter errors into user-friendly messages."""
    if isinstance(exc, AuthenticationError):
        return "OpenRouter authentication failed. Please set a valid API key."
    if isinstance(exc, PermissionDeniedError):
        return "OpenRouter rejected the request. Check your plan and permissions."
    if isinstance(exc, RateLimitError):
        return "OpenRouter rate limit reached. Please wait and try again."
    if isinstance(exc, APIConnectionError):
        return f"Unable to reach OpenRouter at {endpoint}. Check connectivity."
    if isinstance(exc, APIStatusError):
        if getattr(exc, "status_code", None) == 404:
            hint = f"Requested model '{model}' was not found." if model else "Requested model was not found."
            return f"{hint} Verify the model name or choose a fallback."
        if getattr(exc, "status_code", None) and exc.status_code >= 500:
            return "OpenRouter service is currently unavailable. Please try again later."
    if isinstance(exc, APIError):
        return f"OpenRouter API error: {exc}"
    return f"Unexpected OpenRouter error: {exc}"
