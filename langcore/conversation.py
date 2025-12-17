import asyncio
import logging
from typing import Callable, Dict, List, Optional, Tuple

import discord
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    ToolMessage,
    convert_to_messages,
    convert_to_openai_messages,
)

from .abc import ChainProvider
from .models import Conversation, GuildConfig

log = logging.getLogger("red.tin.langcore.conversation")


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
            log.debug("Created new conversation for member %s in channel %s (guild %s)", member_id, channel_id, guild_id)
        return self._conversations.setdefault(key, Conversation())

    async def agent_chat(
        self,
        key: Tuple[int, int, int],  # (member_id, channel_id, guild_id)
        provider: "ChainProvider",  # Import from .abc
        functions: List[dict],  # Tool schemas from ChainHub
        callbacks: Dict[str, Callable],  # Function name -> callable mapping
        guild_id: int,
        member_id: int,
        config: GuildConfig,
    ) -> str:
        """Execute an agent chat with tool calling support.

        Args:
            key: Conversation identifier tuple (member_id, channel_id, guild_id).
            provider: ChainProvider instance for LLM access.
            functions: List of tool schemas (JSON format) to bind to the LLM.
            callbacks: Mapping of function names to callable implementations.
            guild_id: Guild identifier for provider configuration.
            member_id: Member identifier for role-based model selection.
            config: Guild configuration for retention policies.

        Returns:
            Final AI response content as a string.

        Raises:
            Exception: If LLM invocation or tool execution fails.
        """
        try:
            # Get conversation
            conversation_key = f"{key[0]}-{key[1]}-{key[2]}"
            conversation = self._conversations.get(conversation_key)
            if not conversation:
                log.warning("No conversation found for key %s", conversation_key)
                return "No conversation context available."

            # Convert dict messages to LangChain BaseMessage objects
            try:
                messages: List[BaseMessage] = convert_to_messages(conversation.messages)
            except Exception as e:
                log.error("Failed to convert messages to BaseMessage format: %s", e)
                raise

            # Get LLM instance from provider
            try:
                llm = await provider.get_chat_llm(guild_id=guild_id, member_id=member_id)
            except Exception as e:
                log.error("Failed to get chat LLM from provider: %s", e)
                raise

            max_iterations = 5
            iteration = 0

            while iteration < max_iterations:
                iteration += 1
                log.debug("Agent iteration %d/%d", iteration, max_iterations)

                # Bind tools to LLM if functions are provided
                if functions:
                    bound_llm = llm.bind_tools(functions)
                else:
                    bound_llm = llm

                # Invoke LLM
                try:
                    ai_msg: AIMessage = await bound_llm.ainvoke(messages)
                except Exception as e:
                    log.error("LLM invocation failed at iteration %d: %s", iteration, e)
                    raise

                # Append AI message to conversation
                messages.append(ai_msg)

                # Check for tool calls
                if not ai_msg.tool_calls:
                    log.debug("No tool calls in response, ending agent loop")
                    break

                log.debug("Processing %d tool calls", len(ai_msg.tool_calls))

                # Execute each tool call
                for tool_call in ai_msg.tool_calls:
                    tool_name = tool_call["name"]
                    tool_args = tool_call["args"]
                    tool_id = tool_call["id"]

                    # Get callback function
                    callback = callbacks.get(tool_name)
                    if not callback:
                        log.warning("No callback found for tool %s", tool_name)
                        tool_result = f"Error: Tool '{tool_name}' not found"
                    else:
                        # Execute callback
                        try:
                            result = (
                                await callback(**tool_args)
                                if asyncio.iscoroutinefunction(callback)
                                else callback(**tool_args)
                            )
                            # Convert result to string for conversation storage
                            tool_result = str(result)
                        except Exception as e:
                            log.error("Tool %s execution failed: %s", tool_name, e)
                            tool_result = f"Error executing {tool_name}: {str(e)}"

                    # Append ToolMessage
                    messages.append(ToolMessage(content=tool_result, tool_call_id=tool_id))

            # Check if we hit max iterations
            if iteration >= max_iterations:
                log.warning("Agent loop reached max iterations (%d)", max_iterations)

            # Convert BaseMessage list back to OpenAI dict format
            try:
                updated_messages = convert_to_openai_messages(messages)
            except Exception as e:
                log.error("Failed to convert messages back to dict format: %s", e)
                raise

            # Update conversation with new messages
            conversation.messages = updated_messages
            conversation.refresh()

            # Apply retention policies
            conversation.cleanup(config.max_retention, config.max_retention_time)

            # Get the last AI message content
            final_response = ""
            for msg in reversed(messages):
                if isinstance(msg, AIMessage):
                    final_response = str(msg.content) if msg.content else ""
                    break

            if not final_response:
                log.warning("No AI response content found in agent loop")
                final_response = "I apologize, but I couldn't generate a response."

            return final_response
        except Exception:
            log.exception("Agent chat failed for key %s", key)
            # Don't modify conversation state on error
            raise

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
        log.info("Cleaned %s conversations for guild %s", cleaned, guild_id)
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
        log.debug("Reset conversation for member %s in channel %s (guild %s)", member_id, channel_id, guild_id)
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
        log.info("Cleared %s conversations for guild %s", removed, guild_id)
        return removed
