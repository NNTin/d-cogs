"""Ollama API client implementation."""

import asyncio
import logging
from typing import Any, Dict, List, Optional

import aiohttp
from pydantic import Field
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_random_exponential

from langcore.models import BaseModel

log = logging.getLogger("red.ollama.api")


class OllamaChatResponse(BaseModel):
    model: str
    message: Dict[str, Any]
    done: bool
    total_duration: Optional[int] = None
    prompt_eval_count: Optional[int] = None
    eval_count: Optional[int] = None


class OllamaEmbedResponse(BaseModel):
    model: str
    embeddings: List[List[float]]


class OllamaModelInfo(BaseModel):
    name: str
    modified_at: str
    size: int
    digest: str
    details: Dict[str, Any] = Field(default_factory=dict)


class OllamaModelsResponse(BaseModel):
    models: List[OllamaModelInfo] = Field(default_factory=list)


class OllamaClientError(Exception):
    """Base exception for Ollama client errors."""

    def __init__(
        self,
        message: str,
        *,
        endpoint: str,
        model: Optional[str] = None,
        original: Optional[Exception] = None,
    ) -> None:
        super().__init__(message)
        self.endpoint = endpoint
        self.model = model
        self.original = original


class OllamaConnectionError(OllamaClientError):
    """Raised when the Ollama endpoint cannot be reached."""


class OllamaAPIError(OllamaClientError):
    """Raised when the Ollama API responds with an error."""

    def __init__(
        self,
        message: str,
        *,
        endpoint: str,
        model: Optional[str] = None,
        status_code: Optional[int] = None,
        response_text: Optional[str] = None,
        original: Optional[Exception] = None,
    ) -> None:
        super().__init__(message, endpoint=endpoint, model=model, original=original)
        self.status_code = status_code
        self.response_text = response_text


class OllamaClient:
    """
    Stateless HTTP client for the Ollama API.

    This client is consumed by the `ollama.ollama` ChainProvider implementation and the
    `ollama.health` monitor. Endpoint URLs are passed per call, allowing different
    instances of `OllamaConfig` to reuse the same client. Model selection is handled by
    `OllamaGuildConfig.get_user_model` in `ollama.models`.
    """

    @staticmethod
    def _normalize_endpoint(endpoint: str) -> str:
        return endpoint.rstrip("/")

    @staticmethod
    @retry(
        retry=retry_if_exception_type(OllamaConnectionError),
        wait=wait_random_exponential(min=1, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def chat(
        endpoint: str,
        model: str,
        messages: List[Dict[str, Any]],
        **options: Any,
    ) -> OllamaChatResponse:
        url = f"{OllamaClient._normalize_endpoint(endpoint)}/api/chat"
        payload: Dict[str, Any] = {"model": model, "messages": messages, "stream": False}
        allowed_options = {"temperature", "num_predict", "frequency_penalty", "presence_penalty", "seed"}
        filtered_options = {k: v for k, v in options.items() if k in allowed_options and v is not None}
        if filtered_options:
            payload["options"] = filtered_options

        log.debug("Sending chat request to %s with model=%s messages=%s", url, model, len(messages))
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload) as response:
                    if response.status >= 400:
                        response_text = await response.text()
                        log.error("Ollama chat error %s: %s", response.status, response_text)
                        raise OllamaAPIError(
                            f"Ollama chat request failed with status {response.status}",
                            endpoint=endpoint,
                            model=model,
                            status_code=response.status,
                            response_text=response_text,
                        )
                    data = await response.json()
                    log.debug("Received chat response from %s for model=%s", url, model)
                    return OllamaChatResponse.model_validate(data)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            log.error("Connection error calling Ollama chat at %s: %s", url, exc)
            raise OllamaConnectionError(
                "Failed to reach Ollama chat endpoint",
                endpoint=endpoint,
                model=model,
                original=exc,
            )

    @staticmethod
    @retry(
        retry=retry_if_exception_type(OllamaConnectionError),
        wait=wait_random_exponential(min=1, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def embed(endpoint: str, model: str, text: str) -> List[float]:
        url = f"{OllamaClient._normalize_endpoint(endpoint)}/api/embed"
        payload = {"model": model, "input": text}
        log.debug("Sending embed request to %s with model=%s", url, model)
        try:
            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload) as response:
                    if response.status >= 400:
                        response_text = await response.text()
                        log.error("Ollama embed error %s: %s", response.status, response_text)
                        raise OllamaAPIError(
                            f"Ollama embed request failed with status {response.status}",
                            endpoint=endpoint,
                            model=model,
                            status_code=response.status,
                            response_text=response_text,
                        )
                    data = await response.json()
                    log.debug("Received embed response from %s for model=%s", url, model)
                    embed_response = OllamaEmbedResponse.model_validate(data)
                    return embed_response.embeddings[0] if embed_response.embeddings else []
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            log.error("Connection error calling Ollama embed at %s: %s", url, exc)
            raise OllamaConnectionError(
                "Failed to reach Ollama embed endpoint",
                endpoint=endpoint,
                model=model,
                original=exc,
            )

    @staticmethod
    @retry(
        retry=retry_if_exception_type(OllamaConnectionError),
        wait=wait_random_exponential(min=1, max=30),
        stop=stop_after_attempt(2),
        reraise=True,
    )
    async def list_models(endpoint: str) -> List[OllamaModelInfo]:
        url = f"{OllamaClient._normalize_endpoint(endpoint)}/api/tags"
        log.debug("Requesting model list from %s", url)
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status >= 400:
                        response_text = await response.text()
                        log.error("Ollama list models error %s: %s", response.status, response_text)
                        raise OllamaAPIError(
                            f"Ollama list models failed with status {response.status}",
                            endpoint=endpoint,
                            status_code=response.status,
                            response_text=response_text,
                        )
                    data = await response.json()
                    model_response = OllamaModelsResponse.model_validate(data)
                    log.debug("Retrieved %s models from %s", len(model_response.models), url)
                    return model_response.models
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            log.error("Connection error calling Ollama list_models at %s: %s", url, exc)
            raise OllamaConnectionError(
                "Failed to reach Ollama list models endpoint",
                endpoint=endpoint,
                original=exc,
            )


async def check_health(endpoint: str) -> bool:
    """Return True when the Ollama endpoint responds to list_models; False otherwise."""
    try:
        await OllamaClient.list_models(endpoint)
        return True
    except Exception as exc:  # noqa: BLE001
        log.debug("Health check failed for %s: %s", endpoint, exc)
        return False


def format_error_message(exc: Exception, model: Optional[str] = None) -> str:
    """
    Produce a user-friendly error message suitable for Discord responses.
    Includes hints for common issues like missing models or unreachable endpoints.
    """
    endpoint = getattr(exc, "endpoint", "the configured endpoint")
    target_model = model or getattr(exc, "model", None)

    if isinstance(exc, OllamaConnectionError):
        return f"Unable to reach the Ollama service at {endpoint}. Please verify the endpoint is running."

    if isinstance(exc, OllamaAPIError):
        status = f" (status {exc.status_code})" if getattr(exc, "status_code", None) else ""
        base_message = f"Ollama API error at {endpoint}{status}"
        if target_model:
            base_message += f" for model '{target_model}'"

        response_text = (exc.response_text or "").lower()
        if exc.status_code == 404 or "model not found" in response_text:
            hint = "Ensure the model is available or run `ollama pull` to download it."
            return f"{base_message}: model not found. {hint}"

        if exc.status_code and exc.status_code >= 500:
            return f"{base_message}: the service returned an error. Please try again shortly."

        detail = exc.response_text.strip() if exc.response_text else "unexpected error."
        return f"{base_message}: {detail}"

    return f"Unexpected error communicating with Ollama at {endpoint}."
