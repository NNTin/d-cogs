"""Ollama endpoint health monitoring"""

import logging
from typing import List, Tuple

import discord
from discord.ext import tasks
from redbot.core.bot import Red

from . import api
from .api import OllamaAPIError, OllamaConnectionError, OllamaModelInfo
from .models import OllamaConfig

log = logging.getLogger("red.ollama.health")


class HealthMonitor:
    def __init__(self, bot: Red, config: OllamaConfig, endpoint: str) -> None:
        self.bot = bot
        self.config = config
        self.endpoint = endpoint
        self.log = logging.getLogger("red.ollama.health")

        self.health_loop.change_interval(seconds=self.config.health_check_interval)

    async def check_health(self) -> Tuple[bool, List[str]]:
        try:
            healthy = await api.check_health(self.endpoint)
            if not healthy:
                self.log.debug("Health check failed for %s", self.endpoint)
                return False, []

            model_infos: List[OllamaModelInfo] = await api.OllamaClient.list_models(self.endpoint)
            models = [model.name for model in model_infos]
            self.log.debug("Health check ok for %s (%s models)", self.endpoint, len(models))
            return True, models
        except (OllamaConnectionError, OllamaAPIError) as exc:
            self.log.debug("Health check error for %s: %s", self.endpoint, exc)
            return False, []
        except Exception:  # noqa: BLE001
            self.log.exception("Unexpected exception during health check for %s", self.endpoint)
            return False, []

    async def discover_models(self) -> List[str]:
        try:
            model_infos: List[OllamaModelInfo] = await api.OllamaClient.list_models(self.endpoint)
            models = [model.name for model in model_infos]
            self.log.debug("Discovered %s models from %s", len(models), self.endpoint)
            return models
        except (OllamaConnectionError, OllamaAPIError) as exc:
            self.log.debug("Model discovery error for %s: %s", self.endpoint, exc)
            return []
        except Exception:  # noqa: BLE001
            self.log.exception("Unexpected exception during model discovery for %s", self.endpoint)
            return []

    @tasks.loop(seconds=60)
    async def health_loop(self) -> None:
        if not self.config.health_check_enabled:
            return

        try:
            try:
                await self.bot.change_presence(status=discord.Status.idle)
            except Exception:  # noqa: BLE001
                self.log.debug("Failed to set bot presence to idle during health check", exc_info=True)

            healthy, models = await self.check_health()
            self.config.update_health(healthy, models)

            try:
                await self.bot.change_presence(status=discord.Status.online if healthy else discord.Status.dnd)
            except Exception:  # noqa: BLE001
                self.log.debug("Failed to update bot presence after health check", exc_info=True)

            if healthy:
                self.log.debug("Ollama endpoint healthy: %s (%s models)", self.endpoint, len(models))
            else:
                self.log.warning("Ollama endpoint unhealthy: %s", self.endpoint)
        except Exception:  # noqa: BLE001
            self.log.exception("Unhandled exception in health loop for %s", self.endpoint)

    @health_loop.before_loop
    async def before_health_loop(self) -> None:
        await self.bot.wait_until_red_ready()
        self.log.info("Starting Ollama health monitoring for %s", self.endpoint)

    def start(self) -> None:
        if not self.config.health_check_enabled:
            self.log.debug("Health monitoring not started (disabled)")
            return

        try:
            self.health_loop.change_interval(seconds=self.config.health_check_interval)
            if not self.health_loop.is_running():
                self.health_loop.start()
        except Exception:  # noqa: BLE001
            self.log.exception("Failed to start health monitoring for %s", self.endpoint)

    def stop(self) -> None:
        try:
            if self.health_loop.is_running():
                self.health_loop.cancel()
        except Exception:  # noqa: BLE001
            self.log.exception("Failed to stop health monitoring for %s", self.endpoint)

    def update_interval(self, seconds: int) -> None:
        try:
            self.health_loop.change_interval(seconds=seconds)
            self.config.health_check_interval = seconds
            self.log.info("Health check interval updated to %ss for %s", seconds, self.endpoint)
        except Exception:  # noqa: BLE001
            self.log.exception("Failed to update health check interval for %s", self.endpoint)
