import logging
from typing import Dict, Optional, Tuple

import discord

from .models import Conversation, GuildConfig

logger = logging.getLogger("red.langcore.conversation")


class ConversationManager:
    """Manages conversation lifecycle including creation, retrieval, cleanup, and reset operations."""

    def __init__(self) -> None:
        self._conversations: Dict[str, Conversation] = {}

    def get_conversation(self, member_id: int, channel_id: int, guild_id: int) -> Conversation:
        """Retrieve a conversation, creating it if it does not yet exist.

        Args:
            member_id (int): Discord member identifier.
            channel_id (int): Channel identifier where the conversation occurs.
            guild_id (int): Guild identifier for scoping the conversation.

        Returns:
            Conversation: The stored or newly created conversation instance.

        Example:
            >>> manager.get_conversation(1, 2, 3)
            Conversation(...)
        """
        key = f"{member_id}-{channel_id}-{guild_id}"
        if key not in self._conversations:
            logger.debug("Created new conversation for member %s in channel %s (guild %s)", member_id, channel_id, guild_id)
        return self._conversations.setdefault(key, Conversation())

    def cleanup_expired(self, guild_id: int, config: GuildConfig, member: Optional[discord.Member] = None) -> int:
        """Clean up conversations for a guild based on retention settings.

        Args:
            guild_id (int): Target guild identifier to clean conversations for.
            config (GuildConfig): Retention configuration to apply.
            member (Optional[discord.Member], optional): Member context for role-based overrides. Defaults to None.

        Returns:
            int: Number of conversations cleaned for the guild.

        Example:
            >>> manager.cleanup_expired(123, config)
            2
        """
        cleaned = 0
        max_retention = config.get_user_max_retention(member)
        max_retention_time = config.get_user_max_time(member)
        for key, conversation in self._conversations.copy().items():
            key_parts: Tuple[str, str, str] = tuple(key.split("-"))
            if len(key_parts) != 3:
                continue
            _, _, key_guild_id = key_parts
            if int(key_guild_id) != guild_id:
                continue

            conversation.cleanup(max_retention, max_retention_time)
            if not conversation.messages:
                del self._conversations[key]
            cleaned += 1
        logger.info("Cleaned %s conversations for guild %s", cleaned, guild_id)
        return cleaned

    def reset_conversation(self, member_id: int, channel_id: int, guild_id: int) -> bool:
        """Reset a conversation to an empty state.

        Args:
            member_id (int): Discord member identifier.
            channel_id (int): Channel identifier where the conversation occurs.
            guild_id (int): Guild identifier for scoping the conversation.

        Returns:
            bool: True if a conversation existed and was reset, False otherwise.
        """
        key = f"{member_id}-{channel_id}-{guild_id}"
        conversation = self._conversations.get(key)
        if not conversation:
            return False
        conversation.reset()
        logger.debug("Reset conversation for member %s in channel %s (guild %s)", member_id, channel_id, guild_id)
        return True

    def get_conversation_count(self, guild_id: Optional[int] = None) -> int:
        """Get total conversation count, optionally filtered by guild.

        Args:
            guild_id (Optional[int], optional): Guild identifier to filter by. Defaults to None.

        Returns:
            int: Number of tracked conversations.
        """
        if guild_id is None:
            return len(self._conversations)

        count = 0
        for key in self._conversations:
            key_parts: Tuple[str, str, str] = tuple(key.split("-"))
            if len(key_parts) != 3:
                continue
            if int(key_parts[2]) == guild_id:
                count += 1
        return count

    def clear_guild_conversations(self, guild_id: int) -> int:
        """Remove all conversations for a specific guild.

        Args:
            guild_id (int): Guild identifier whose conversations should be removed.

        Returns:
            int: Number of conversations removed.
        """
        removed = 0
        for key in list(self._conversations):
            key_parts: Tuple[str, str, str] = tuple(key.split("-"))
            if len(key_parts) != 3:
                continue
            if int(key_parts[2]) != guild_id:
                continue
            del self._conversations[key]
            removed += 1
        logger.info("Cleared %s conversations for guild %s", removed, guild_id)
        return removed
