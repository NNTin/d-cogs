from __future__ import annotations

from typing import Iterable, Optional


def resolve_model_name(desired: str, available_models: Iterable[str]) -> Optional[str]:
    """
    Resolve a configured model name to a concrete name from the OpenRouter model list.

    OpenRouter models are typically provider-prefixed (e.g. `openai/gpt-4o`) and may
    include version suffixes. This attempts to match on full name, then on prefix.
    """
    models = [m for m in available_models if m]
    if not desired or not models:
        return None

    if desired in models:
        return desired

    # Allow shorthand without provider prefix if unique
    shorthand = desired.split("/")[-1]
    for model in models:
        if model.endswith("/" + shorthand) or model == shorthand:
            return model

    # Try matching by base without version suffix (e.g., ':latest' or '-latest')
    base = desired.split(":", 1)[0].split("@", 1)[0]
    for model in models:
        candidate_base = model.split(":", 1)[0].split("@", 1)[0]
        if candidate_base == base:
            return model

    return None


def is_embedding_model(model_name: str) -> bool:
    name = (model_name or "").lower()
    embedding_markers = (
        "embedding",
        "embed",
        "text-embedding",
        "nomic-embed",
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
