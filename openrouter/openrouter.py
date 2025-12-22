import logging
import re
import time
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional

import discord
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.config import Config

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from cogchain.interfaces import ChainProvider, LangcoreProtocol
from .health import HealthMonitor
from .model_utils import (
    is_embedding_model,
    resolve_model_name,
    select_default_chat_model,
    select_default_embed_model,
)
from .models import OpenRouterConfig, OpenRouterGuildConfig
from .utils import format_openrouter_error

RequestType = Literal["discord_deleted_user", "owner", "user", "user_strict"]

log = logging.getLogger("red.tin.openrouter")


class RetryingChatOpenRouter:
    """Wrapper that retries ChatOpenAI calls across endpoints and candidate models."""

    _rate_limit_cooldowns: Dict[str, float] = {}

    def __init__(
        self,
        *,
        endpoints: List[str],
        candidates: List[str],
        api_key: str,
        default_headers: Optional[Dict[str, str]] = None,
        tools: Optional[Any] = None,
        **kwargs: Any,
    ) -> None:
        if not candidates:
            raise ValueError("No candidate models provided for OpenRouter")
        endpoints = [e for e in endpoints if e]
        if not endpoints:
            raise ValueError("No endpoints provided for OpenRouter")

        self.endpoints = endpoints
        self.candidates = [c for c in candidates if c]
        self.api_key = api_key
        self.default_headers = default_headers
        self.kwargs = kwargs
        self._tools = tools
        self._current_endpoint = self.endpoints[0]
        self._current_model = self.candidates[0]
        self._llm = self._build_llm(self._current_endpoint, self._current_model)

    def _build_llm(self, endpoint: str, model: str) -> ChatOpenAI:
        llm = ChatOpenAI(
            base_url=endpoint,
            api_key=self.api_key,
            model=model,
            default_headers=self.default_headers,
            **self.kwargs,
        )
        if self._tools:
            llm = llm.bind_tools(self._tools)
        return llm

    def bind_tools(self, tools: Any) -> "RetryingChatOpenRouter":
        return RetryingChatOpenRouter(
            endpoints=self.endpoints,
            candidates=self.candidates,
            api_key=self.api_key,
            default_headers=self.default_headers,
            tools=tools,
            **self.kwargs,
        )

    async def ainvoke(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call_with_retries(lambda llm: llm.ainvoke(*args, **kwargs))

    async def _call_with_retries(self, caller: Callable[[ChatOpenAI], Awaitable[Any]]) -> Any:
        last_exc: Optional[Exception] = None
        last_endpoint = self._current_endpoint
        last_model = self._current_model

        for endpoint in self.endpoints:
            for idx, model in enumerate(self.candidates):
                if self._is_in_cooldown(endpoint, model):
                    log.info(
                        "Skipping OpenRouter model '%s' on %s due to rate-limit cooldown (%.0fs remaining)",
                        model,
                        endpoint,
                        self._cooldown_seconds_left(endpoint, model),
                    )
                    continue

                if idx > 0 or endpoint != self._current_endpoint or model != self._current_model:
                    log.warning(
                        "Retrying OpenRouter chat with endpoint=%s model=%s (previous endpoint=%s model=%s)",
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
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    last_endpoint = endpoint
                    last_model = model
                    self._track_rate_limit(endpoint, model, exc)
                    if not self._should_retry(exc):
                        raise
                    continue

        if last_exc:
            raise commands.UserFeedbackCheckFailure(
                format_openrouter_error(last_exc, model=last_model, endpoint=last_endpoint)
            ) from last_exc
        raise RuntimeError("RetryingChatOpenRouter exhausted without attempts")

    @staticmethod
    def _status_code(exc: Exception) -> int:
        return getattr(exc, "status_code", None) or getattr(exc, "http_status", None) or 0

    @classmethod
    def _should_retry(cls, exc: Exception) -> bool:
        status = cls._status_code(exc)
        message = str(exc).lower()
        retryable_status = status in (0, 429, 500, 502, 503)
        rate_limited = "rate limit" in message or "overloaded" in message
        not_found = status == 404 or "not found" in message
        if rate_limited or retryable_status:
            return True
        if not_found:
            return True
        return False

    @classmethod
    def _track_rate_limit(cls, endpoint: str, model: str, exc: Exception) -> None:
        try:
            from openai import RateLimitError  # type: ignore
        except Exception:  # noqa: BLE001
            RateLimitError = Exception  # type: ignore[assignment]

        status = cls._status_code(exc)
        message = str(exc).lower()
        if not isinstance(exc, RateLimitError) and status != 429 and "rate limit" not in message:
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


class openrouter(commands.Cog):
    """
    Implements ChainProvider for langcore via OpenRouter.
    """

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(
            self,
            identifier=257263088,
            force_registration=True,
        )
        self.config.register_global(openrouter_config={})
        default_guild = OpenRouterGuildConfig().model_dump(exclude_defaults=False)
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
        self.openrouter_config = OpenRouterConfig()
        self.health_monitor = HealthMonitor(
            bot=self.bot,
            config=self.openrouter_config,
            endpoints=self.openrouter_config.base_urls,
        )
        self.provider = self._build_provider()

    def _build_provider(self) -> ChainProvider:
        """Build a ChainProvider instance bound to this cog."""
        cog = self

        class _OpenRouterChainProvider(ChainProvider):
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
            ) -> ChatOpenAI:
                return await cog.get_chat_llm(guild_id=guild_id, member_id=member_id, model=model)

        return _OpenRouterChainProvider()

    def _refresh_provider(self) -> None:
        self.provider = self._build_provider()

    async def get_guild_config(self, guild_id: int) -> OpenRouterGuildConfig:
        data = await self.config.guild_from_id(guild_id).all()
        try:
            return OpenRouterGuildConfig.model_validate(data)
        except Exception as exc:  # noqa: BLE001
            log.warning("Invalid OpenRouter config for guild %s, using defaults: %s", guild_id, exc)
            default = OpenRouterGuildConfig()
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
                log.exception("Failed to reset OpenRouter config for guild %s", guild_id)
            return default

    async def save_openrouter_config(self) -> bool:
        """Save global OpenRouter configuration."""
        try:
            await self.config.openrouter_config.set(
                self.openrouter_config.model_dump(exclude_defaults=False)
            )
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to save OpenRouter config: %s", exc)
            return False

    async def cog_load(self) -> None:
        """Load config and register provider when available."""
        try:
            stored = await self.config.openrouter_config()
            if stored:
                loaded = OpenRouterConfig.model_validate(stored)
                for key, value in loaded.model_dump(exclude_defaults=False).items():
                    setattr(self.openrouter_config, key, value)
            else:
                await self.config.openrouter_config.set(
                    self.openrouter_config.model_dump(exclude_defaults=False)
                )

            self.health_monitor.endpoints = list(self.openrouter_config.base_urls)
            self.health_monitor.health_loop.change_interval(seconds=self.openrouter_config.health_check_interval)

            # Prime guild configs
            for guild in self.bot.guilds:
                await self.get_guild_config(guild.id)

            # Initial health check & discovery
            results = await self.health_monitor.check_health()
            for endpoint, (healthy, models) in results.items():
                self.openrouter_config.update_health(endpoint, healthy, models)
                log.debug(
                    "Initial OpenRouter health check for %s: healthy=%s (%s models)",
                    endpoint,
                    healthy,
                    len(models),
                )

            if self.openrouter_config.health_check_enabled:
                self.health_monitor.start()

            langcore_cog = self.bot.get_cog("langcore")
            if langcore_cog:
                self._refresh_provider()
                success = langcore_cog.register_provider(self.qualified_name, self.provider)
                if success:
                    log.info("Registered openrouter with existing langcore instance")
        except Exception:  # noqa: BLE001
            log.exception("Failed to initialize OpenRouter cog")

    async def cog_unload(self) -> None:
        self.health_monitor.stop()
        langcore_cog = self.bot.get_cog("langcore")
        if langcore_cog:
            try:
                langcore_cog.unregister_provider(self.qualified_name)
                log.info("Unregistered openrouter provider from langcore on unload.")
            except Exception as exc:  # noqa: BLE001
                log.warning("Failed to unregister openrouter provider on unload: %s", exc)

    @commands.Cog.listener()
    async def on_langcore_cog_add(self, langcore_cog):
        """Register this cog as a ChainProvider when langcore loads."""
        if not isinstance(langcore_cog, LangcoreProtocol):
            return
        self._refresh_provider()
        success = langcore_cog.register_provider(self.qualified_name, self.provider)
        if success:
            log.info("Registered openrouter as ChainProvider with langcore")
        else:
            log.error("Failed to register openrouter with langcore")

    @commands.Cog.listener()
    async def on_langcore_cog_remove(self):
        """Handle langcore cog removal."""
        log.info("Langcore cog removed, openrouter provider registration cleared")

    @commands.Cog.listener()
    async def on_cog_remove(self, cog: commands.Cog):
        if getattr(cog, "qualified_name", "") == "langcore":
            self.provider = None

    def _build_chat_candidates(
        self,
        preferred_model: str,
        fallback_models: List[str],
        available_models: List[str],
    ) -> List[str]:
        candidates: List[str] = []
        seen = set()

        def add_candidate(model_name: Optional[str]) -> None:
            if not model_name or model_name in seen:
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

    def _build_messages(self, messages: List[Dict[str, Any]]) -> List[Any]:
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
        return langchain_messages

    def _filter_chat_kwargs(self, **kwargs: Any) -> Dict[str, Any]:
        chat_model_fields = getattr(ChatOpenAI, "model_fields", None)
        allowed_options = set(chat_model_fields.keys()) if chat_model_fields else set()
        if not allowed_options:
            allowed_options = {"temperature", "max_tokens", "top_p", "frequency_penalty", "presence_penalty"}
        return {k: v for k, v in kwargs.items() if k in allowed_options and v is not None}

    def _build_headers(self) -> Optional[Dict[str, str]]:
        return self.openrouter_config.default_headers or None

    def _ensure_api_key(self) -> None:
        if not self.openrouter_config.has_api_key():
            raise commands.UserFeedbackCheckFailure("OpenRouter API key is not configured.")

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        guild: discord.Guild,
        member: Optional[discord.Member] = None,
        **kwargs: Any,
    ) -> str:
        self._ensure_api_key()
        guild_config = await self.get_guild_config(guild.id)
        available_models = self.openrouter_config.available_models
        fallback_models = guild_config.get_chat_fallbacks()

        preferred_model = guild_config.get_user_model(member, available_models)
        candidates = self._build_chat_candidates(preferred_model, fallback_models, available_models)
        ordered_candidates = self._order_candidates_by_strategy(
            guild.id,
            candidates,
            self._normalize_strategy(guild_config.llm_selection_strategy),
        )
        healthy_endpoints = self.openrouter_config.get_healthy_endpoints()
        endpoints = healthy_endpoints or (self.openrouter_config.base_urls or [self.openrouter_config.base_url])

        langchain_messages = self._build_messages(messages)
        filtered_kwargs = self._filter_chat_kwargs(**kwargs)

        retrying_llm: Optional[RetryingChatOpenRouter] = None
        try:
            retrying_llm = RetryingChatOpenRouter(
                endpoints=endpoints,
                candidates=ordered_candidates,
                api_key=self.openrouter_config.api_key,
                default_headers=self._build_headers(),
                **filtered_kwargs,
            )
            response = await retrying_llm.ainvoke(langchain_messages)
            return str(response.content)
        except commands.UserFeedbackCheckFailure:
            raise
        except Exception as exc:  # noqa: BLE001
            endpoint = (
                getattr(retrying_llm, "_current_endpoint", None)
                or (endpoints[0] if endpoints else self.openrouter_config.base_url)
            )
            model_used = (
                getattr(retrying_llm, "_current_model", None)
                or (ordered_candidates[0] if ordered_candidates else preferred_model)
            )
            log.exception(
                "Unexpected OpenRouter chat error for guild %s endpoint=%s model=%s",
                guild.id,
                endpoint,
                model_used,
            )
            raise commands.UserFeedbackCheckFailure(
                format_openrouter_error(exc, model=model_used, endpoint=endpoint)
            ) from exc

    async def embed(self, text: str, guild: discord.Guild, **kwargs: Any) -> List[float]:
        self._ensure_api_key()
        guild_config = await self.get_guild_config(guild.id)
        available_models = self.openrouter_config.available_models
        healthy_endpoints = self.openrouter_config.get_healthy_endpoints()
        endpoints = healthy_endpoints or (self.openrouter_config.base_urls or [self.openrouter_config.base_url])

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

        async def invoke_embed(endpoint: str, selected_model: str) -> List[float]:
            embedder = OpenAIEmbeddings(
                model=selected_model,
                base_url=endpoint,
                api_key=self.openrouter_config.api_key,
                default_headers=self._build_headers(),
            )
            return await embedder.aembed_query(text)

        last_exc: Optional[Exception] = None
        last_endpoint = endpoints[-1] if endpoints else self.openrouter_config.base_url
        for endpoint in endpoints:
            try:
                return await invoke_embed(endpoint, model)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                last_endpoint = endpoint
                if model != guild_config.embed_fallback:
                    if available_models:
                        retry_model = (
                            resolve_model_name(guild_config.embed_fallback, available_models)
                            or select_default_embed_model(available_models)
                            or available_models[0]
                        )
                    else:
                        retry_model = guild_config.embed_fallback
                    if retry_model != model:
                        try:
                            log.warning(
                                "Embedding failed for model '%s', retrying with fallback '%s' on %s",
                                model,
                                retry_model,
                                endpoint,
                            )
                            return await invoke_embed(endpoint, retry_model)
                        except Exception as retry_exc:  # noqa: BLE001
                            last_exc = retry_exc
                            last_endpoint = endpoint
                            log.warning(
                                "Fallback embed failed for guild %s model=%s endpoint=%s: %s",
                                guild.id,
                                retry_model,
                                endpoint,
                                retry_exc,
                            )
                log.warning(
                    "Embedding failed for guild %s endpoint=%s model=%s: %s", guild.id, endpoint, model, exc
                )
                continue

        if last_exc:
            raise commands.UserFeedbackCheckFailure(
                format_openrouter_error(last_exc, model=model, endpoint=last_endpoint)
            ) from last_exc
        raise commands.UserFeedbackCheckFailure("Failed to embed text on any OpenRouter endpoint.")

    async def get_chat_llm(
        self,
        guild_id: int,
        member_id: Optional[int] = None,
        model: Optional[str] = None,
    ) -> ChatOpenAI:
        self._ensure_api_key()
        guild_config = await self.get_guild_config(guild_id)
        available_models = self.openrouter_config.available_models
        healthy_endpoints = self.openrouter_config.get_healthy_endpoints()
        endpoints = healthy_endpoints or (self.openrouter_config.base_urls or [self.openrouter_config.base_url])

        member = None
        if member_id:
            guild = self.bot.get_guild(guild_id)
            if guild:
                member = guild.get_member(member_id)

        preferred_model = model or guild_config.get_user_model(member, available_models)
        fallback_models = [] if model else guild_config.get_chat_fallbacks()
        candidates = self._build_chat_candidates(preferred_model, fallback_models, available_models)
        ordered_candidates = self._order_candidates_by_strategy(
            guild_id,
            candidates,
            self._normalize_strategy(guild_config.llm_selection_strategy),
        )

        if available_models and ordered_candidates and ordered_candidates[0] not in available_models:
            log.warning(
                "Selected model '%s' not in available models for guild %s; using anyway",
                ordered_candidates[0],
                guild_id,
            )

        try:
            return RetryingChatOpenRouter(
                endpoints=endpoints,
                candidates=ordered_candidates,
                api_key=self.openrouter_config.api_key,
                default_headers=self._build_headers(),
            )
        except Exception as exc:  # noqa: BLE001
            raise commands.UserFeedbackCheckFailure(
                f"Failed to initialize OpenRouter chat model '{ordered_candidates[0] if ordered_candidates else preferred_model}': {exc}"
            ) from exc

    async def red_delete_data_for_user(self, *, requester: RequestType, user_id: int) -> None:
        # No user-specific data is stored locally by this cog.
        return

    # ------------------------------ Commands ------------------------------
    @commands.group(name="openrouter")
    @commands.admin_or_permissions(administrator=True)
    @commands.guild_only()
    async def openrouter_config_group(self, ctx: commands.Context):
        """Manage OpenRouter provider configuration."""
        pass

    @openrouter_config_group.command(name="settings")
    async def view_settings(self, ctx: commands.Context):
        """View current OpenRouter configuration for this server."""
        guild_config = await self.get_guild_config(ctx.guild.id)

        embed = discord.Embed(
            title="OpenRouter Configuration",
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="Endpoints",
            value="\n".join(
                f"{'✅' if self.openrouter_config.endpoint_health.get(endpoint) else '❌'} "
                f"{endpoint} ({len(self.openrouter_config.endpoint_models.get(endpoint, []))} models)"
                for endpoint in self.openrouter_config.base_urls
            )
            or "None configured",
            inline=False,
        )
        embed.add_field(
            name="Health Check",
            value=(
                f"Enabled: {self.openrouter_config.health_check_enabled}\n"
                f"Interval: {self.openrouter_config.health_check_interval}s\n"
                f"Status: {'✅ Healthy' if self.openrouter_config.is_healthy() else '❌ Unhealthy'}"
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
            value=", ".join(self.openrouter_config.available_models)
            if self.openrouter_config.available_models
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

    @openrouter_config_group.command(name="apikey")
    async def set_api_key(self, ctx: commands.Context, api_key: str):
        """Set the OpenRouter API key."""
        self.openrouter_config.api_key = api_key
        await self.save_openrouter_config()
        await ctx.send("OpenRouter API key updated.")

    @openrouter_config_group.command(name="healthcheck")
    async def toggle_health_check(self, ctx: commands.Context, enabled: bool):
        """Enable or disable endpoint health monitoring."""
        self.openrouter_config.health_check_enabled = enabled
        await self.save_openrouter_config()

        if enabled:
            if not self.health_monitor.health_loop.is_running():
                self.health_monitor.start()
            await ctx.send("Health check enabled.")
        else:
            if self.health_monitor.health_loop.is_running():
                self.health_monitor.stop()
            await ctx.send("Health check disabled.")

    @openrouter_config_group.command(name="healthinterval")
    async def set_health_interval(self, ctx: commands.Context, seconds: int):
        """Set health check interval in seconds."""
        if seconds < 10:
            await ctx.send("Interval must be at least 10 seconds.")
            return

        self.openrouter_config.health_check_interval = seconds
        await self.save_openrouter_config()
        self.health_monitor.health_loop.change_interval(seconds=seconds)
        await ctx.send(f"Health check interval set to {seconds} seconds.")

    @openrouter_config_group.group(name="endpoints")
    async def endpoints_group(self, ctx: commands.Context):
        """Manage OpenRouter endpoints."""
        pass

    @endpoints_group.command(name="list")
    async def list_endpoints(self, ctx: commands.Context):
        """List configured OpenRouter endpoints with health status."""
        endpoint_lines = []
        last_check = self.openrouter_config.last_health_check
        last_check_str = (
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_check)) if last_check else "never"
        )
        for endpoint in self.openrouter_config.base_urls:
            health = self.openrouter_config.endpoint_health.get(endpoint)
            status = "✅" if health else ("⏳" if health is None else "❌")
            model_count = len(self.openrouter_config.endpoint_models.get(endpoint, []))
            endpoint_lines.append(f"{status} {endpoint} — {model_count} models")

        description = "\n".join(endpoint_lines) if endpoint_lines else "No endpoints configured."
        embed = discord.Embed(
            title="OpenRouter Endpoints",
            description=description,
            color=discord.Color.blue(),
        )
        embed.add_field(name="Last Check", value=last_check_str, inline=False)
        await ctx.send(embed=embed)

    @endpoints_group.command(name="add")
    async def add_endpoint(self, ctx: commands.Context, url: str):
        """Add a new OpenRouter endpoint."""
        if not url.startswith(("http://", "https://")):
            await ctx.send("Endpoint must start with http:// or https://")
            return

        url = url.rstrip("/")
        if url in self.openrouter_config.base_urls:
            await ctx.send("Endpoint already configured.")
            return

        self.openrouter_config.base_urls.append(url)
        self.openrouter_config.endpoint_health[url] = False
        self.openrouter_config.endpoint_models[url] = self.openrouter_config.endpoint_models.get(url, [])
        self.health_monitor.endpoints = list(self.openrouter_config.base_urls)
        await self.save_openrouter_config()

        results = await self.health_monitor.check_health(url)
        for endpoint, (healthy, models) in results.items():
            self.openrouter_config.update_health(endpoint, healthy, models)
        await self.save_openrouter_config()

        status = "healthy" if self.openrouter_config.endpoint_health.get(url) else "unhealthy"
        await ctx.send(f"Added endpoint: {url} (initial status: {status})")

    @endpoints_group.command(name="remove")
    async def remove_endpoint(self, ctx: commands.Context, url: str):
        """Remove an OpenRouter endpoint."""
        url = url.rstrip("/")
        if url not in self.openrouter_config.base_urls:
            await ctx.send("Endpoint not found.")
            return

        if len(self.openrouter_config.base_urls) == 1:
            await ctx.send("At least one endpoint is required.")
            return

        self.openrouter_config.base_urls = [e for e in self.openrouter_config.base_urls if e != url]
        self.openrouter_config.endpoint_health.pop(url, None)
        self.openrouter_config.endpoint_models.pop(url, None)
        self.health_monitor.endpoints = list(self.openrouter_config.base_urls)
        await self.save_openrouter_config()
        await ctx.send(f"Removed endpoint: {url}")

    @endpoints_group.command(name="priority")
    async def set_endpoint_priority(self, ctx: commands.Context, url: str, position: int):
        """Reorder an endpoint's priority (1 = highest)."""
        url = url.rstrip("/")
        if url not in self.openrouter_config.base_urls:
            await ctx.send("Endpoint not found.")
            return
        if position < 1 or position > len(self.openrouter_config.base_urls):
            await ctx.send(f"Position must be between 1 and {len(self.openrouter_config.base_urls)}.")
            return

        endpoints = [e for e in self.openrouter_config.base_urls if e != url]
        endpoints.insert(position - 1, url)
        self.openrouter_config.base_urls = endpoints
        self.health_monitor.endpoints = list(endpoints)
        await self.save_openrouter_config()
        await ctx.send(f"Moved {url} to position {position}.")

    @endpoints_group.command(name="check")
    async def check_endpoints(self, ctx: commands.Context, url: Optional[str] = None):
        """Run a health check for all endpoints or a specific one."""
        normalized = url.rstrip("/") if url else None
        targets = [normalized] if normalized else list(self.openrouter_config.base_urls)
        results = await self.health_monitor.check_health(normalized)
        for endpoint, (healthy, models) in results.items():
            self.openrouter_config.update_health(endpoint, healthy, models)
        await self.save_openrouter_config()

        lines = []
        for endpoint in targets:
            healthy = self.openrouter_config.endpoint_health.get(endpoint)
            status = "✅" if healthy else "❌"
            model_count = len(self.openrouter_config.endpoint_models.get(endpoint, []))
            lines.append(f"{status} {endpoint} — {model_count} models")
        await ctx.send("\n".join(lines))

    async def model_autocomplete(self, interaction: discord.Interaction, current: str):
        """Autocomplete callback for model selection."""
        models = self.openrouter_config.available_models
        if not models:
            return []

        filtered = [model for model in models if current.lower() in model.lower()]
        return [discord.app_commands.Choice(name=model, value=model) for model in filtered[:25]]

    @openrouter_config_group.command(name="chatmodel")
    @discord.app_commands.autocomplete(model=model_autocomplete)
    async def set_chat_model(self, ctx: commands.Context, model: str):
        """Set the default chat model for this server."""
        await self.config.guild(ctx.guild).chat_model.set(model)
        await ctx.send(f"Chat model set to: {model}")

    @openrouter_config_group.command(name="embedmodel")
    @discord.app_commands.autocomplete(model=model_autocomplete)
    async def set_embed_model(self, ctx: commands.Context, model: str):
        """Set the default embedding model for this server."""
        await self.config.guild(ctx.guild).embed_model.set(model)
        await ctx.send(f"Embed model set to: {model}")

    @openrouter_config_group.command(name="chatfallback")
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

    @openrouter_config_group.command(name="embedfallback")
    @discord.app_commands.autocomplete(model=model_autocomplete)
    async def set_embed_fallback(self, ctx: commands.Context, model: str):
        """Set the fallback embedding model."""
        await self.config.guild(ctx.guild).embed_fallback.set(model)
        await ctx.send(f"Embed fallback model set to: {model}")

    @openrouter_config_group.command(name="strategy")
    async def set_llm_strategy(self, ctx: commands.Context, strategy: str):
        """Set LLM selection strategy (fallback or loadbalancing)."""
        strategy = strategy.lower()
        valid = ["fallback", "loadbalancing"]
        if strategy not in valid:
            await ctx.send(f"Invalid strategy. Choose from: {', '.join(valid)}")
            return

        await self.config.guild(ctx.guild).llm_selection_strategy.set(strategy)
        await ctx.send(f"LLM selection strategy set to: {strategy}")

    @openrouter_config_group.command(name="listmodels")
    async def list_models(self, ctx: commands.Context):
        """List available models from OpenRouter."""
        try:
            models_by_endpoint: Dict[str, List[str]] = {}
            endpoints = self.openrouter_config.base_urls or [self.openrouter_config.base_url]
            for endpoint in endpoints:
                models = await self.health_monitor.discover_models(endpoint)
                models_by_endpoint[endpoint] = models
                self.openrouter_config.update_health(endpoint, bool(models), models)
            await self.save_openrouter_config()
            if not any(models_by_endpoint.values()):
                await ctx.send("No models found or unable to fetch models.")
                return

            embed = discord.Embed(
                title="Available OpenRouter Models",
                color=discord.Color.green(),
            )
            for endpoint, models in models_by_endpoint.items():
                status = "✅" if self.openrouter_config.endpoint_health.get(endpoint) else "❌"
                embed.add_field(
                    name=f"{status} {endpoint}",
                    value="\n".join(f"- {model}" for model in models) if models else "None",
                    inline=False,
                )
            await ctx.send(embed=embed)
        except Exception as exc:  # noqa: BLE001
            await ctx.send(f"Failed to fetch models: {exc}")

    @openrouter_config_group.group(name="roleoverride")
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

    @openrouter_config_group.command(name="toolscope")
    async def set_tool_scope(self, ctx: commands.Context, scope: str):
        """Set tool calling scope (core, extended, all)."""
        valid_scopes = ["core", "extended", "all"]
        if scope not in valid_scopes:
            await ctx.send(f"Invalid scope. Choose from: {', '.join(valid_scopes)}")
            return

        await self.config.guild(ctx.guild).tool_scope.set(scope)
        await ctx.send(f"Tool scope set to: {scope}")
