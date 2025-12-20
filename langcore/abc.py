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

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Union

import discord
from redbot.core import commands

from .models import GuildConfig


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

    Implementations may optionally provide a retrieve_texts(guild, collection, query_text,
    top_n, min_score, provider) convenience method that combines embedding generation
    with similarity search. See qdrant cog for reference implementation.
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
