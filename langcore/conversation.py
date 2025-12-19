import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

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
    """
    Manages conversation lifecycle including creation, retrieval, cleanup, and reset operations.

    Sub-Agent Interface:
    -------------------
    Sub-agents (e.g., MermaidManager) can access and modify conversations using this pattern:

    1. Get the conversation instance:
       conversation = conversation_manager.get_conversation(member_id, channel_id, guild_id)

    2. Acquire the conversation lock for thread-safety:
       async with conversation_manager._get_lock(f"{member_id}-{channel_id}-{guild_id}"):
           # Read conversation history
           messages = conversation.get_messages()

           # Add sub-agent's response
           conversation.add_assistant_message("Generated content here")

           # Optionally add tool messages
           conversation.add_tool_message(
               content="Tool result",
               tool_call_id="call_123",
               name="tool_name",
           )

    3. Upload files via MessageHandler (outside the lock):
       handler = langcore_cog.get_message_handler("mermaid")
       await handler.send_file(ctx, discord_file)

    Important:
    - Always acquire the lock before modifying conversation state
    - Keep lock duration minimal (don't hold during LLM calls or file uploads)
    - Use add_assistant_message() for content the ConversationManager should see
    - Use MessageHandler for content the Discord user should see

    Example: MermaidManager adding diagram syntax to conversation
    async def create_diagram(self, ctx, description, diagram_type, conversation_manager):
        # Generate diagram
        syntax, file = await self.generate_and_render(description, diagram_type)

        # Get conversation and lock
        conversation = conversation_manager.get_conversation(
            ctx.author.id, ctx.channel.id, ctx.guild.id
        )
        lock = conversation_manager.get_conversation_lock(
            ctx.author.id, ctx.channel.id, ctx.guild.id
        )

        # Add syntax to conversation (what ConversationManager sees)
        async with lock:
            conversation.add_assistant_message(f"```mermaid\\n{syntax}\\n```")

        # Upload file to Discord (what user sees)
        handler = self.langcore_cog.get_message_handler("mermaid")
        await handler.send_file(ctx, file, content="Here's your diagram!")
    """

    def __init__(self) -> None:
        self._conversations: Dict[str, Conversation] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    def _get_lock(self, key: str) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

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

    def get_conversation_lock(self, member_id: int, channel_id: int, guild_id: int) -> asyncio.Lock:
        """Get the lock for a specific conversation to ensure thread-safe access.

        Sub-agents should acquire this lock before modifying conversation state.

        Args:
            member_id: Discord member identifier
            channel_id: Channel identifier
            guild_id: Guild identifier

        Returns:
            asyncio.Lock for the conversation

        Example:
            lock = manager.get_conversation_lock(member_id, channel_id, guild_id)
            async with lock:
                conversation.add_assistant_message("content")
        """
        key = f"{member_id}-{channel_id}-{guild_id}"
        return self._get_lock(key)

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
            lock = self._get_lock(conversation_key)
            conversation: Optional[Conversation]
            async with lock:
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

            # todo: Make max_iterations configurable
            max_iterations = 50
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
                async with lock:
                    messages.append(ai_msg)

                # Check for tool calls
                if not ai_msg.tool_calls:
                    log.debug("No tool calls in response, ending agent loop")
                    break

                log.debug("Processing %d tool calls", len(ai_msg.tool_calls))

                # Execute each tool call
                for tool_index, tool_call in enumerate(ai_msg.tool_calls):
                    # LangChain may return ToolCall objects instead of plain dicts.
                    # Prefer attribute access, with a dict fallback.
                    tool_name: Optional[str]
                    tool_args: Any
                    tool_id: Optional[str]

                    if isinstance(tool_call, dict):
                        tool_name = tool_call.get("name")
                        tool_args = tool_call.get("args")
                        tool_id = tool_call.get("id")
                    else:
                        tool_name = getattr(tool_call, "name", None)
                        tool_args = getattr(tool_call, "args", None)
                        tool_id = getattr(tool_call, "id", None)

                        # Some implementations are mapping-like without being dicts.
                        if (tool_name is None or tool_args is None or tool_id is None) and hasattr(tool_call, "get"):
                            tool_name = tool_name or tool_call.get("name")
                            tool_args = tool_args if tool_args is not None else tool_call.get("args")
                            tool_id = tool_id or tool_call.get("id")

                    if tool_name is None:
                        log.warning("Tool call missing name (type=%s): %r", type(tool_call), tool_call)
                        tool_name = ""

                    if tool_args is None:
                        tool_args = {}

                    if not isinstance(tool_args, dict):
                        log.warning(
                            "Tool %s args expected dict, got %s: %r",
                            tool_name,
                            type(tool_args),
                            tool_args,
                        )
                        tool_args = {}

                    if tool_id is None:
                        tool_id = f"tool_call_{iteration}_{tool_index}"

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
                    async with lock:
                        messages.append(ToolMessage(content=tool_result, tool_call_id=tool_id))

            # Check if we hit max iterations
            if iteration >= max_iterations:
                log.warning("Agent loop reached max iterations (%d)", max_iterations)

            # Convert BaseMessage list back to OpenAI dict format
            async with lock:
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

    def reset_channel_conversations(self, channel_id: int, guild_id: int) -> int:
        """Reset all conversations in a specific channel within a guild.

        Args:
            channel_id (int): Channel identifier to filter conversations.
            guild_id (int): Guild identifier to filter conversations.

        Returns:
            int: Number of conversations reset.

        Example:
            >>> manager.reset_channel_conversations(456, 123)
            3
        """
        reset_count = 0
        for key, _ in self._conversations.copy().items():
            key_parts: Tuple[str, str, str] = tuple(key.split("-"))
            if len(key_parts) != 3:
                continue
            try:
                member_id_str, channel_id_str, guild_id_str = key_parts
                parsed_channel_id = int(channel_id_str)
                parsed_guild_id = int(guild_id_str)
            except ValueError:
                continue
            if parsed_channel_id != channel_id or parsed_guild_id != guild_id:
                continue
            if self.reset_conversation(int(member_id_str), parsed_channel_id, parsed_guild_id):
                reset_count += 1
        log.info("Reset %d conversations for channel %s in guild %s", reset_count, channel_id, guild_id)
        return reset_count
