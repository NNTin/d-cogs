import logging
import re
import time
from typing import Any, Dict, List, Literal, Optional, Callable, Awaitable

import discord
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.config import Config

from cogchain.interfaces import ChainProvider
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_ollama import ChatOllama, OllamaEmbeddings
from ollama import AsyncClient, ResponseError

from .health import HealthMonitor
from .model_utils import (
    is_embedding_model,
    resolve_model_name,
    select_default_chat_model,
    select_default_embed_model,
)
from .models import OllamaConfig, OllamaGuildConfig
from .utils import format_ollama_error

RequestType = Literal["discord_deleted_user", "owner", "user", "user_strict"]

log = logging.getLogger("red.tin.ollama")


class RetryingChatOllama:
    """Wrapper that retries ChatOllama calls across configured endpoints and fallback models."""

    _rate_limit_cooldowns: Dict[str, float] = {}

    def __init__(
        self,
        *,
        endpoints: List[str],
        candidates: List[str],
        tools: Optional[Any] = None,
        **kwargs: Any,
    ) -> None:
        if not candidates:
            raise ValueError("No candidate models provided for ChatOllama")
        endpoints = [e for e in endpoints if e]
        if not endpoints:
            raise ValueError("No endpoints provided for ChatOllama")

        self.endpoints = endpoints
        self.candidates = [c for c in candidates if c]
        self.kwargs = kwargs
        self._tools = tools
        self._current_endpoint = self.endpoints[0]
        self._current_model = self.candidates[0]
        self._llm = self._build_llm(self._current_endpoint, self._current_model)

    def _build_llm(self, endpoint: str, model: str) -> ChatOllama:
        llm = ChatOllama(
            base_url=endpoint,
            model=model,
            **self.kwargs,
        )
        if self._tools:
            llm = llm.bind_tools(self._tools)
        return llm

    def bind_tools(self, tools: Any) -> "RetryingChatOllama":
        return RetryingChatOllama(
            endpoints=self.endpoints,
            candidates=self.candidates,
            tools=tools,
            **self.kwargs,
        )

    async def ainvoke(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call_with_retries(lambda llm: llm.ainvoke(*args, **kwargs))

    async def _call_with_retries(self, caller: Callable[[ChatOllama], Awaitable[Any]]) -> Any:
        last_exc: Optional[Exception] = None
        last_endpoint = self._current_endpoint
        last_model = self._current_model

        for endpoint in self.endpoints:
            for idx, model in enumerate(self.candidates):
                if self._is_in_cooldown(endpoint, model):
                    log.info(
                        "Skipping Ollama model '%s' on %s due to rate-limit cooldown (%.0fs remaining)",
                        model,
                        endpoint,
                        self._cooldown_seconds_left(endpoint, model),
                    )
                    continue

                if idx > 0 or endpoint != self._current_endpoint or model != self._current_model:
                    log.warning(
                        "Retrying Ollama chat with endpoint=%s model=%s (previous endpoint=%s model=%s)",
                        endpoint,
                        model,
                        self._current_endpoint,
                        self._current_model,
                    )
                    self._current_endpoint = endpoint
                    self._current_model = model
                    self._llm = self._build_llm(endpoint, model)

                try:
                    return await caller(self._llm)
                except ResponseError as exc:
                    last_exc = exc
                    last_endpoint = endpoint
                    last_model = model
                    self._track_rate_limit(endpoint, model, exc)
                    if not self._should_retry(exc):
                        raise
                    continue
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    last_endpoint = endpoint
                    last_model = model
                    continue

        if isinstance(last_exc, ResponseError):
            raise commands.UserFeedbackCheckFailure(
                format_ollama_error(
                    last_exc,
                    model=last_model,
                    endpoint=last_endpoint,
                )
            ) from last_exc

        if last_exc:
            raise last_exc
        raise RuntimeError("RetryingChatOllama exhausted without attempts")

    @staticmethod
    def _should_retry(exc: ResponseError) -> bool:
        status = exc.status_code or 0
        message = str(exc).lower()
        if status in (429, 500, 503):
            return True
        if "rate limit" in message:
            return True
        if status == 404 or "not found" in message:
            return True
        if status == 400 and "does not support chat" in message:
            return True
        return False

    @classmethod
    def _track_rate_limit(cls, endpoint: str, model: str, exc: ResponseError) -> None:
        status = exc.status_code or 0
        if status != 429 and "rate limit" not in str(exc).lower():
            return

        cooldown_seconds = cls._extract_retry_after(str(exc)) or 300
        cls._rate_limit_cooldowns[f"{endpoint}:{model}"] = time.monotonic() + cooldown_seconds
        log.warning(
            "Model '%s' on %s hit rate limit; cooling down for %ss",
            model,
            endpoint,
            int(cooldown_seconds),
        )

    @staticmethod
    def _extract_retry_after(message: str) -> Optional[int]:
        # Try to parse phrases like "retry in 12s" or "retry after 120"
        match = re.search(r"retry\s+(?:in|after)\s+(\d+)", message, re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return None
        match_seconds = re.search(r"(\d+)\s*seconds", message, re.IGNORECASE)
        if match_seconds:
            try:
                return int(match_seconds.group(1))
            except ValueError:
                return None
        match_short = re.search(r"(\d+)\s*s", message, re.IGNORECASE)
        if match_short:
            try:
                return int(match_short.group(1))
            except ValueError:
                return None
        return None

    @classmethod
    def _is_in_cooldown(cls, endpoint: str, model: str) -> bool:
        now = time.monotonic()
        expires = cls._rate_limit_cooldowns.get(f"{endpoint}:{model}", 0)
        return expires > now

    @classmethod
    def _cooldown_seconds_left(cls, endpoint: str, model: str) -> float:
        now = time.monotonic()
        expires = cls._rate_limit_cooldowns.get(f"{endpoint}:{model}", 0)
        return max(0.0, expires - now)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._llm, item)


class ollama(commands.Cog):
    """
    implements ChainProvider for langcore
    """

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(
            self,
            identifier=257263088,
            force_registration=True,
        )
        self.config.register_global(ollama_config={})
        default_guild = OllamaGuildConfig().model_dump(exclude_defaults=False)
        self.config.register_guild(
            chat_model=default_guild["chat_model"],
            embed_model=default_guild["embed_model"],
            chat_fallback=default_guild["chat_fallback"],
            embed_fallback=default_guild["embed_fallback"],
            role_model_overrides=default_guild["role_model_overrides"],
            tool_scope=default_guild["tool_scope"],
            llm_selection_strategy=default_guild["llm_selection_strategy"],
        )

        self._round_robin_state: Dict[int, int] = {}
        self.ollama_config = OllamaConfig()
        self.health_monitor = HealthMonitor(
            bot=self.bot,
            config=self.ollama_config,
            endpoints=self.ollama_config.endpoints,
        )
        self.llm = None
        self.embedder = None
        self.provider = self._build_provider()

    def _build_provider(self) -> ChainProvider:
        """Build a ChainProvider instance bound to this cog."""
        cog = self

        class _OllamaChainProvider(ChainProvider):
            async def chat(
                self,
                messages: List[Dict[str, Any]],
                guild: discord.Guild,
                member: Optional[discord.Member] = None,
                **kwargs: Any,
            ) -> str:
                return await cog.chat(messages=messages, guild=guild, member=member, **kwargs)

            async def embed(self, text: str, guild: discord.Guild, **kwargs: Any) -> List[float]:
                return await cog.embed(text=text, guild=guild, **kwargs)

            async def get_chat_llm(
                self,
                guild_id: int,
                member_id: Optional[int] = None,
                model: Optional[str] = None,
            ) -> ChatOllama:
                """Get a configured ChatOllama instance for tool binding and agentic workflows."""
                return await cog.get_chat_llm(guild_id=guild_id, member_id=member_id, model=model)

        return _OllamaChainProvider()

    def _refresh_provider(self) -> None:
        self.provider = self._build_provider()

    async def get_guild_config(self, guild_id: int) -> OllamaGuildConfig:
        data = await self.config.guild_from_id(guild_id).all()
        try:
            return OllamaGuildConfig.model_validate(data)
        except Exception as exc:  # noqa: BLE001
            log.warning("Invalid Ollama config for guild %s, using defaults: %s", guild_id, exc)
            default = OllamaGuildConfig()
            try:
                guild_conf = self.config.guild_from_id(guild_id)
                default_data = default.model_dump(exclude_defaults=False)
                for key in (
                    "chat_model",
                    "embed_model",
                    "chat_fallback",
                    "embed_fallback",
                    "role_model_overrides",
                    "tool_scope",
                    "llm_selection_strategy",
                ):
                    await getattr(guild_conf, key).set(default_data.get(key))
            except Exception:  # noqa: BLE001
                log.exception("Failed to reset Ollama config for guild %s", guild_id)
            return default

    async def save_ollama_config(self) -> bool:
        """Save global Ollama configuration."""
        try:
            await self.config.ollama_config.set(self.ollama_config.model_dump(exclude_defaults=False))
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to save Ollama config: %s", exc)
            return False

    async def cog_load(self) -> None:
        try:
            stored = await self.config.ollama_config()
            if stored:
                loaded = OllamaConfig.model_validate(stored)
                for key, value in loaded.model_dump(exclude_defaults=False).items():
                    setattr(self.ollama_config, key, value)
            else:
                await self.config.ollama_config.set(self.ollama_config.model_dump(exclude_defaults=False))

            if not self.ollama_config.endpoints:
                self.ollama_config.endpoints = [self.ollama_config.endpoint]

            self.health_monitor.endpoints = list(self.ollama_config.endpoints)
            self.health_monitor.health_loop.change_interval(seconds=self.ollama_config.health_check_interval)

            results = await self.health_monitor.check_health()
            for endpoint, (healthy, models) in results.items():
                self.ollama_config.update_health(endpoint, healthy, models)
                log.debug(
                    "Initial Ollama health check for %s: healthy=%s (%s models)",
                    endpoint,
                    healthy,
                    len(models),
                )

            for guild in self.bot.guilds:
                await self.get_guild_config(guild.id)

            if self.ollama_config.health_check_enabled:
                self.health_monitor.start()

            # Register with langcore if it's already loaded
            langcore_cog = self.bot.get_cog("langcore")
            if langcore_cog:
                self._refresh_provider()
                success = langcore_cog.register_provider(self.qualified_name, self.provider)
                if success:
                    log.info("Registered ollama with existing langcore instance")
        except Exception:  # noqa: BLE001
            log.exception("Failed to initialize Ollama cog health monitoring")

    async def cog_unload(self) -> None:
        self.health_monitor.stop()

    @commands.Cog.listener()
    async def on_langcore_cog_add(self, langcore_cog):
        """Register this cog as a ChainProvider when langcore loads."""
        self._refresh_provider()
        success = langcore_cog.register_provider(self.qualified_name, self.provider)
        if success:
            log.info("Registered ollama as ChainProvider with langcore")
        else:
            log.error("Failed to register ollama with langcore")

    @commands.Cog.listener()
    async def on_langcore_cog_remove(self):
        """Handle langcore cog removal."""
        log.info("LangCore cog removed, provider registration cleared")

    async def red_delete_data_for_user(self, *, requester: RequestType, user_id: int) -> None:
        # TODO: Replace this with the proper end user data removal handling.
        super().red_delete_data_for_user(requester=requester, user_id=user_id)

    def _build_chat_candidates(
        self,
        preferred_model: str,
        fallback_models: List[str],
        available_models: List[str],
    ) -> List[str]:
        candidates: List[str] = []
        seen = set()

        def add_candidate(model_name: Optional[str]) -> None:
            if not model_name:
                return
            if model_name in seen:
                return
            seen.add(model_name)
            candidates.append(model_name)

        resolved_preferred = resolve_model_name(preferred_model, available_models) or preferred_model
        add_candidate(resolved_preferred)

        for fb in fallback_models:
            resolved_fb = resolve_model_name(fb, available_models) or fb
            add_candidate(resolved_fb)

        if available_models:
            default_candidate = select_default_chat_model(available_models) or available_models[0]
            add_candidate(default_candidate)
            for candidate in available_models:
                if candidate and not is_embedding_model(candidate):
                    add_candidate(candidate)

        return candidates or [preferred_model]

    def _order_candidates_by_strategy(
        self,
        guild_id: int,
        candidates: List[str],
        strategy: str,
    ) -> List[str]:
        if self._normalize_strategy(strategy) != "loadbalancing" or not candidates:
            return candidates

        index = self._round_robin_state.get(guild_id, 0)
        index = index % len(candidates)
        self._round_robin_state[guild_id] = (index + 1) % len(candidates)
        return candidates[index:] + candidates[:index]

    @staticmethod
    def _normalize_strategy(strategy: Optional[str]) -> str:
        if strategy in ("fallback", "loadbalancing"):
            return strategy
        return "fallback"

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        guild: discord.Guild,
        member: Optional[discord.Member] = None,
        **kwargs: Any,
    ) -> str:
        guild_config = await self.get_guild_config(guild.id)
        available_models = self.ollama_config.available_models

        preferred_model = guild_config.get_user_model(member, available_models)
        fallback_models = guild_config.get_chat_fallbacks()
        candidates = self._build_chat_candidates(preferred_model, fallback_models, available_models)
        ordered_candidates = self._order_candidates_by_strategy(
            guild.id,
            candidates,
            self._normalize_strategy(guild_config.llm_selection_strategy),
        )
        healthy_endpoints = self.ollama_config.get_healthy_endpoints()
        endpoints = healthy_endpoints or (self.ollama_config.endpoints or [self.ollama_config.endpoint])

        langchain_messages: List[Any] = []
        for message in messages:
            role = (message.get("role") or "").lower()
            content = message.get("content") or ""
            if role == "system":
                langchain_messages.append(SystemMessage(content=content))
            elif role == "assistant":
                langchain_messages.append(AIMessage(content=content))
            else:
                langchain_messages.append(HumanMessage(content=content))

        chat_model_fields = getattr(ChatOllama, "model_fields", None)
        allowed_options = set(chat_model_fields.keys()) if chat_model_fields else {"temperature", "num_predict", "seed"}
        filtered_kwargs = {k: v for k, v in kwargs.items() if k in allowed_options and v is not None}

        retrying_llm: Optional[RetryingChatOllama] = None
        try:
            retrying_llm = RetryingChatOllama(
                endpoints=endpoints,
                candidates=ordered_candidates,
                **filtered_kwargs,
            )
            response = await retrying_llm.ainvoke(langchain_messages)
            return str(response.content)
        except ResponseError as exc:
            log.warning("Ollama chat failed for guild %s: %s", guild.id, exc)
            raise commands.UserFeedbackCheckFailure(
                format_ollama_error(
                    exc,
                    model=ordered_candidates[0] if ordered_candidates else preferred_model,
                    endpoint=getattr(retrying_llm, "_current_endpoint", endpoints[0] if endpoints else ""),
                )
            ) from exc
        except Exception as exc:  # noqa: BLE001
            log.exception("Unexpected Ollama chat error for guild %s", guild.id)
            raise commands.UserFeedbackCheckFailure(
                format_ollama_error(
                    exc,
                    model=ordered_candidates[0] if ordered_candidates else preferred_model,
                    endpoint=getattr(retrying_llm, "_current_endpoint", endpoints[0] if endpoints else ""),
                )
            ) from exc

    async def embed(self, text: str, guild: discord.Guild, **kwargs: Any) -> List[float]:
        guild_config = await self.get_guild_config(guild.id)
        available_models = self.ollama_config.available_models
        healthy_endpoints = self.ollama_config.get_healthy_endpoints()
        endpoints = healthy_endpoints or (self.ollama_config.endpoints or [self.ollama_config.endpoint])

        # Prefer embedding-like models when choosing defaults
        resolved_embed = resolve_model_name(guild_config.embed_model, available_models) or guild_config.embed_model
        resolved_fallback = (
            resolve_model_name(guild_config.embed_fallback, available_models) or guild_config.embed_fallback
        )
        if available_models:
            model = resolved_embed if resolved_embed in available_models else None
            if not model:
                model = resolved_fallback if resolved_fallback in available_models else None
            if not model:
                model = select_default_embed_model(available_models) or available_models[0]
        else:
            model = resolved_embed

        async def _embed_with_endpoint(endpoint: str, selected_model: str) -> List[float]:
            try:
                embedder = OllamaEmbeddings(
                    base_url=endpoint,
                    model=selected_model,
                )
                return await embedder.aembed_query(text)
            except ResponseError as exc:
                is_not_embed = (
                    exc.status_code == 400
                    and "does not support" in str(exc).lower()
                    and "embed" in str(exc).lower()
                )
                if is_not_embed and available_models:
                    embed_candidates = [m for m in available_models if m and is_embedding_model(m) and m != selected_model]
                    configured = resolve_model_name(guild_config.embed_model, available_models)
                    if configured and configured in embed_candidates:
                        embed_candidates.remove(configured)
                        embed_candidates.insert(0, configured)
                    for candidate in embed_candidates[:5]:
                        try:
                            log.warning(
                                "Model '%s' does not support embeddings; retrying with '%s' on %s",
                                selected_model,
                                candidate,
                                endpoint,
                            )
                            embedder = OllamaEmbeddings(
                                base_url=endpoint,
                                model=candidate,
                            )
                            return await embedder.aembed_query(text)
                        except ResponseError as retry_exc:
                            if retry_exc.status_code == 400 and "does not support" in str(retry_exc).lower():
                                continue
                            raise

                is_not_found = exc.status_code == 404 or "not found" in str(exc).lower()
                if is_not_found and selected_model != guild_config.embed_fallback:
                    if available_models:
                        retry_model = (
                            resolve_model_name(guild_config.embed_fallback, available_models)
                            or select_default_embed_model(available_models)
                            or available_models[0]
                        )
                    else:
                        retry_model = guild_config.embed_fallback
                    if retry_model != selected_model:
                        embedder = OllamaEmbeddings(
                            base_url=endpoint,
                            model=retry_model,
                        )
                        return await embedder.aembed_query(text)
                raise

        last_exc: Optional[Exception] = None
        for endpoint in endpoints:
            try:
                return await _embed_with_endpoint(endpoint, model)
            except ResponseError as exc:
                last_exc = exc
                log.warning("Ollama embed failed for guild %s endpoint=%s model=%s: %s", guild.id, endpoint, model, exc)
                continue
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                log.exception(
                    "Unexpected Ollama embed error for guild %s endpoint=%s model=%s",
                    guild.id,
                    endpoint,
                    model,
                )
                continue

        if last_exc:
            raise commands.UserFeedbackCheckFailure(
                format_ollama_error(last_exc, model=model, endpoint=endpoints[-1])
            ) from last_exc
        raise commands.UserFeedbackCheckFailure("Failed to embed text on any Ollama endpoint.")

    async def get_chat_llm(
        self,
        guild_id: int,
        member_id: Optional[int] = None,
        model: Optional[str] = None,
    ) -> ChatOllama:
        """Get a configured ChatOllama instance for the specified guild and member.

        This method performs model selection using the same logic as chat(), including:
        - Guild-specific model configuration
        - Role-based model overrides
        - Fallback model handling
        - Model availability validation

        Args:
            guild_id: Guild identifier for configuration lookup.
            member_id: Optional member ID for role-based model overrides.
            model: Optional model name to override guild/role configuration. When provided,
                skips selection logic and uses this model directly.

        Returns:
            ChatOllama instance ready for tool binding and invocation.

        Raises:
            commands.UserFeedbackCheckFailure: If model selection fails or endpoint unavailable.
        """
        guild_config = await self.get_guild_config(guild_id)
        available_models = self.ollama_config.available_models
        healthy_endpoints = self.ollama_config.get_healthy_endpoints()
        endpoints = healthy_endpoints or (self.ollama_config.endpoints or [self.ollama_config.endpoint])

        # Get member object if member_id provided
        member = None
        if member_id:
            guild = self.bot.get_guild(guild_id)
            if guild:
                member = guild.get_member(member_id)

        # Use same model selection logic as chat()
        preferred_model = model or guild_config.get_user_model(member, available_models)
        fallback_models = [] if model else guild_config.get_chat_fallbacks()
        candidates = self._build_chat_candidates(preferred_model, fallback_models, available_models)
        ordered_candidates = self._order_candidates_by_strategy(
            guild_id,
            candidates,
            self._normalize_strategy(guild_config.llm_selection_strategy),
        )

        # Validate model availability
        if available_models and ordered_candidates and ordered_candidates[0] not in available_models:
            log.warning(
                "Selected model '%s' not in available models for guild %s; using anyway",
                ordered_candidates[0],
                guild_id,
            )

        # Return configured ChatOllama instance
        try:
            return RetryingChatOllama(
                endpoints=endpoints,
                candidates=ordered_candidates,
            )
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to create ChatOllama instance for guild %s: %s", guild_id, exc)
            raise commands.UserFeedbackCheckFailure(f"Failed to initialize chat model: {exc}") from exc

    @commands.group(name="ollama")
    @commands.admin_or_permissions(administrator=True)
    @commands.guild_only()
    async def ollama_config(self, ctx: commands.Context):
        """Manage Ollama provider configuration."""
        pass

    @ollama_config.command(name="settings")
    async def view_settings(self, ctx: commands.Context):
        """View current Ollama configuration for this server."""
        guild_config = await self.get_guild_config(ctx.guild.id)

        embed = discord.Embed(
            title="Ollama Configuration",
            color=discord.Color.blue(),
        )
        endpoint_lines = []
        for endpoint in self.ollama_config.endpoints:
            status = "✅" if self.ollama_config.endpoint_health.get(endpoint) else "❌"
            models_count = len(self.ollama_config.endpoint_models.get(endpoint, []))
            endpoint_lines.append(f"{status} {endpoint} ({models_count} models)")
        embed.add_field(
            name="Endpoints",
            value="\n".join(endpoint_lines) if endpoint_lines else "None configured",
            inline=False,
        )
        embed.add_field(
            name="Health Check",
            value=(
                f"Enabled: {self.ollama_config.health_check_enabled}\n"
                f"Interval: {self.ollama_config.health_check_interval}s\n"
                f"Status: {'✅ Healthy' if self.ollama_config.is_healthy() else '❌ Unhealthy'}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Models",
            value=(
                f"Chat: {guild_config.chat_model}\n"
                f"Embed: {guild_config.embed_model}\n"
                f"Chat Fallbacks: {', '.join(guild_config.get_chat_fallbacks()) or 'None'}\n"
                f"Embed Fallback: {guild_config.embed_fallback}\n"
                f"Strategy: {guild_config.llm_selection_strategy}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Available Models",
            value=", ".join(self.ollama_config.available_models)
            if self.ollama_config.available_models
            else "None discovered",
            inline=False,
        )
        embed.add_field(
            name="Role Overrides",
            value=f"{len(guild_config.role_model_overrides)} configured"
            if guild_config.role_model_overrides
            else "None",
            inline=True,
        )
        embed.add_field(
            name="Tool Scope",
            value=guild_config.tool_scope,
            inline=True,
        )

        await ctx.send(embed=embed)

    @ollama_config.command(name="endpoint")
    async def set_endpoint(self, ctx: commands.Context, url: str):
        """Set the Ollama endpoint URL (deprecated; use endpoints commands)."""
        if not url.startswith(("http://", "https://")):
            await ctx.send("Endpoint must start with http:// or https://")
            return

        self.ollama_config.endpoints = [url]
        self.ollama_config.endpoint_health = {url: self.ollama_config.endpoint_health.get(url, False)}
        self.ollama_config.endpoint_models = {url: self.ollama_config.endpoint_models.get(url, [])}
        await self.config.ollama_config.set(self.ollama_config.model_dump(exclude_defaults=False))
        self.health_monitor.endpoints = [url]
        await ctx.send(f"Ollama endpoint set to: {url}\nNote: this command is deprecated. Prefer `ollama endpoints`.")

    @ollama_config.group(name="endpoints")
    async def endpoints_group(self, ctx: commands.Context):
        """Manage Ollama endpoints."""
        pass

    @endpoints_group.command(name="list")
    async def list_endpoints(self, ctx: commands.Context):
        """List configured Ollama endpoints with health status."""
        endpoint_lines = []
        last_check = self.ollama_config.last_health_check
        last_check_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_check)) if last_check else "never"
        for endpoint in self.ollama_config.endpoints:
            health = self.ollama_config.endpoint_health.get(endpoint)
            status = "✅" if health else ("⏳" if health is None else "❌")
            model_count = len(self.ollama_config.endpoint_models.get(endpoint, []))
            endpoint_lines.append(f"{status} {endpoint} — {model_count} models")

        description = "\n".join(endpoint_lines) if endpoint_lines else "No endpoints configured."
        embed = discord.Embed(
            title="Ollama Endpoints",
            description=description,
            color=discord.Color.blue(),
        )
        embed.add_field(name="Last Check", value=last_check_str, inline=False)
        await ctx.send(embed=embed)

    @endpoints_group.command(name="add")
    async def add_endpoint(self, ctx: commands.Context, url: str):
        """Add a new Ollama endpoint."""
        if not url.startswith(("http://", "https://")):
            await ctx.send("Endpoint must start with http:// or https://")
            return

        if url in self.ollama_config.endpoints:
            await ctx.send("Endpoint already configured.")
            return

        self.ollama_config.endpoints.append(url)
        self.ollama_config.endpoint_health[url] = False
        self.ollama_config.endpoint_models[url] = self.ollama_config.endpoint_models.get(url, [])
        self.health_monitor.endpoints = list(self.ollama_config.endpoints)
        await self.save_ollama_config()

        results = await self.health_monitor.check_health(url)
        for endpoint, (healthy, models) in results.items():
            self.ollama_config.update_health(endpoint, healthy, models)
        await self.save_ollama_config()

        status = "healthy" if self.ollama_config.endpoint_health.get(url) else "unhealthy"
        await ctx.send(f"Added endpoint: {url} (initial status: {status})")

    @endpoints_group.command(name="remove")
    async def remove_endpoint(self, ctx: commands.Context, url: str):
        """Remove an Ollama endpoint."""
        if url not in self.ollama_config.endpoints:
            await ctx.send("Endpoint not found.")
            return

        if len(self.ollama_config.endpoints) == 1:
            await ctx.send("At least one endpoint is required.")
            return

        self.ollama_config.endpoints = [e for e in self.ollama_config.endpoints if e != url]
        self.ollama_config.endpoint_health.pop(url, None)
        self.ollama_config.endpoint_models.pop(url, None)
        self.health_monitor.endpoints = list(self.ollama_config.endpoints)
        await self.save_ollama_config()
        await ctx.send(f"Removed endpoint: {url}")

    @endpoints_group.command(name="check")
    async def check_endpoints(self, ctx: commands.Context, url: Optional[str] = None):
        """Run a health check for all endpoints or a specific one."""
        targets = [url] if url else list(self.ollama_config.endpoints)
        results = await self.health_monitor.check_health(url)
        for endpoint, (healthy, models) in results.items():
            self.ollama_config.update_health(endpoint, healthy, models)
        await self.save_ollama_config()

        lines = []
        for endpoint in targets:
            healthy = self.ollama_config.endpoint_health.get(endpoint)
            status = "✅" if healthy else "❌"
            model_count = len(self.ollama_config.endpoint_models.get(endpoint, []))
            lines.append(f"{status} {endpoint} — {model_count} models")
        await ctx.send("\n".join(lines))

    @endpoints_group.command(name="priority")
    async def set_endpoint_priority(self, ctx: commands.Context, url: str, position: int):
        """Reorder an endpoint's priority (1 = highest)."""
        if url not in self.ollama_config.endpoints:
            await ctx.send("Endpoint not found.")
            return
        if position < 1 or position > len(self.ollama_config.endpoints):
            await ctx.send(f"Position must be between 1 and {len(self.ollama_config.endpoints)}.")
            return

        endpoints = [e for e in self.ollama_config.endpoints if e != url]
        endpoints.insert(position - 1, url)
        self.ollama_config.endpoints = endpoints
        self.health_monitor.endpoints = list(endpoints)
        await self.save_ollama_config()
        await ctx.send(f"Moved {url} to position {position}.")

    @ollama_config.command(name="healthcheck")
    async def toggle_health_check(self, ctx: commands.Context, enabled: bool):
        """Enable or disable endpoint health monitoring."""
        self.ollama_config.health_check_enabled = enabled
        await self.config.ollama_config.set(self.ollama_config.model_dump(exclude_defaults=False))

        if enabled:
            if not self.health_monitor.health_loop.is_running():
                self.health_monitor.start()
            await ctx.send("Health check enabled.")
        else:
            if self.health_monitor.health_loop.is_running():
                self.health_monitor.stop()
            await ctx.send("Health check disabled.")

    @ollama_config.command(name="healthinterval")
    async def set_health_interval(self, ctx: commands.Context, seconds: int):
        """Set health check interval in seconds."""
        if seconds < 10:
            await ctx.send("Interval must be at least 10 seconds.")
            return

        self.ollama_config.health_check_interval = seconds
        await self.config.ollama_config.set(self.ollama_config.model_dump(exclude_defaults=False))
        self.health_monitor.health_loop.change_interval(seconds=seconds)
        await ctx.send(f"Health check interval set to {seconds} seconds.")

    async def model_autocomplete(self, interaction: discord.Interaction, current: str):
        """Autocomplete callback for model selection."""
        models = self.ollama_config.available_models
        if not models:
            return []

        filtered = [model for model in models if current.lower() in model.lower()]
        return [discord.app_commands.Choice(name=model, value=model) for model in filtered[:25]]

    @ollama_config.command(name="chatmodel")
    @discord.app_commands.autocomplete(model=model_autocomplete)
    async def set_chat_model(self, ctx: commands.Context, model: str):
        """Set the default chat model for this server."""
        await self.config.guild(ctx.guild).chat_model.set(model)
        await ctx.send(f"Chat model set to: {model}")

    @ollama_config.command(name="embedmodel")
    @discord.app_commands.autocomplete(model=model_autocomplete)
    async def set_embed_model(self, ctx: commands.Context, model: str):
        """Set the default embedding model for this server."""
        await self.config.guild(ctx.guild).embed_model.set(model)
        await ctx.send(f"Embed model set to: {model}")

    @ollama_config.command(name="chatfallback")
    async def set_chat_fallback(self, ctx: commands.Context, *models: str):
        """Set one or more fallback chat models (priority order)."""
        if not models:
            await ctx.send("Provide at least one fallback model.")
            return

        if len(models) == 1 and "," in models[0]:
            models = tuple(m.strip() for m in models[0].split(",") if m.strip())

        fallbacks = [m for m in models if m]
        await self.config.guild(ctx.guild).chat_fallback.set(fallbacks)
        await ctx.send(f"Chat fallback models set to: {', '.join(fallbacks)}")

    @ollama_config.command(name="embedfallback")
    @discord.app_commands.autocomplete(model=model_autocomplete)
    async def set_embed_fallback(self, ctx: commands.Context, model: str):
        """Set the fallback embedding model."""
        await self.config.guild(ctx.guild).embed_fallback.set(model)
        await ctx.send(f"Embed fallback model set to: {model}")

    @ollama_config.command(name="strategy")
    async def set_llm_strategy(self, ctx: commands.Context, strategy: str):
        """Set LLM selection strategy (fallback or loadbalancing)."""
        strategy = strategy.lower()
        valid = ["fallback", "loadbalancing"]
        if strategy not in valid:
            await ctx.send(f"Invalid strategy. Choose from: {', '.join(valid)}")
            return

        await self.config.guild(ctx.guild).llm_selection_strategy.set(strategy)
        await ctx.send(f"LLM selection strategy set to: {strategy}")

    @ollama_config.command(name="listmodels")
    async def list_models(self, ctx: commands.Context):
        """List all available models from Ollama endpoint."""
        endpoints = self.ollama_config.endpoints or [self.ollama_config.endpoint]
        models_by_endpoint: Dict[str, List[str]] = {}
        try:
            for endpoint in endpoints:
                try:
                    client = AsyncClient(host=endpoint)
                    response = await client.list()
                    models = response.get("models", [])
                    model_names = [model.get("model") or model.get("name") for model in models]
                    models_by_endpoint[endpoint] = model_names
                except Exception as exc:  # noqa: BLE001
                    models_by_endpoint[endpoint] = []
                    log.warning("Failed to fetch models from %s: %s", endpoint, exc)

            if not any(models_by_endpoint.values()):
                await ctx.send("No models found on any endpoint.")
                return

            embed = discord.Embed(
                title="Available Ollama Models",
                color=discord.Color.green(),
            )
            shared: Dict[str, List[str]] = {}
            for endpoint, names in models_by_endpoint.items():
                status = "✅" if self.ollama_config.endpoint_health.get(endpoint) else "❌"
                value = "\n".join(f"- {name}" for name in names) if names else "None"
                embed.add_field(
                    name=f"{status} {endpoint}",
                    value=value,
                    inline=False,
                )
                for name in names:
                    shared.setdefault(name, []).append(endpoint)

            shared_models = [name for name, eps in shared.items() if len(eps) > 1]
            if shared_models:
                embed.add_field(name="Shared Models", value=", ".join(shared_models), inline=False)

            await ctx.send(embed=embed)
        except Exception as exc:  # noqa: BLE001
            await ctx.send(f"Failed to fetch models: {exc}")

    @ollama_config.group(name="roleoverride")
    async def role_override_group(self, ctx: commands.Context):
        """Manage role-based model overrides."""
        pass

    @role_override_group.command(name="add")
    @discord.app_commands.autocomplete(model=model_autocomplete)
    async def role_override_add(
        self,
        ctx: commands.Context,
        role: discord.Role,
        model: str,
    ):
        """Set a model override for a specific role."""
        async with self.config.guild(ctx.guild).role_model_overrides() as overrides:
            overrides[role.id] = model
        await ctx.send(f"Role {role.mention} will use model: {model}")

    @role_override_group.command(name="remove")
    async def role_override_remove(self, ctx: commands.Context, role: discord.Role):
        """Remove a role's model override."""
        async with self.config.guild(ctx.guild).role_model_overrides() as overrides:
            if role.id not in overrides:
                await ctx.send(f"Role {role.mention} has no override.")
                return
            del overrides[role.id]
        await ctx.send(f"Removed model override for {role.mention}")

    @role_override_group.command(name="list")
    async def role_override_list(self, ctx: commands.Context):
        """List all role model overrides."""
        overrides = await self.config.guild(ctx.guild).role_model_overrides()
        if not overrides:
            await ctx.send("No role overrides configured.")
            return

        entries = []
        for role_id, model in overrides.items():
            try:
                role_id_int = int(role_id)
            except (TypeError, ValueError):
                role_id_int = role_id  # type: ignore[assignment]
            role = ctx.guild.get_role(role_id_int) if isinstance(role_id_int, int) else None
            if role:
                entries.append(f"- {role.mention}: `{model}`")
            else:
                entries.append(f"- Unknown Role ({role_id}): `{model}`")

        embed = discord.Embed(
            title="Role Model Overrides",
            description="\n".join(entries),
            color=discord.Color.blue(),
        )
        await ctx.send(embed=embed)

    @ollama_config.command(name="toolscope")
    async def set_tool_scope(self, ctx: commands.Context, scope: str):
        """Set tool calling scope (core, extended, all)."""
        valid_scopes = ["core", "extended", "all"]
        if scope not in valid_scopes:
            await ctx.send(f"Invalid scope. Choose from: {', '.join(valid_scopes)}")
            return

        await self.config.guild(ctx.guild).tool_scope.set(scope)
        await ctx.send(f"Tool scope set to: {scope}")
