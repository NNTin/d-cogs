import logging
from typing import Any, Dict, List, Literal, Optional

import discord
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.config import Config

from langcore.abc import ChainProvider

from .api import OllamaAPIError, OllamaClient, OllamaConnectionError, format_error_message
from .health import HealthMonitor
from .models import OllamaConfig, OllamaGuildConfig

RequestType = Literal["discord_deleted_user", "owner", "user", "user_strict"]

log = logging.getLogger("red.ollama")


class ollama(commands.Cog, ChainProvider):
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
        default_guild = OllamaGuildConfig().model_dump()
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

    async def get_guild_config(self, guild_id: int) -> OllamaGuildConfig:
        data = await self.config.guild_from_id(guild_id).all()
        try:
            return OllamaGuildConfig.model_validate(data)
        except Exception as exc:  # noqa: BLE001
            log.warning("Invalid Ollama config for guild %s, using defaults: %s", guild_id, exc)
            default = OllamaGuildConfig()
            try:
                guild_conf = self.config.guild_from_id(guild_id)
                default_data = default.model_dump()
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

    async def cog_load(self) -> None:
        try:
            stored = await self.config.ollama_config()
            if stored:
                loaded = OllamaConfig.model_validate(stored)
                for key, value in loaded.model_dump().items():
                    setattr(self.ollama_config, key, value)
            else:
                await self.config.ollama_config.set(self.ollama_config.model_dump())

            self.health_monitor.endpoint = self.ollama_config.endpoint
            self.health_monitor.health_loop.change_interval(seconds=self.ollama_config.health_check_interval)

            for guild in self.bot.guilds:
                await self.get_guild_config(guild.id)

            if self.ollama_config.health_check_enabled:
                self.health_monitor.start()
        except Exception:  # noqa: BLE001
            log.exception("Failed to initialize Ollama cog health monitoring")

    async def cog_unload(self) -> None:
        self.health_monitor.stop()

    async def red_delete_data_for_user(self, *, requester: RequestType, user_id: int) -> None:
        # TODO: Replace this with the proper end user data removal handling.
        super().red_delete_data_for_user(requester=requester, user_id=user_id)

    def _select_model_with_fallback(
        self,
        preferred_model: str,
        fallback_model: str,
        available_models: List[str],
    ) -> str:
        if not available_models:
            return preferred_model

        if preferred_model in available_models:
            return preferred_model

        if fallback_model in available_models:
            log.warning("Preferred model '%s' unavailable; falling back to '%s'", preferred_model, fallback_model)
            return fallback_model

        selected = available_models[0]
        log.warning(
            "Neither preferred model '%s' nor fallback '%s' available; using '%s'",
            preferred_model,
            fallback_model,
            selected,
        )
        return selected

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
        model = self._select_model_with_fallback(preferred_model, guild_config.chat_fallback, available_models)

        allowed_options = {"temperature", "num_predict", "frequency_penalty", "presence_penalty", "seed"}
        filtered_kwargs = {k: v for k, v in kwargs.items() if k in allowed_options and v is not None}

        try:
            response = await OllamaClient.chat(
                endpoint=self.ollama_config.endpoint,
                model=model,
                messages=messages,
                **filtered_kwargs,
            )
            return str(response.message.get("content", "") or "")
        except OllamaAPIError as exc:
            response_text = (exc.response_text or "").lower()
            is_not_found = exc.status_code == 404 or "model not found" in response_text
            if is_not_found and model != guild_config.chat_fallback:
                retry_model = self._select_model_with_fallback(
                    guild_config.chat_fallback,
                    guild_config.chat_fallback,
                    available_models,
                )
                if retry_model != model:
                    try:
                        response = await OllamaClient.chat(
                            endpoint=self.ollama_config.endpoint,
                            model=retry_model,
                            messages=messages,
                            **filtered_kwargs,
                        )
                        return str(response.message.get("content", "") or "")
                    except (OllamaConnectionError, OllamaAPIError) as retry_exc:
                        log.warning("Ollama chat failed for guild %s model=%s: %s", guild.id, retry_model, retry_exc)
                        raise commands.UserFeedbackCheckFailure(
                            format_error_message(retry_exc, model=retry_model)
                        ) from retry_exc
                    except Exception as retry_exc:  # noqa: BLE001
                        log.exception("Unexpected Ollama chat error for guild %s model=%s", guild.id, retry_model)
                        raise commands.UserFeedbackCheckFailure(
                            format_error_message(retry_exc, model=retry_model)
                        ) from retry_exc
            log.warning("Ollama chat failed for guild %s model=%s: %s", guild.id, model, exc)
            raise commands.UserFeedbackCheckFailure(format_error_message(exc, model=model)) from exc
        except OllamaConnectionError as exc:
            log.warning("Ollama chat failed for guild %s model=%s: %s", guild.id, model, exc)
            raise commands.UserFeedbackCheckFailure(format_error_message(exc, model=model)) from exc
        except Exception as exc:  # noqa: BLE001
            log.exception("Unexpected Ollama chat error for guild %s model=%s", guild.id, model)
            raise commands.UserFeedbackCheckFailure(format_error_message(exc, model=model)) from exc

    async def embed(self, text: str, guild: discord.Guild, **kwargs: Any) -> List[float]:
        guild_config = await self.get_guild_config(guild.id)
        available_models = self.ollama_config.available_models

        model = self._select_model_with_fallback(
            guild_config.embed_model,
            guild_config.embed_fallback,
            available_models,
        )

        try:
            return await OllamaClient.embed(
                endpoint=self.ollama_config.endpoint,
                model=model,
                text=text,
            )
        except OllamaAPIError as exc:
            response_text = (exc.response_text or "").lower()
            is_not_found = exc.status_code == 404 or "model not found" in response_text
            if is_not_found and model != guild_config.embed_fallback:
                retry_model = self._select_model_with_fallback(
                    guild_config.embed_fallback,
                    guild_config.embed_fallback,
                    available_models,
                )
                if retry_model != model:
                    try:
                        return await OllamaClient.embed(
                            endpoint=self.ollama_config.endpoint,
                            model=retry_model,
                            text=text,
                        )
                    except (OllamaConnectionError, OllamaAPIError) as retry_exc:
                        log.warning("Ollama embed failed for guild %s model=%s: %s", guild.id, retry_model, retry_exc)
                        raise commands.UserFeedbackCheckFailure(
                            format_error_message(retry_exc, model=retry_model)
                        ) from retry_exc
                    except Exception as retry_exc:  # noqa: BLE001
                        log.exception("Unexpected Ollama embed error for guild %s model=%s", guild.id, retry_model)
                        raise commands.UserFeedbackCheckFailure(
                            format_error_message(retry_exc, model=retry_model)
                        ) from retry_exc
            log.warning("Ollama embed failed for guild %s model=%s: %s", guild.id, model, exc)
            raise commands.UserFeedbackCheckFailure(format_error_message(exc, model=model)) from exc
        except OllamaConnectionError as exc:
            log.warning("Ollama embed failed for guild %s model=%s: %s", guild.id, model, exc)
            raise commands.UserFeedbackCheckFailure(format_error_message(exc, model=model)) from exc
        except Exception as exc:  # noqa: BLE001
            log.exception("Unexpected Ollama embed error for guild %s model=%s", guild.id, model)
            raise commands.UserFeedbackCheckFailure(format_error_message(exc, model=model)) from exc
