import asyncio
import logging
from collections import deque
from typing import Dict, Optional

import discord
from langchain_core.messages import convert_to_messages

from .abc import ChainProvider
from .models import GuildConfig

log = logging.getLogger("red.tin.langcore.classifier")

# Manager for per-channel conversation classifiers
# This was hastily written and needs some refactoring later together with the ConversationManager
# ClassifierManager works on a per-channel basis, maintaining buffers and states for each channel
# ConversationManager works on a per-user basis and handles conversations
# TODO: This collidion of responsibilities is not ideal and should be cleaned up

class ClassifierManager:
    def __init__(self) -> None:
        self._buffers: Dict[int, deque] = {}
        self._states: Dict[int, str] = {}

    def get_buffer(self, channel_id: int) -> deque:
        buffer = self._buffers.get(channel_id)
        if buffer is None:
            buffer = deque(maxlen=10)
            self._buffers[channel_id] = buffer
        return buffer

    def update_state(self, channel_id: int, state: str) -> None:
        old_state = self.get_state(channel_id)
        self._states[channel_id] = state
        log.info("Classifier state change (channel %d): %s -> %s", channel_id, old_state, state)

    def get_state(self, channel_id: int) -> str:
        return self._states.get(channel_id, "IGNORE")

    def clear_buffer(self, channel_id: int) -> None:
        buffer = self._buffers.get(channel_id)
        if buffer is not None:
            buffer.clear()
        self._states[channel_id] = "IGNORE"

    def reset_channel(self, channel_id: int) -> None:
        if channel_id in self._buffers:
            del self._buffers[channel_id]
        if channel_id in self._states:
            del self._states[channel_id]
        log.debug("Reset classifier state for channel %d", channel_id)

    def _format_buffer_context(self, buffer: deque) -> str:
        """Format last 5 messages from buffer for classifier prompt."""
        recent = list(buffer)[-5:]
        return "\n".join(recent) if recent else "(no recent messages)"

    async def classify(
        self,
        channel_id: int,
        message: discord.Message,
        provider: Optional[ChainProvider],
        config: GuildConfig,
        guild_id: int,
    ) -> str:
        if provider is None:
            log.error("Classifier provider missing; defaulting to IGNORE for channel %d", channel_id)
            return "IGNORE"

        content = (message.content or "").strip()
        if not content:
            log.debug("Skipping empty message for classifier in channel %d", channel_id)
            return self.get_state(channel_id)

        buffer = self.get_buffer(channel_id)
        buffer.append(f"{message.author.name}: {content}")
        log.debug("Added message to buffer (channel %d, size %d)", channel_id, len(buffer))

        context = self._format_buffer_context(buffer)
        prompt = (
            "You are a conversation classifier. Analyze the recent messages and decide:\n"
            "- RESPOND: The bot should engage in this conversation\n"
            "- IGNORE: The bot should stay silent and continue buffering\n"
            "- END: The conversation is finished, clear history\n\n"
            f"Recent messages:\n{context}\n\n"
            "Reply with exactly one word: RESPOND, IGNORE, or END."
        )

        decision = "IGNORE"
        try:
            log.debug("Invoking classifier LLM (model: %s)", config.classifier_model)
            llm = await provider.get_chat_llm(guild_id=guild_id, model=config.classifier_model)
            messages = convert_to_messages([{"role": "user", "content": prompt}])
            response = await llm.ainvoke(messages)
            decision = str(response.content).strip().upper()
        except asyncio.TimeoutError as e:
            log.error("Classifier timed out for channel %d: %s", channel_id, e)
            decision = "IGNORE"
        except Exception as e:
            log.error("Classifier failed for channel %d: %s", channel_id, e)
            decision = "IGNORE"

        if decision not in {"RESPOND", "IGNORE", "END"}:
            if "RESPOND" in decision:
                decision = "RESPOND"
            elif "IGNORE" in decision:
                decision = "IGNORE"
            elif "END" in decision:
                decision = "END"

        if decision in {"RESPOND", "IGNORE", "END"}:
            self.update_state(channel_id, decision)
            log.debug("Classifier decision for channel %d: %s", channel_id, decision)
            if decision == "END":
                buffer.clear()
            return decision

        log.warning(
            "Invalid classifier decision for channel %d, defaulting to IGNORE (raw: %s)",
            channel_id,
            decision,
        )
        self.update_state(channel_id, "IGNORE")
        return "IGNORE"
