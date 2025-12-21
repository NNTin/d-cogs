from datetime import datetime
from typing import Dict, List, Optional, Union

import discord
from pydantic import Field

from cogchain.models import BaseModel
from pydantic import VERSION
from typing import Any

from .model_utils import resolve_model_name


class OllamaGuildConfig(BaseModel):
    chat_model: str = "gemma3"
    embed_model: str = "qwen3-embedding"
    chat_fallback: Union[str, List[str]] = Field(default_factory=lambda: ["llama3.1"])
    embed_fallback: str = "nomic-embed-text"
    tool_scope: str = "core"
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
    endpoint: str = "http://localhost:11434"
    available_models: List[str] = Field(default_factory=list)
    health_check_enabled: bool = False
    health_check_interval: int = 60
    last_health_check: float = 0.0
    endpoint_healthy: bool = False

    def is_healthy(self) -> bool:
        if not self.health_check_enabled:
            return self.endpoint_healthy

        now = datetime.utcnow().timestamp()
        if not self.last_health_check:
            return False
        return self.endpoint_healthy and now - self.last_health_check <= self.health_check_interval

    def update_health(self, healthy: bool, models: List[str]) -> None:
        self.endpoint_healthy = healthy
        self.available_models = models
        self.last_health_check = datetime.utcnow().timestamp()
