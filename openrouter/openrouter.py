import logging
from typing import Any, Dict, List, Literal, Optional

import discord
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.config import Config

from cogchain.interfaces import ChainProvider
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
        )

        self.openrouter_config = OpenRouterConfig()
        self.health_monitor = HealthMonitor(
            bot=self.bot,
            config=self.openrouter_config,
            base_url=self.openrouter_config.base_url,
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

            self.health_monitor.base_url = self.openrouter_config.base_url
            self.health_monitor.health_loop.change_interval(seconds=self.openrouter_config.health_check_interval)

            # Prime guild configs
            for guild in self.bot.guilds:
                await self.get_guild_config(guild.id)

            # Initial health check & discovery
            healthy, models = await self.health_monitor.check_health()
            self.openrouter_config.update_health(healthy, models)
            log.debug(
                "Initial OpenRouter health check for %s: healthy=%s (%s models)",
                self.openrouter_config.base_url,
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

    def _select_model_with_fallback(
        self,
        preferred_model: str,
        fallback_model: str,
        available_models: List[str],
    ) -> str:
        if not available_models:
            return preferred_model

        resolved_preferred = resolve_model_name(preferred_model, available_models) or preferred_model
        if resolved_preferred in available_models:
            return resolved_preferred

        resolved_fallback = resolve_model_name(fallback_model, available_models) or fallback_model
        if resolved_fallback in available_models:
            log.warning("Preferred model '%s' unavailable; falling back to '%s'", preferred_model, resolved_fallback)
            return resolved_fallback

        selected = select_default_chat_model(available_models) or available_models[0]
        log.warning(
            "Neither preferred model '%s' nor fallback '%s' available; using '%s'",
            preferred_model,
            fallback_model,
            selected,
        )
        return selected

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

        preferred_model = guild_config.get_user_model(member, available_models)
        model = self._select_model_with_fallback(preferred_model, guild_config.chat_fallback, available_models)

        langchain_messages = self._build_messages(messages)
        filtered_kwargs = self._filter_chat_kwargs(**kwargs)

        async def invoke_chat(selected_model: str) -> str:
            llm = ChatOpenAI(
                base_url=self.openrouter_config.base_url,
                api_key=self.openrouter_config.api_key,
                model=selected_model,
                default_headers=self._build_headers(),
                **filtered_kwargs,
            )
            response = await llm.ainvoke(langchain_messages)
            return str(response.content)

        try:
            return await invoke_chat(model)
        except Exception as exc:  # noqa: BLE001
            if model != guild_config.chat_fallback:
                retry_model = self._select_model_with_fallback(
                    guild_config.chat_fallback,
                    guild_config.chat_fallback,
                    available_models,
                )
                if retry_model != model:
                    try:
                        log.warning("Chat failed for model '%s', retrying with '%s'", model, retry_model)
                        return await invoke_chat(retry_model)
                    except Exception as retry_exc:  # noqa: BLE001
                        log.warning(
                            "Fallback chat failed for guild %s model=%s: %s", guild.id, retry_model, retry_exc
                        )
                        raise commands.UserFeedbackCheckFailure(
                            format_openrouter_error(
                                retry_exc,
                                model=retry_model,
                                endpoint=self.openrouter_config.base_url,
                            )
                        ) from retry_exc

            log.exception("Unexpected OpenRouter chat error for guild %s model=%s", guild.id, model)
            raise commands.UserFeedbackCheckFailure(
                format_openrouter_error(exc, model=model, endpoint=self.openrouter_config.base_url)
            ) from exc

    async def embed(self, text: str, guild: discord.Guild, **kwargs: Any) -> List[float]:
        self._ensure_api_key()
        guild_config = await self.get_guild_config(guild.id)
        available_models = self.openrouter_config.available_models

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

        async def invoke_embed(selected_model: str) -> List[float]:
            embedder = OpenAIEmbeddings(
                model=selected_model,
                base_url=self.openrouter_config.base_url,
                api_key=self.openrouter_config.api_key,
                default_headers=self._build_headers(),
            )
            return await embedder.aembed_query(text)

        try:
            return await invoke_embed(model)
        except Exception as exc:  # noqa: BLE001
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
                            "Embedding failed for model '%s', retrying with fallback '%s'",
                            model,
                            retry_model,
                        )
                        return await invoke_embed(retry_model)
                    except Exception as retry_exc:  # noqa: BLE001
                        log.warning(
                            "Fallback embed failed for guild %s model=%s: %s",
                            guild.id,
                            retry_model,
                            retry_exc,
                        )
                        raise commands.UserFeedbackCheckFailure(
                            format_openrouter_error(
                                retry_exc,
                                model=retry_model,
                                endpoint=self.openrouter_config.base_url,
                            )
                        ) from retry_exc

            log.exception("Unexpected OpenRouter embed error for guild %s model=%s", guild.id, model)
            raise commands.UserFeedbackCheckFailure(
                format_openrouter_error(exc, model=model, endpoint=self.openrouter_config.base_url)
            ) from exc

    async def get_chat_llm(
        self,
        guild_id: int,
        member_id: Optional[int] = None,
        model: Optional[str] = None,
    ) -> ChatOpenAI:
        self._ensure_api_key()
        if model:
            try:
                return ChatOpenAI(
                    base_url=self.openrouter_config.base_url,
                    api_key=self.openrouter_config.api_key,
                    model=model,
                    default_headers=self._build_headers(),
                )
            except Exception as exc:  # noqa: BLE001
                raise commands.UserFeedbackCheckFailure(
                    f"Failed to initialize OpenRouter chat model '{model}': {exc}"
                ) from exc

        guild_config = await self.get_guild_config(guild_id)
        available_models = self.openrouter_config.available_models

        member = None
        if member_id:
            guild = self.bot.get_guild(guild_id)
            if guild:
                member = guild.get_member(member_id)

        preferred_model = guild_config.get_user_model(member, available_models)
        selected = self._select_model_with_fallback(
            preferred_model,
            guild_config.chat_fallback,
            available_models,
        )

        if available_models and selected not in available_models:
            log.warning(
                "Selected model '%s' not in available models for guild %s; using anyway",
                selected,
                guild_id,
            )

        try:
            return ChatOpenAI(
                base_url=self.openrouter_config.base_url,
                api_key=self.openrouter_config.api_key,
                model=selected,
                default_headers=self._build_headers(),
            )
        except Exception as exc:  # noqa: BLE001
            raise commands.UserFeedbackCheckFailure(
                f"Failed to initialize OpenRouter chat model '{selected}': {exc}"
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
            name="Endpoint",
            value=self.openrouter_config.base_url,
            inline=False,
        )
        embed.add_field(
            name="Health Check",
            value=(
                f"Enabled: {self.openrouter_config.health_check_enabled}\n"
                f"Interval: {self.openrouter_config.health_check_interval}s\n"
                f"Status: {'✅ Healthy' if self.openrouter_config.endpoint_healthy else '❌ Unhealthy'}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Models",
            value=(
                f"Chat: {guild_config.chat_model}\n"
                f"Embed: {guild_config.embed_model}\n"
                f"Chat Fallback: {guild_config.chat_fallback}\n"
                f"Embed Fallback: {guild_config.embed_fallback}"
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
    @discord.app_commands.autocomplete(model=model_autocomplete)
    async def set_chat_fallback(self, ctx: commands.Context, model: str):
        """Set the fallback chat model."""
        await self.config.guild(ctx.guild).chat_fallback.set(model)
        await ctx.send(f"Chat fallback model set to: {model}")

    @openrouter_config_group.command(name="embedfallback")
    @discord.app_commands.autocomplete(model=model_autocomplete)
    async def set_embed_fallback(self, ctx: commands.Context, model: str):
        """Set the fallback embedding model."""
        await self.config.guild(ctx.guild).embed_fallback.set(model)
        await ctx.send(f"Embed fallback model set to: {model}")

    @openrouter_config_group.command(name="listmodels")
    async def list_models(self, ctx: commands.Context):
        """List available models from OpenRouter."""
        try:
            models = await self.health_monitor.discover_models()
            self.openrouter_config.update_health(bool(models), models)
            await self.save_openrouter_config()
            if not models:
                await ctx.send("No models found or unable to fetch models.")
                return

            embed = discord.Embed(
                title="Available OpenRouter Models",
                description="\n".join(f"- {model}" for model in models),
                color=discord.Color.green(),
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
