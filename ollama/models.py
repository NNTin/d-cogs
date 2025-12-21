from datetime import datetime
from typing import Dict, List, Optional

import discord
from pydantic import Field

from cogchain.models import BaseModel

from .model_utils import resolve_model_name


class OllamaGuildConfig(BaseModel):
    chat_model: str = "gemma3"
    embed_model: str = "qwen3-embedding"
    chat_fallback: str = "llama3.1"
    embed_fallback: str = "nomic-embed-text"
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
            resolved_fallback = resolve_model_name(self.chat_fallback, models)
            if resolved_fallback:
                return resolved_fallback

        return self.chat_model


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
