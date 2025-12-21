import logging
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
    """Wrapper that retries ChatOllama calls across configured fallback models."""

    def __init__(
        self,
        *,
        base_url: str,
        candidates: List[str],
        tools: Optional[Any] = None,
        endpoint: str,
        **kwargs: Any,
    ) -> None:
        if not candidates:
            raise ValueError("No candidate models provided for ChatOllama")

        self.base_url = base_url
        self.candidates = [c for c in candidates if c]
        self.kwargs = kwargs
        self._tools = tools
        self._endpoint = endpoint
        self._current_model = self.candidates[0]
        self._llm = self._build_llm(self._current_model)

    def _build_llm(self, model: str) -> ChatOllama:
        llm = ChatOllama(
            base_url=self.base_url,
            model=model,
            **self.kwargs,
        )
        if self._tools:
            llm = llm.bind_tools(self._tools)
        return llm

    def bind_tools(self, tools: Any) -> "RetryingChatOllama":
        return RetryingChatOllama(
            base_url=self.base_url,
            candidates=self.candidates,
            tools=tools,
            endpoint=self._endpoint,
            **self.kwargs,
        )

    async def ainvoke(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call_with_retries(lambda llm: llm.ainvoke(*args, **kwargs))

    async def _call_with_retries(self, caller: Callable[[ChatOllama], Awaitable[Any]]) -> Any:
        last_exc: Optional[Exception] = None

        for idx, model in enumerate(self.candidates):
            if idx > 0:
                log.warning("Retrying Ollama chat with fallback '%s' (previous=%s)", model, self._current_model)
                self._current_model = model
                self._llm = self._build_llm(model)

            try:
                return await caller(self._llm)
            except ResponseError as exc:
                last_exc = exc
                if not self._should_retry(exc):
                    raise
                continue
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                continue

        if isinstance(last_exc, ResponseError):
            raise commands.UserFeedbackCheckFailure(
                format_ollama_error(
                    last_exc,
                    model=self._current_model,
                    endpoint=self._endpoint,
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
        )

        self.ollama_config = OllamaConfig()
        self.health_monitor = HealthMonitor(
            bot=self.bot,
            config=self.ollama_config,
            endpoint=self.ollama_config.endpoint,
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

            self.health_monitor.endpoint = self.ollama_config.endpoint
            self.health_monitor.health_loop.change_interval(seconds=self.ollama_config.health_check_interval)

            healthy, models = await self.health_monitor.check_health()
            self.ollama_config.update_health(healthy, models)
            log.debug(
                "Initial Ollama health check for %s: healthy=%s (%s models)",
                self.ollama_config.endpoint,
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

        try:
            retrying_llm = RetryingChatOllama(
                base_url=self.ollama_config.endpoint,
                candidates=candidates,
                endpoint=self.ollama_config.endpoint,
                **filtered_kwargs,
            )
            response = await retrying_llm.ainvoke(langchain_messages)
            return str(response.content)
        except ResponseError as exc:
            log.warning("Ollama chat failed for guild %s: %s", guild.id, exc)
            raise commands.UserFeedbackCheckFailure(
                format_ollama_error(
                    exc,
                    model=candidates[0] if candidates else preferred_model,
                    endpoint=self.ollama_config.endpoint,
                )
            ) from exc
        except Exception as exc:  # noqa: BLE001
            log.exception("Unexpected Ollama chat error for guild %s", guild.id)
            raise commands.UserFeedbackCheckFailure(
                format_ollama_error(
                    exc,
                    model=candidates[0] if candidates else preferred_model,
                    endpoint=self.ollama_config.endpoint,
                )
            ) from exc

    async def embed(self, text: str, guild: discord.Guild, **kwargs: Any) -> List[float]:
        guild_config = await self.get_guild_config(guild.id)
        available_models = self.ollama_config.available_models

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

        try:
            embedder = OllamaEmbeddings(
                base_url=self.ollama_config.endpoint,
                model=model,
            )
            return await embedder.aembed_query(text)
        except ResponseError as exc:
            is_not_embed = exc.status_code == 400 and "does not support" in str(exc).lower() and "embed" in str(exc).lower()
            if is_not_embed and available_models:
                embed_candidates = [
                    m for m in available_models if m and is_embedding_model(m) and m != model
                ]
                configured = resolve_model_name(guild_config.embed_model, available_models)
                if configured and configured in embed_candidates:
                    embed_candidates.remove(configured)
                    embed_candidates.insert(0, configured)
                for candidate in embed_candidates[:5]:
                    try:
                        log.warning("Model '%s' does not support embeddings; retrying with '%s'", model, candidate)
                        embedder = OllamaEmbeddings(
                            base_url=self.ollama_config.endpoint,
                            model=candidate,
                        )
                        return await embedder.aembed_query(text)
                    except ResponseError as retry_exc:
                        if retry_exc.status_code == 400 and "does not support" in str(retry_exc).lower():
                            continue
                        raise

            is_not_found = exc.status_code == 404 or "not found" in str(exc).lower()
            if is_not_found and model != guild_config.embed_fallback:
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
                        embedder = OllamaEmbeddings(
                            base_url=self.ollama_config.endpoint,
                            model=retry_model,
                        )
                        return await embedder.aembed_query(text)
                    except ResponseError as retry_exc:
                        log.warning("Ollama embed failed for guild %s model=%s: %s", guild.id, retry_model, retry_exc)
                        raise commands.UserFeedbackCheckFailure(
                            format_ollama_error(
                                retry_exc,
                                model=retry_model,
                                endpoint=self.ollama_config.endpoint,
                            )
                        ) from retry_exc
                    except Exception as retry_exc:  # noqa: BLE001
                        log.exception("Unexpected Ollama embed error for guild %s model=%s", guild.id, retry_model)
                        raise commands.UserFeedbackCheckFailure(
                            format_ollama_error(
                                retry_exc,
                                model=retry_model,
                                endpoint=self.ollama_config.endpoint,
                            )
                        ) from retry_exc
            log.warning("Ollama embed failed for guild %s model=%s: %s", guild.id, model, exc)
            raise commands.UserFeedbackCheckFailure(
                format_ollama_error(exc, model=model, endpoint=self.ollama_config.endpoint)
            ) from exc
        except Exception as exc:  # noqa: BLE001
            log.exception("Unexpected Ollama embed error for guild %s model=%s", guild.id, model)
            raise commands.UserFeedbackCheckFailure(
                format_ollama_error(exc, model=model, endpoint=self.ollama_config.endpoint)
            ) from exc

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

        # Get member object if member_id provided
        member = None
        if member_id:
            guild = self.bot.get_guild(guild_id)
            if guild:
                member = guild.get_member(member_id)

        # Use same model selection logic as chat()
        preferred_model = model or guild_config.get_user_model(member, available_models)
        fallback_models = guild_config.get_chat_fallbacks()
        candidates = self._build_chat_candidates(preferred_model, fallback_models, available_models)

        # Validate model availability
        if available_models and candidates and candidates[0] not in available_models:
            log.warning(
                "Selected model '%s' not in available models for guild %s; using anyway",
                candidates[0],
                guild_id,
            )

        # Return configured ChatOllama instance
        try:
            return RetryingChatOllama(
                base_url=self.ollama_config.endpoint,
                candidates=candidates,
                endpoint=self.ollama_config.endpoint,
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
        embed.add_field(
            name="Endpoint",
            value=self.ollama_config.endpoint,
            inline=False,
        )
        embed.add_field(
            name="Health Check",
            value=(
                f"Enabled: {self.ollama_config.health_check_enabled}\n"
                f"Interval: {self.ollama_config.health_check_interval}s\n"
                f"Status: {'✅ Healthy' if self.ollama_config.endpoint_healthy else '❌ Unhealthy'}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Models",
            value=(
                f"Chat: {guild_config.chat_model}\n"
                f"Embed: {guild_config.embed_model}\n"
                f"Chat Fallbacks: {', '.join(guild_config.get_chat_fallbacks()) or 'None'}\n"
                f"Embed Fallback: {guild_config.embed_fallback}"
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
        """Set the Ollama endpoint URL."""
        if not url.startswith(("http://", "https://")):
            await ctx.send("Endpoint must start with http:// or https://")
            return

        self.ollama_config.endpoint = url
        await self.config.ollama_config.set(self.ollama_config.model_dump(exclude_defaults=False))
        self.health_monitor.endpoint = url
        await ctx.send(f"Ollama endpoint set to: {url}")

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

    @ollama_config.command(name="listmodels")
    async def list_models(self, ctx: commands.Context):
        """List all available models from Ollama endpoint."""
        try:
            client = AsyncClient(host=self.ollama_config.endpoint)
            response = await client.list()
            models = response.get("models", [])
            model_names = [model.get("model") or model.get("name") for model in models]
            if not model_names:
                await ctx.send("No models found.")
                return

            embed = discord.Embed(
                title="Available Ollama Models",
                description="\n".join(f"- {model}" for model in model_names),
                color=discord.Color.green(),
            )
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
