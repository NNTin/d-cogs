"""
Shared abstractions for language model providers, vector stores, extension contexts, and message handlers.

These interfaces are imported by multiple cogs to avoid cross-cog imports or getattr() hacks.
Keep implementation details in individual cogs; only contracts live here.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Protocol, runtime_checkable

import discord
from langchain_core.messages import AIMessage, ToolMessage, convert_to_messages
from redbot.core import commands

if TYPE_CHECKING:  # pragma: no cover - type checking only
    from langcore.langcore import langcore as LangcoreCog


class ChainProvider(ABC):
    """Interface for large language model providers (e.g., Ollama, OpenRouter)."""

    @abstractmethod
    async def chat(
        self,
        messages: List[Dict[str, Any]],
        guild: discord.Guild,
        member: Optional[discord.Member] = None,
        **kwargs: Any,
    ) -> str:
        """Send a chat completion request using guild and member context to select models."""
        raise NotImplementedError

    @abstractmethod
    async def embed(self, text: str, guild: discord.Guild, **kwargs: Any) -> List[float]:
        """Generate an embedding vector for text using the configured embedding model."""
        raise NotImplementedError

    @abstractmethod
    async def get_chat_llm(
        self,
        guild_id: int,
        member_id: Optional[int] = None,
        model: Optional[str] = None,
    ) -> Any:
        """Get a bindable LangChain chat model instance for advanced workflows."""
        raise NotImplementedError


class ChainStore(ABC):
    """Interface for vector storage backends (e.g., Qdrant)."""

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
        """Store or upsert an embedding with optional metadata for a guild namespace."""
        raise NotImplementedError

    @abstractmethod
    async def delete_embeddings(
        self,
        guild: discord.Guild,
        collection: str,
        names: List[str],
    ) -> int:
        """Delete stored embeddings by exact name match within a guild collection."""
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
        """Perform similarity search for embeddings using cosine distance."""
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
        """Embed query_text using the provider then search the collection for similar entries."""
        raise NotImplementedError


class MessageHandler(ABC):
    """Interface for custom message handling by ExtensionCogs."""

    @abstractmethod
    async def send_text(
        self,
        ctx: commands.Context,
        text: str,
        **kwargs: Any,
    ) -> discord.Message:
        """Send a text message to the channel."""
        raise NotImplementedError

    @abstractmethod
    async def send_file(
        self,
        ctx: commands.Context,
        file: discord.File,
        content: Optional[str] = None,
        **kwargs: Any,
    ) -> discord.Message:
        """Send a file attachment to the channel."""
        raise NotImplementedError

    @abstractmethod
    async def delete_message(
        self,
        ctx: commands.Context,
        message_id: int,
    ) -> None:
        """Delete a message by ID."""
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
        """Edit an existing message."""
        raise NotImplementedError


@dataclass
class ExtensionContext:
    """Context injected into extension cog tool functions."""

    guild_id: int
    channel_id: int
    member_id: int
    langcore: Optional["LangcoreCog"]
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
        """Inject content into the active conversation using langcore's helper."""
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
    """Base class for extension cog sub-agents."""

    def __init__(self, extension_cog: Any, langcore_cog: "LangcoreCog") -> None:
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
        channel_id: int,
        member_id: Optional[int] = None,
        ctx: Optional[ExtensionContext] = None,
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

        extension_ctx = ctx or ExtensionContext(
            guild_id=guild_id,
            channel_id=channel_id,
            member_id=member_id or 0,
            langcore=self.langcore_cog,
            default_provider=getattr(self.langcore_cog, "DEFAULT_PROVIDER_FALLBACK", None),
        )

        def _is_signature_type_error(exc: TypeError) -> bool:
            msg = str(exc)
            return (
                "unexpected keyword argument" in msg
                or "positional arguments but" in msg
                or "required positional argument" in msg
                or "positional argument" in msg
            )

        def build_wrapper(cb: Callable[..., Any]) -> Callable[..., Any]:
            async def wrapper(**tool_args):
                kw_with_ctx = dict(tool_args)
                kw_with_ctx.setdefault("ctx", extension_ctx)
                try:
                    if asyncio.iscoroutinefunction(cb):
                        return await cb(**kw_with_ctx)
                    return cb(**kw_with_ctx)
                except TypeError as exc:
                    if not _is_signature_type_error(exc):
                        raise
                    self.logger.debug(
                        "Tool %s rejected ExtensionContext injection: %s",
                        getattr(cb, "__name__", cb),
                        exc,
                    )

                kw_with_context = dict(tool_args)
                kw_with_context.update(
                    guild_id=guild_id,
                    channel_id=extension_ctx.channel_id,
                    member_id=extension_ctx.member_id,
                )
                try:
                    if asyncio.iscoroutinefunction(cb):
                        return await cb(**kw_with_context)
                    return cb(**kw_with_context)
                except TypeError as exc:
                    if not _is_signature_type_error(exc):
                        raise
                    self.logger.debug(
                        "Tool %s rejected context kwargs, falling back to raw args: %s",
                        getattr(cb, "__name__", cb),
                        exc,
                    )

                if asyncio.iscoroutinefunction(cb):
                    return await cb(**tool_args)
                return cb(**tool_args)

            return wrapper

        wrapped_callbacks: Dict[str, Callable[..., Any]] = {
            name: build_wrapper(cb) for name, cb in callbacks.items()
        }

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

                callback = wrapped_callbacks.get(tool_name) or callbacks.get(tool_name) or (lambda **_: f"Tool '{tool_name}' not found")

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


__all__ = [
    "ChainProvider",
    "ChainStore",
    "MessageHandler",
    "ExtensionContext",
    "SubAgent",
    "ConversationManagerProtocol",
    "ChainHubProtocol",
    "LangcoreProtocol",
]


class ConversationManagerProtocol(Protocol):
    """Minimal contract for registering/unregistering cog system prompts."""

    def register_cog_system_prompt(self, cog_name: str, prompt: str) -> None:
        ...

    def unregister_cog_system_prompt(self, cog_name: str) -> None:
        ...


class ChainHubProtocol(Protocol):
    """Contract for registering callable tool functions with langcore's hub."""

    def register_function(
        self,
        cog_name: str,
        schema: dict,
        permission_level: str = "user",
    ) -> bool:
        ...

    def unregister_function(self, cog_name: str, function_name: str) -> None:
        ...

    def unregister_cog(self, cog_name: str) -> None:
        ...


@runtime_checkable
class LangcoreProtocol(Protocol):
    """High-level langcore interactions used by extension/provider cogs."""

    DEFAULT_PROVIDER_FALLBACK: Optional[str]
    conversation_manager: ConversationManagerProtocol
    hub: ChainHubProtocol

    def register_message_handler(self, cog_name: str, handler: MessageHandler) -> bool:
        ...

    def unregister_message_handler(self, cog_name: str) -> None:
        ...

    def register_provider(self, name: str, provider: ChainProvider) -> bool:
        ...

    def unregister_provider(self, name: str) -> None:
        ...

    def get_provider(self, name: str) -> Optional[ChainProvider]:
        ...

    def get_providers(self) -> Dict[str, ChainProvider]:
        ...

    def register_chain_store(self, chain_store: ChainStore) -> bool:
        ...

    def unregister_chain_store(self) -> None:
        ...

    def get_store(self) -> ChainStore:
        ...
