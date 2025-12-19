from __future__ import annotations

from typing import Iterable, Optional


def resolve_model_name(desired: str, available_models: Iterable[str]) -> Optional[str]:
    """
    Resolve a configured model name to a concrete name from the Ollama model list.

    Ollama commonly reports models with tags (e.g. `gemma3:latest`) while users
    often configure tagless names (e.g. `gemma3`). This function maps tagless
    names to a matching tagged model, preferring `:latest` when present.
    """
    models = [m for m in available_models if m]
    if not desired or not models:
        return None

    if desired in models:
        return desired

    if ":" not in desired:
        latest = f"{desired}:latest"
        if latest in models:
            return latest
        prefix = desired + ":"
        for model in models:
            if model.startswith(prefix):
                return model
        return None

    base = desired.split(":", 1)[0]
    if base in models:
        return base

    prefix = base + ":"
    if base and any(m.startswith(prefix) for m in models):
        latest = f"{base}:latest"
        if latest in models:
            return latest
        for model in models:
            if model.startswith(prefix):
                return model
    return None


def is_embedding_model(model_name: str) -> bool:
    name = (model_name or "").lower()
    # Heuristic: Ollama doesn't label capabilities in `list()`, so we infer from
    # common naming conventions.
    embedding_markers = (
        "embedding",
        "embed",
        "minilm",
        "text-embedding",
        "bge-",
        "e5-",
    )
    return any(marker in name for marker in embedding_markers)


def select_default_chat_model(available_models: Iterable[str]) -> Optional[str]:
    models = [m for m in available_models if m]
    for model in models:
        if not is_embedding_model(model):
            return model
    return models[0] if models else None


def select_default_embed_model(available_models: Iterable[str]) -> Optional[str]:
    models = [m for m in available_models if m]
    for model in models:
        if is_embedding_model(model):
            return model
    return models[0] if models else None
