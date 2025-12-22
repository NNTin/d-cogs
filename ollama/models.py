from datetime import datetime
from typing import Any, Dict, List, Optional, Union

import discord
from pydantic import VERSION, Field

from cogchain.models import BaseModel
from .model_utils import resolve_model_name


class OllamaGuildConfig(BaseModel):
    chat_model: str = "gemma3"
    embed_model: str = "qwen3-embedding"
    chat_fallback: Union[str, List[str]] = Field(default_factory=lambda: ["llama3.1"])
    embed_fallback: str = "nomic-embed-text"
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
                resolved_override = resolve_model_name(override, models)
                if resolved_override:
                    return resolved_override
                # If override isn't resolvable, still honor it (it may exist but
                # not be reported yet); failures are handled at call time.
                return override

        if models:
            resolved_primary = resolve_model_name(self.chat_model, models)
            if resolved_primary:
                return resolved_primary
            for fallback in self.get_chat_fallbacks():
                resolved_fallback = resolve_model_name(fallback, models)
                if resolved_fallback:
                    return resolved_fallback

        return self.chat_model

    def get_chat_fallbacks(self) -> List[str]:
        raw = self.chat_fallback
        if isinstance(raw, list):
            return [m for m in raw if m]
        if isinstance(raw, str):
            return [raw] if raw else []
        return [str(raw)]


class OllamaConfig(BaseModel):
    endpoints: List[str] = Field(default_factory=lambda: ["http://localhost:11434"])
    endpoint_health: Dict[str, bool] = Field(default_factory=dict)
    endpoint_models: Dict[str, List[str]] = Field(default_factory=dict)
    health_check_enabled: bool = False
    health_check_interval: int = 60
    last_health_check: float = 0.0

    if VERSION >= "2.0.1":
        from pydantic import field_validator, model_validator

        @model_validator(mode="before")
        @classmethod
        def _migrate_legacy(cls, data: Any) -> Any:
            if not isinstance(data, dict):
                return data

            migrated = dict(data)

            if "endpoints" not in migrated:
                legacy_endpoint = migrated.get("endpoint")
                if legacy_endpoint:
                    migrated["endpoints"] = [legacy_endpoint]

            endpoints = migrated.get("endpoints") or []
            primary_endpoint = endpoints[0] if endpoints else None

            if "endpoint_health" not in migrated and "endpoint_healthy" in migrated and primary_endpoint:
                migrated["endpoint_health"] = {primary_endpoint: migrated.get("endpoint_healthy")}

            if "endpoint_models" not in migrated and "available_models" in migrated and primary_endpoint is not None:
                migrated["endpoint_models"] = {primary_endpoint: migrated.get("available_models") or []}

            return migrated

        @field_validator("endpoints", mode="before")
        @classmethod
        def _ensure_endpoints(cls, value: Any) -> List[str]:
            if not value:
                return ["http://localhost:11434"]
            if isinstance(value, str):
                return [value]
            if isinstance(value, (list, tuple)):
                cleaned = [str(v) for v in value if v]
                return cleaned or ["http://localhost:11434"]
            return ["http://localhost:11434"]
    else:  # pragma: no cover - pydantic v1 fallback
        from pydantic import root_validator, validator

        @root_validator(pre=True)
        def _migrate_legacy(cls, values: Dict[str, Any]) -> Dict[str, Any]:
            migrated = dict(values)

            if "endpoints" not in migrated:
                legacy_endpoint = migrated.get("endpoint")
                if legacy_endpoint:
                    migrated["endpoints"] = [legacy_endpoint]

            endpoints = migrated.get("endpoints") or []
            primary_endpoint = endpoints[0] if endpoints else None

            if "endpoint_health" not in migrated and "endpoint_healthy" in migrated and primary_endpoint:
                migrated["endpoint_health"] = {primary_endpoint: migrated.get("endpoint_healthy")}

            if "endpoint_models" not in migrated and "available_models" in migrated and primary_endpoint is not None:
                migrated["endpoint_models"] = {primary_endpoint: migrated.get("available_models") or []}

            return migrated

        @validator("endpoints", pre=True, always=True)
        def _ensure_endpoints(cls, value: Any) -> List[str]:
            if not value:
                return ["http://localhost:11434"]
            if isinstance(value, str):
                return [value]
            if isinstance(value, (list, tuple)):
                cleaned = [str(v) for v in value if v]
                return cleaned or ["http://localhost:11434"]
            return ["http://localhost:11434"]

    def get_healthy_endpoints(self) -> List[str]:
        health = self.endpoint_health or {}
        return [endpoint for endpoint in self.endpoints if health.get(endpoint)]

    def get_all_available_models(self) -> List[str]:
        models: List[str] = []
        seen = set()
        for endpoint in self.get_healthy_endpoints():
            for model in self.endpoint_models.get(endpoint, []):
                if model and model not in seen:
                    seen.add(model)
                    models.append(model)
        return models

    def is_healthy(self) -> bool:
        if not self.endpoint_health:
            return False
        return any(self.endpoint_health.values())

    def update_health(self, endpoint: str, healthy: bool, models: List[str]) -> None:
        if endpoint not in self.endpoints and endpoint:
            self.endpoints.append(endpoint)
        self.endpoint_health[endpoint] = healthy
        self.endpoint_models[endpoint] = models
        self.last_health_check = datetime.utcnow().timestamp()

    @property
    def endpoint(self) -> str:
        return self.endpoints[0] if self.endpoints else "http://localhost:11434"

    @endpoint.setter
    def endpoint(self, value: str) -> None:
        if not value:
            return
        self.endpoints = [value]
        self.endpoint_health = {value: self.endpoint_health.get(value, False)}
        self.endpoint_models = {value: self.endpoint_models.get(value, [])}

    @property
    def endpoint_healthy(self) -> bool:
        return self.endpoint_health.get(self.endpoint, False)

    @property
    def available_models(self) -> List[str]:
        return self.get_all_available_models()
