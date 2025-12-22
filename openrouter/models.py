from datetime import datetime
from typing import Any, Dict, List, Optional

import discord
from pydantic import VERSION, Field

from cogchain.models import BaseModel


class OpenRouterGuildConfig(BaseModel):
    chat_model: str = "openai/gpt-4o-mini"
    embed_model: str = "text-embedding-3-large"
    chat_fallback: List[str] = Field(default_factory=lambda: ["openai/gpt-3.5-turbo"])
    embed_fallback: str = "text-embedding-3-large"
    tool_scope: str = "core"
    llm_selection_strategy: str = "fallback"
    role_model_overrides: Dict[int, str] = Field(default_factory=dict)

    if VERSION >= "2.0.1":
        from pydantic import field_validator

        @field_validator("chat_fallback", mode="before")
        @classmethod
        def _normalize_chat_fallback(cls, value: Any) -> List[str]:
            if value is None:
                return []
            if isinstance(value, str):
                return [value]
            if isinstance(value, (list, tuple)):
                return [str(v) for v in value if v]
            return [str(value)]

        @field_validator("llm_selection_strategy", mode="before")
        @classmethod
        def _normalize_strategy(cls, value: Any) -> str:
            if isinstance(value, str):
                lowered = value.lower()
                if lowered in ("fallback", "loadbalancing"):
                    return lowered
            return "fallback"
    else:  # pragma: no cover - pydantic v1 fallback
        from pydantic import validator

        @validator("chat_fallback", pre=True, always=True)
        def _normalize_chat_fallback(cls, value: Any) -> List[str]:
            if value is None:
                return []
            if isinstance(value, str):
                return [value]
            if isinstance(value, (list, tuple)):
                return [str(v) for v in value if v]
            return [str(value)]

        @validator("llm_selection_strategy", pre=True, always=True)
        def _normalize_strategy(cls, value: Any) -> str:
            if isinstance(value, str):
                lowered = value.lower()
                if lowered in ("fallback", "loadbalancing"):
                    return lowered
            return "fallback"

    def get_user_model(
        self, member: Optional[discord.Member], available_models: Optional[List[str]]
    ) -> str:
        models = available_models or []

        if member:
            for role in sorted(member.roles, key=lambda r: r.position, reverse=True):
                override = self.role_model_overrides.get(role.id)
                if not override:
                    continue
                if not models:
                    return override
                from .model_utils import resolve_model_name  # local import to avoid cycles

                resolved_override = resolve_model_name(override, models)
                if resolved_override:
                    return resolved_override
                return override

        if models:
            from .model_utils import resolve_model_name

            resolved_primary = resolve_model_name(self.chat_model, models)
            if resolved_primary:
                return resolved_primary
            for fallback in self.get_chat_fallbacks():
                resolved_fallback = resolve_model_name(fallback, models)
                if resolved_fallback:
                    return resolved_fallback

        return self.chat_model

    def get_chat_fallbacks(self) -> List[str]:
        return [m for m in (self.chat_fallback or []) if m]


class OpenRouterConfig(BaseModel):
    api_key: str = ""
    base_urls: List[str] = Field(default_factory=lambda: ["https://openrouter.ai/api/v1"])
    default_headers: Dict[str, str] = Field(default_factory=dict)
    endpoint_health: Dict[str, bool] = Field(default_factory=dict)
    endpoint_models: Dict[str, List[str]] = Field(default_factory=dict)
    health_check_enabled: bool = False
    health_check_interval: int = 60
    last_health_check: float = 0.0

    if VERSION >= "2.0.1":
        from pydantic import field_validator, model_validator

        @model_validator(mode="before")
        @classmethod
        def _migrate_legacy(cls, value: Dict[str, Any]) -> Dict[str, Any]:  # type: ignore[type-arg]
            if not isinstance(value, dict):
                return value
            data = dict(value)
            base_url = data.pop("base_url", None)
            if base_url:
                data.setdefault("base_urls", [base_url])
            available_models = data.pop("available_models", None)
            endpoint_healthy = data.pop("endpoint_healthy", None)
            if available_models and "endpoint_models" not in data:
                primary = (data.get("base_urls") or [None])[0]
                if primary:
                    data["endpoint_models"] = {primary: available_models}
            if endpoint_healthy is not None and "endpoint_health" not in data:
                primary = (data.get("base_urls") or [None])[0]
                if primary:
                    data["endpoint_health"] = {primary: endpoint_healthy}
            return data

        @field_validator("base_urls", mode="before")
        @classmethod
        def _normalize_base_urls(cls, value: Any) -> List[str]:
            if not value:
                return ["https://openrouter.ai/api/v1"]
            if isinstance(value, str):
                return [value]
            if isinstance(value, (list, tuple)):
                cleaned = [str(v) for v in value if v]
                return cleaned or ["https://openrouter.ai/api/v1"]
            return ["https://openrouter.ai/api/v1"]
    else:  # pragma: no cover - pydantic v1 fallback
        from pydantic import root_validator, validator

        @root_validator(pre=True)
        def _migrate_legacy(cls, values: Dict[str, Any]) -> Dict[str, Any]:
            data = dict(values)
            base_url = data.pop("base_url", None)
            if base_url:
                data.setdefault("base_urls", [base_url])
            available_models = data.pop("available_models", None)
            endpoint_healthy = data.pop("endpoint_healthy", None)
            if available_models and "endpoint_models" not in data:
                primary = (data.get("base_urls") or [None])[0]
                if primary:
                    data["endpoint_models"] = {primary: available_models}
            if endpoint_healthy is not None and "endpoint_health" not in data:
                primary = (data.get("base_urls") or [None])[0]
                if primary:
                    data["endpoint_health"] = {primary: endpoint_healthy}
            return data

        @validator("base_urls", pre=True, always=True)
        def _normalize_base_urls(cls, value: Any) -> List[str]:
            if not value:
                return ["https://openrouter.ai/api/v1"]
            if isinstance(value, str):
                return [value]
            if isinstance(value, (list, tuple)):
                cleaned = [str(v) for v in value if v]
                return cleaned or ["https://openrouter.ai/api/v1"]
            return ["https://openrouter.ai/api/v1"]

    def has_api_key(self) -> bool:
        return bool(self.api_key)

    def is_healthy(self) -> bool:
        if not self.health_check_enabled:
            return any(self.endpoint_health.values()) if self.endpoint_health else False

        now = datetime.utcnow().timestamp()
        if not self.last_health_check:
            return False
        healthy_recent = now - self.last_health_check <= self.health_check_interval
        return healthy_recent and any(self.endpoint_health.values())

    def update_health(self, endpoint: str, healthy: bool, models: List[str]) -> None:
        if endpoint not in self.base_urls and endpoint:
            self.base_urls.append(endpoint)
        self.endpoint_health[endpoint] = healthy
        self.endpoint_models[endpoint] = models
        self.last_health_check = datetime.utcnow().timestamp()

    def get_healthy_endpoints(self) -> List[str]:
        if not self.endpoint_health:
            return []
        return [endpoint for endpoint, healthy in self.endpoint_health.items() if healthy]

    def get_all_available_models(self) -> List[str]:
        models: List[str] = []
        seen = set()
        for endpoint, endpoint_models in self.endpoint_models.items():
            if not self.endpoint_health or self.endpoint_health.get(endpoint):
                for model in endpoint_models:
                    if model and model not in seen:
                        seen.add(model)
                        models.append(model)
        return models

    @property
    def base_url(self) -> str:
        return self.base_urls[0] if self.base_urls else "https://openrouter.ai/api/v1"

    @base_url.setter
    def base_url(self, value: str) -> None:
        if not value:
            return
        self.base_urls = [value]
        self.endpoint_health = {value: self.endpoint_health.get(value, False)}
        self.endpoint_models = {value: self.endpoint_models.get(value, [])}

    @property
    def available_models(self) -> List[str]:
        return self.get_all_available_models()
