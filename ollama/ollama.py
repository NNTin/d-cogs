import logging
from typing import Literal

import discord
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.config import Config

from .health import HealthMonitor
from .models import OllamaConfig

RequestType = Literal["discord_deleted_user", "owner", "user", "user_strict"]

log = logging.getLogger("red.ollama")


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

        self.ollama_config = OllamaConfig()
        self.health_monitor = HealthMonitor(
            bot=self.bot,
            config=self.ollama_config,
            endpoint=self.ollama_config.endpoint,
        )

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

            if self.ollama_config.health_check_enabled:
                self.health_monitor.start()
        except Exception:  # noqa: BLE001
            log.exception("Failed to initialize Ollama cog health monitoring")

    async def cog_unload(self) -> None:
        self.health_monitor.stop()

    async def red_delete_data_for_user(self, *, requester: RequestType, user_id: int) -> None:
        # TODO: Replace this with the proper end user data removal handling.
        super().red_delete_data_for_user(requester=requester, user_id=user_id)
