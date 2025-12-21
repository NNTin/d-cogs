"""
Abstract interfaces for language model providers and vector storage backends.

Error handling:
- Implementations should raise commands.UserFeedbackCheckFailure for user-facing errors.
- Use standard exceptions (ValueError, ConnectionError, etc.) for internal failures.
- Log provider and storage errors with the logging module for observability.
- Translate provider-specific errors (OpenAI, Ollama, etc.) into standard exceptions to keep callers consistent.
"""

"""
MessageHandler Pattern:
- ExtensionCogs implement MessageHandler to provide custom message operations.
- Handlers are registered via langcore_cog.register_message_handler(name, handler).
- Future: Tools can request specific handlers via JSON response fields.
- This enables modular, cog-specific rendering and message lifecycle management.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Union

import discord
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage, convert_to_messages
from redbot.core import commands

from .models import GuildConfig

if TYPE_CHECKING:
    from .langcore import langcore


class ChainProvider(ABC):
    """Interface for large language model providers (e.g., Ollama, OpenAI)."""

    @abstractmethod
    async def chat(
        self,
        messages: List[Dict[str, Any]],
        guild: discord.Guild,
        member: Optional[discord.Member] = None,
        **kwargs: Any,
    ) -> str:
        """Send a chat completion request using guild and member context to select models.

        Args:
            messages: Ordered conversation messages containing role and content keys.
            guild: Guild context used for configuration lookups such as model selection.
            member: Optional member to allow role-based overrides from GuildConfig.
            **kwargs: Provider-specific options (temperature, max_tokens, etc.).
        Returns:
            Assistant response text returned by the LLM provider.
        Raises:
            NotImplementedError: If the provider does not implement this interface.
        """
        raise NotImplementedError

    @abstractmethod
    async def embed(self, text: str, guild: discord.Guild, **kwargs: Any) -> List[float]:
        """Generate an embedding vector for text using the configured embedding model.

        Args:
            text: Content to embed for similarity search.
            guild: Guild context used for model and configuration lookups.
            **kwargs: Provider-specific options for the embedding request.
        Returns:
            Embedding vector represented as a list of floats.
        Raises:
            NotImplementedError: If the provider does not implement this interface.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_chat_llm(
        self,
        guild_id: int,
        member_id: Optional[int] = None,
        model: Optional[str] = None,
    ) -> Any:
        """Get a bindable LangChain chat model instance for advanced workflows.

        This method returns the underlying LangChain chat model object that can be
        used with .bind_tools() for tool calling and agentic execution patterns.

        Args:
            guild_id: Guild identifier for configuration lookup.
            member_id: Optional member ID for role-based model overrides.
            model: Optional model name to use, overriding guild/role configuration.
                If None, uses standard selection logic.

        Returns:
            A LangChain chat model instance (e.g., ChatOllama, ChatOpenAI) that
            supports .bind_tools() and .ainvoke() methods.

        Raises:
            NotImplementedError: If the provider does not implement this interface.

        Example:
            >>> provider = get_provider("ollama")
            >>> llm = await provider.get_chat_llm(guild_id=123, member_id=456)
            >>> bound_llm = llm.bind_tools(tool_schemas)
            >>> response = await bound_llm.ainvoke(messages)
            >>> override_llm = await provider.get_chat_llm(
            ...     guild_id=123,
            ...     member_id=456,
            ...     model="llama3.2:1b",
            ... )
        """
        raise NotImplementedError


class ChainStore(ABC):
    """Interface for vector storage backends (e.g., Qdrant).

    Implementations support embedding storage plus convenience retrieval that pairs
    embedding generation with similarity search. See qdrant cog for reference implementation.
    """

    @abstractmethod
    async def add_embedding(
        self,
        guild: discord.Guild,
        collection: str,
        name: str,
        text: str,
        embedding: List[float],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Store or upsert an embedding with optional metadata for a guild namespace.

        Args:
            guild: Guild context for multi-tenant storage isolation.
            collection: Collection name for namespace isolation (typically cog name).
            name: Unique identifier for the embedding entry.
            text: Original text content associated with the embedding.
            embedding: Vector representation of the text.
            metadata: Optional metadata to persist alongside the embedding.
        Returns:
            True when the embedding is stored successfully.
        Raises:
            NotImplementedError: If the store does not implement this interface.
        """
        raise NotImplementedError

    @abstractmethod
    async def delete_embeddings(
        self,
        guild: discord.Guild,
        collection: str,
        names: List[str],
    ) -> int:
        """Delete stored embeddings by exact name match within a guild collection.

        Args:
            guild: Guild context for multi-tenant storage isolation.
            collection: Collection name (typically the cog name or user-specific namespace).
            names: List of embedding record names to delete. Names must match the stored payload name exactly.
        Returns:
            Count of embeddings deleted (may be 0 if none matched or collection missing).
        Raises:
            NotImplementedError: If the store does not implement this interface.
        """
        raise NotImplementedError

    @abstractmethod
    async def query(
        self,
        guild: discord.Guild,
        collection: str,
        query_embedding: List[float],
        top_n: int = 3,
        min_score: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Perform similarity search for embeddings using cosine distance.

        Args:
            guild: Guild context for multi-tenant storage isolation.
            collection: Collection name for namespace isolation (typically cog name).
            query_embedding: Vector representation to compare against stored embeddings.
            top_n: Maximum number of results to return.
            min_score: Optional minimum similarity score threshold for filtering.
        Returns:
            List of collection-scoped result dictionaries containing name, text, score, dimensions, and metadata keys.
        Raises:
            NotImplementedError: If the store does not implement this interface.
        """
        raise NotImplementedError

    @abstractmethod
    async def retrieve_texts(
        self,
        guild: discord.Guild,
        collection: str,
        query_text: str,
        top_n: int = 3,
        min_score: Optional[float] = None,
        provider: Any = None,
    ) -> List[Dict[str, Any]]:
        """Embed query_text using the provider then search the collection for similar entries.

        Args:
            guild: Guild context for multi-tenant isolation.
            collection: Collection name (often the cog or user-specific namespace).
            query_text: Natural language text to embed and search against stored entries.
            top_n: Maximum number of results to return (default 3).
            min_score: Optional minimum similarity score threshold to filter results.
            provider: ChainProvider instance used to generate the query embedding.
        Returns:
            List of result dicts with keys name, text, score, metadata, and dimensions.
        Raises:
            NotImplementedError: If the store does not implement this interface.
            RuntimeError: Implementations may raise if provider is missing or embedding fails.
        """
        raise NotImplementedError


class MessageHandler(ABC):
    """Interface for custom message handling by ExtensionCogs.

    ExtensionCogs can implement this interface to provide specialized message
    handling capabilities (e.g., rendering diagrams, formatting responses,
    managing message lifecycle). Handlers are registered with langcore and
    can be requested by tools for custom response rendering.

    Example:
        class MermaidMessageHandler(MessageHandler):
            def __init__(self, mermaid_cog):
                self.cog = mermaid_cog

            async def send_text(self, ctx, text):
                return await ctx.send(text)

            async def send_file(self, ctx, file):
                return await ctx.send(file=file)

        # In mermaid cog's on_langcore_cog_add:
        handler = MermaidMessageHandler(self)
        langcore_cog.register_message_handler(self.qualified_name, handler)
    """

    @abstractmethod
    async def send_text(
        self,
        ctx: commands.Context,
        text: str,
        **kwargs: Any,
    ) -> discord.Message:
        """Send a text message to the channel.

        Args:
            ctx: Command context containing channel and guild information.
            text: Text content to send.
            **kwargs: Additional Discord message parameters (embed, view, etc.).

        Returns:
            The sent Discord message object.

        Raises:
            NotImplementedError: If the handler does not implement this method.
        """
        raise NotImplementedError

    @abstractmethod
    async def send_file(
        self,
        ctx: commands.Context,
        file: discord.File,
        content: Optional[str] = None,
        **kwargs: Any,
    ) -> discord.Message:
        """Send a file attachment to the channel.

        Args:
            ctx: Command context containing channel and guild information.
            file: Discord file object to send.
            content: Optional text content to accompany the file.
            **kwargs: Additional Discord message parameters.

        Returns:
            The sent Discord message object.

        Raises:
            NotImplementedError: If the handler does not implement this method.
        """
        raise NotImplementedError

    @abstractmethod
    async def delete_message(
        self,
        ctx: commands.Context,
        message_id: int,
    ) -> None:
        """Delete a message by ID.

        Args:
            ctx: Command context containing channel and guild information.
            message_id: Discord message ID to delete.

        Raises:
            NotImplementedError: If the handler does not implement this method.
            discord.NotFound: If the message doesn't exist.
            discord.Forbidden: If lacking permissions to delete.
        """
        raise NotImplementedError

    @abstractmethod
    async def edit_message(
        self,
        ctx: commands.Context,
        message_id: int,
        content: Optional[str] = None,
        file: Optional[discord.File] = None,
        **kwargs: Any,
    ) -> None:
        """Edit an existing message.

        Args:
            ctx: Command context containing channel and guild information.
            message_id: Discord message ID to edit.
            content: New text content (None to keep unchanged).
            file: New file attachment (None to keep unchanged).
            **kwargs: Additional Discord edit parameters.

        Raises:
            NotImplementedError: If the handler does not implement this method.
            discord.NotFound: If the message doesn't exist.
            discord.Forbidden: If lacking permissions to edit.
        """
        raise NotImplementedError


@dataclass
class ExtensionContext:
    """Context injected into extension cog tool functions.

    Provides safe access to providers, vector stores, and conversation updates without
    requiring direct access to langcore internals.

    Example:
        async def generate_diagram(description: str, ctx: ExtensionContext) -> str:
            provider = ctx.get_provider()
            store = ctx.get_store()
            # ... use provider/store ...
            await ctx.add_to_conversation("Done rendering diagram.")
            return "✅ Diagram created"
    """

    guild_id: int
    channel_id: int
    member_id: int
    langcore: "langcore"
    default_provider: Optional[str] = None

    def get_provider(self, name: Optional[str] = None) -> ChainProvider:
        """Return a registered provider, defaulting to the guild's configured provider."""
        if not self.langcore:
            raise RuntimeError("Langcore reference missing on ExtensionContext.")

        provider_name = name or self.default_provider or getattr(self.langcore, "DEFAULT_PROVIDER_FALLBACK", None) or "ollama"
        provider = self.langcore.get_provider(provider_name)
        if not provider:
            raise RuntimeError(f"Provider '{provider_name}' is not registered with langcore.")
        return provider

    def get_store(self) -> ChainStore:
        """Get the configured ChainStore, raising if none is available."""
        if not self.langcore:
            raise RuntimeError("Langcore reference missing on ExtensionContext.")
        return self.langcore.get_store()

    async def add_to_conversation(
        self,
        content: str,
        role: str = "assistant",
        tool_call_id: Optional[str] = None,
        name: Optional[str] = None,
    ) -> None:
        """Inject content into the active conversation using langcore's helper.

        Args:
            content: Text to add.
            role: Message role (`assistant`, `tool`, or `user`).
            tool_call_id: Optional tool call identifier when role is `tool`.
            name: Optional message name for tool metadata.
        """
        if not self.langcore:
            raise RuntimeError("Langcore reference missing on ExtensionContext.")

        await self.langcore.inject_conversation_content(
            member_id=self.member_id,
            channel_id=self.channel_id,
            guild_id=self.guild_id,
            content=content,
            role=role,
            tool_call_id=tool_call_id,
            name=name,
        )


class SubAgent(ABC):
    """Base class for extension cog sub-agents.

    Provides a shared tool-calling loop for managers like MermaidManager or SpoilarrManager.

    Usage:
        class MermaidManager(SubAgent):
            async def handle_request(self, request: str, ctx: ExtensionContext) -> str:
                provider = ctx.get_provider()
                messages = [
                    {"role": "system", "content": "..."},
                    {"role": "user", "content": request},
                ]
                callbacks = {"render": self.render}
                return await self.run_tool_loop(
                    messages=messages,
                    tools=[{"name": "render"}],
                    callbacks=callbacks,
                    guild_id=ctx.guild_id,
                    member_id=ctx.member_id,
                    provider=provider,
                )
    """

    def __init__(self, extension_cog: Any, langcore_cog: "langcore") -> None:
        self.extension_cog = extension_cog
        self.langcore_cog = langcore_cog
        cog_name = getattr(extension_cog, "qualified_name", type(extension_cog).__name__)
        self.logger = logging.getLogger(f"red.{cog_name}.agent")

    @abstractmethod
    async def handle_request(self, request: str, ctx: ExtensionContext) -> Any:
        """Handle a request forwarded from langcore."""
        raise NotImplementedError

    async def run_tool_loop(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        callbacks: Dict[str, Callable[..., Any]],
        guild_id: int,
        member_id: Optional[int] = None,
        provider: Optional[ChainProvider] = None,
        max_iterations: int = 10,
    ) -> str:
        """Standardized LangChain tool-calling loop for sub-agents."""
        provider_to_use = provider
        if provider_to_use is None:
            try:
                provider_to_use = await self.langcore_cog.get_default_provider(guild_id)  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001
                self.logger.debug("Could not resolve default provider for guild %s: %s", guild_id, exc)
                provider_to_use = None

        if provider_to_use is None:
            raise RuntimeError("No provider available for sub-agent tool loop.")

        llm = await provider_to_use.get_chat_llm(guild_id=guild_id, member_id=member_id)
        lc_messages = convert_to_messages(messages)

        iteration = 0
        while iteration < max_iterations:
            iteration += 1
            bound_llm = llm.bind_tools(tools) if tools else llm
            ai_msg: AIMessage = await bound_llm.ainvoke(lc_messages)
            lc_messages.append(ai_msg)

            if not ai_msg.tool_calls:
                break

            for tool_index, tool_call in enumerate(ai_msg.tool_calls):
                if isinstance(tool_call, dict):
                    tool_name = tool_call.get("name")
                    tool_args = tool_call.get("args")
                    tool_id = tool_call.get("id")
                else:
                    tool_name = getattr(tool_call, "name", None)
                    tool_args = getattr(tool_call, "args", None)
                    tool_id = getattr(tool_call, "id", None)
                    if (tool_name is None or tool_args is None or tool_id is None) and hasattr(tool_call, "get"):
                        tool_name = tool_name or tool_call.get("name")
                        tool_args = tool_args if tool_args is not None else tool_call.get("args")
                        tool_id = tool_id or tool_call.get("id")

                if tool_name is None:
                    self.logger.warning("Tool call missing name (type=%s): %r", type(tool_call), tool_call)
                    tool_name = ""

                if tool_args is None or not isinstance(tool_args, dict):
                    self.logger.warning("Tool %s args expected dict, got %s", tool_name, type(tool_args))
                    tool_args = {}

                if tool_id is None:
                    tool_id = f"tool_call_{iteration}_{tool_index}"

                callback = callbacks.get(tool_name, lambda **_: f"Tool '{tool_name}' not found")

                try:
                    result = (
                        await callback(**tool_args)
                        if asyncio.iscoroutinefunction(callback)
                        else callback(**tool_args)
                    )
                    tool_result = str(result)
                except Exception as exc:  # noqa: BLE001
                    self.logger.error("Tool %s execution failed: %s", tool_name, exc)
                    tool_result = f"Error executing {tool_name}: {exc}"

                lc_messages.append(ToolMessage(content=tool_result, tool_call_id=tool_id))

        if iteration >= max_iterations:
            self.logger.warning("Sub-agent loop reached max iterations (%s)", max_iterations)

        final_content: Any = ""
        for msg in reversed(lc_messages):
            if isinstance(msg, AIMessage):
                final_content = msg.content if msg.content else ""
                break

        return str(final_content).strip()
