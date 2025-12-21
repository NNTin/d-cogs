"""OpenRouter health monitoring and model discovery."""

import logging
from typing import List, Tuple

import aiohttp
import discord
from discord.ext import tasks
from redbot.core.bot import Red

from .models import OpenRouterConfig

log = logging.getLogger("red.tin.openrouter.health")


class HealthMonitor:
    def __init__(self, bot: Red, config: OpenRouterConfig, base_url: str) -> None:
        self.bot = bot
        self.config = config
        self.base_url = base_url.rstrip("/")

        self.health_loop.change_interval(seconds=self.config.health_check_interval)

    def _headers(self) -> dict:
        headers = {"Authorization": f"Bearer {self.config.api_key}"} if self.config.api_key else {}
        if self.config.default_headers:
            headers.update(self.config.default_headers)
        return headers

    async def _fetch_models(self) -> List[str]:
        url = f"{self.base_url}/models"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self._headers(), timeout=15) as resp:
                    if resp.status != 200:
                        log.debug("Model fetch failed for %s: status %s", url, resp.status)
                        return []
                    data = await resp.json()
                    # OpenRouter returns {"data": [{"id": "openai/gpt-4o", ...}, ...]}
                    models = data.get("data", []) if isinstance(data, dict) else []
                    names = [m.get("id") or m.get("name") for m in models if isinstance(m, dict)]
                    return [n for n in names if n]
        except Exception:  # noqa: BLE001
            log.exception("Unexpected exception during model fetch for %s", url)
            return []

    async def check_health(self) -> Tuple[bool, List[str]]:
        models = await self._fetch_models()
        healthy = bool(models)
        if healthy:
            log.debug("Health check ok for %s (%s models)", self.base_url, len(models))
        else:
            log.debug("Health check failed for %s", self.base_url)
        return healthy, models

    async def discover_models(self) -> List[str]:
        models = await self._fetch_models()
        log.debug("Discovered %s models from %s", len(models), self.base_url)
        return models

    @tasks.loop(seconds=60)
    async def health_loop(self) -> None:
        if not self.config.health_check_enabled:
            return

        try:
            try:
                await self.bot.change_presence(status=discord.Status.idle)
            except Exception:  # noqa: BLE001
                log.debug("Failed to set bot presence to idle during health check", exc_info=True)

            healthy, models = await self.check_health()
            self.config.update_health(healthy, models)

            try:
                await self.bot.change_presence(status=discord.Status.online if healthy else discord.Status.dnd)
            except Exception:  # noqa: BLE001
                log.debug("Failed to update bot presence after health check", exc_info=True)

            if healthy:
                log.debug("OpenRouter endpoint healthy: %s (%s models)", self.base_url, len(models))
            else:
                log.warning("OpenRouter endpoint unhealthy: %s", self.base_url)
        except Exception:  # noqa: BLE001
            log.exception("Unhandled exception in health loop for %s", self.base_url)

    @health_loop.before_loop
    async def before_health_loop(self) -> None:
        await self.bot.wait_until_red_ready()
        log.info("Starting OpenRouter health monitoring for %s", self.base_url)

    def start(self) -> None:
        if not self.config.health_check_enabled:
            log.debug("Health monitoring not started (disabled)")
            return

        try:
            self.health_loop.change_interval(seconds=self.config.health_check_interval)
            if not self.health_loop.is_running():
                self.health_loop.start()
        except Exception:  # noqa: BLE001
            log.exception("Failed to start health monitoring for %s", self.base_url)

    def stop(self) -> None:
        try:
            if self.health_loop.is_running():
                self.health_loop.cancel()
        except Exception:  # noqa: BLE001
            log.exception("Failed to stop health monitoring for %s", self.base_url)

    def update_interval(self, seconds: int) -> None:
        try:
            self.health_loop.change_interval(seconds=seconds)
            self.config.health_check_interval = seconds
            log.info("Health check interval updated to %ss for %s", seconds, self.base_url)
        except Exception:  # noqa: BLE001
            log.exception("Failed to update health check interval for %s", self.base_url)
