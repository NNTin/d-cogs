from datetime import datetime
from typing import Any, Dict, List, Optional

import discord
import orjson
from pydantic import VERSION, BaseModel as PydanticBaseModel, Field


class BaseModel(PydanticBaseModel):
    @classmethod
    def model_validate(cls, obj: Any) -> "BaseModel":
        if VERSION >= "2.0.1":
            return super().model_validate(obj)  # type: ignore
        return super().parse_obj(obj)  # type: ignore

    def model_dump(self, exclude_defaults: bool = True) -> Dict[str, Any]:
        if VERSION >= "2.0.1":
            return super().model_dump(mode="json", exclude_defaults=exclude_defaults)  # type: ignore
        return orjson.loads(super().json(exclude_defaults=exclude_defaults))


class Conversation(BaseModel):
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    last_updated: float = 0.0
    system_prompt_override: Optional[str] = None

    def refresh(self) -> None:
        self.last_updated = datetime.utcnow().timestamp()

    def reset(self) -> None:
        self.refresh()
        self.messages = []

    def cleanup(self, max_retention: int, max_retention_time: int) -> None:
        if max_retention_time and self.is_expired(max_retention_time):
            self.reset()
            return

        if not max_retention:
            self.messages.clear()
            return

        if len(self.messages) > max_retention:
            self.messages = self.messages[-max_retention:]

    def is_expired(self, max_retention_time: int) -> bool:
        if not max_retention_time:
            return False
        now = datetime.utcnow().timestamp()
        return bool(self.last_updated and now - self.last_updated > max_retention_time)

    def update_messages(self, message: str, role: str, name: Optional[str] = None) -> None:
        payload: Dict[str, Any] = {"role": role, "content": message}
        if name:
            payload["name"] = name
        self.messages.append(payload)
        self.refresh()

    def add_assistant_message(self, content: str) -> None:
        self.messages.append({"role": "assistant", "content": content})
        self.refresh()

    def add_tool_message(self, content: str, tool_call_id: str, name: Optional[str] = None) -> None:
        payload: Dict[str, Any] = {"role": "tool", "content": content, "tool_call_id": tool_call_id}
        if name:
            payload["name"] = name
        self.messages.append(payload)
        self.refresh()

    def get_messages(self) -> List[Dict[str, Any]]:
        return self.messages.copy()

    def prepare_chat(self, user_message: str, system_prompt: str, initial_prompt: str) -> List[Dict[str, Any]]:
        messages: List[Dict[str, Any]] = []
        prompt = self.system_prompt_override or system_prompt
        if prompt:
            messages.append({"role": "system", "content": prompt})
        if initial_prompt:
            messages.append({"role": "assistant", "content": initial_prompt})
        messages.extend(self.messages)
        messages.append({"role": "user", "content": user_message})
        return messages


class GuildConfig(BaseModel):
    enabled: bool = True
    default_provider: str = "ollama"
    max_retention: int = 50
    max_retention_time: int = 1800
    blacklist: List[int] = Field(default_factory=list)
    role_overrides: Dict[int, str] = Field(default_factory=dict)
    function_statuses: Dict[str, bool] = Field(default_factory=dict)
    channel_id: Optional[int] = None
    listen_channels: List[int] = Field(default_factory=list)
    mention_respond: bool = True
    min_length: int = 3
    use_classifier: bool = False
    classifier_model: str = "llama3.2:1b"

    def get_user_max_retention(self, member: Optional[discord.Member]) -> int:
        if not member:
            return self.max_retention

        for role in sorted(member.roles, key=lambda r: r.position, reverse=True):
            override = self.role_overrides.get(role.id)
            if override is None:
                continue
            try:
                return int(override)
            except (TypeError, ValueError):
                continue
        return self.max_retention

    def get_user_max_time(self, member: Optional[discord.Member]) -> int:
        if not member:
            return self.max_retention_time

        for role in sorted(member.roles, key=lambda r: r.position, reverse=True):
            override = self.role_overrides.get(role.id)
            if override is None:
                continue
            try:
                return int(override)
            except (TypeError, ValueError):
                continue
        return self.max_retention_time


__all__ = ["BaseModel", "Conversation", "GuildConfig"]
