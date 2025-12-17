"""
Abstract interfaces for language model providers and vector storage backends.

Error handling:
- Implementations should raise commands.UserFeedbackCheckFailure for user-facing errors.
- Use standard exceptions (ValueError, ConnectionError, etc.) for internal failures.
- Log provider and storage errors with the logging module for observability.
- Translate provider-specific errors (OpenAI, Ollama, etc.) into standard exceptions to keep callers consistent.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Union

import discord

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
    ) -> Any:
        """Get a bindable LangChain chat model instance for advanced workflows.

        This method returns the underlying LangChain chat model object that can be
        used with .bind_tools() for tool calling and agentic execution patterns.

        Args:
            guild_id: Guild identifier for configuration lookup.
            member_id: Optional member ID for role-based model overrides.

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
        """
        raise NotImplementedError


class ChainStore(ABC):
    """Interface for vector storage backends (e.g., Qdrant)."""

    @abstractmethod
    async def add_embedding(
        self,
        guild: discord.Guild,
        name: str,
        text: str,
        embedding: List[float],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Store or upsert an embedding with optional metadata for a guild namespace.

        Args:
            guild: Guild context for multi-tenant storage isolation.
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
        query_embedding: List[float],
        top_n: int = 3,
        min_score: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Perform similarity search for embeddings using cosine distance.

        Args:
            guild: Guild context for multi-tenant storage isolation.
            query_embedding: Vector representation to compare against stored embeddings.
            top_n: Maximum number of results to return.
            min_score: Optional minimum similarity score threshold for filtering.
        Returns:
            List of result dictionaries containing name, text, score, dimensions, and metadata keys.
        Raises:
            NotImplementedError: If the store does not implement this interface.
        """
        raise NotImplementedError
