from datetime import datetime
from typing import Dict, List, Optional

import discord
from pydantic import Field

from langcore.models import BaseModel


class OpenRouterGuildConfig(BaseModel):
    chat_model: str = "openai/gpt-4o-mini"
    embed_model: str = "text-embedding-3-large"
    chat_fallback: str = "openai/gpt-3.5-turbo"
    embed_fallback: str = "text-embedding-3-large"
    tool_scope: str = "core"
    role_model_overrides: Dict[int, str] = Field(default_factory=dict)

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
            resolved_fallback = resolve_model_name(self.chat_fallback, models)
            if resolved_fallback:
                return resolved_fallback

        return self.chat_model


class OpenRouterConfig(BaseModel):
    api_key: str = ""
    base_url: str = "https://openrouter.ai/api/v1"
    default_headers: Dict[str, str] = Field(default_factory=dict)
    available_models: List[str] = Field(default_factory=list)
    health_check_enabled: bool = False
    health_check_interval: int = 60
    last_health_check: float = 0.0
    endpoint_healthy: bool = False

    def has_api_key(self) -> bool:
        return bool(self.api_key)

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
