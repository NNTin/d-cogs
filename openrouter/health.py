"""OpenRouter health monitoring and model discovery."""

import logging
from typing import Dict, List, Optional, Tuple

import aiohttp
import discord
from discord.ext import tasks
from redbot.core.bot import Red

from .models import OpenRouterConfig

log = logging.getLogger("red.tin.openrouter.health")


class HealthMonitor:
    def __init__(self, bot: Red, config: OpenRouterConfig, endpoints: List[str]) -> None:
        self.bot = bot
        self.config = config
        self.endpoints = [e.rstrip("/") for e in endpoints if e]

        self.health_loop.change_interval(seconds=self.config.health_check_interval)

    def _headers(self) -> dict:
        headers = {"Authorization": f"Bearer {self.config.api_key}"} if self.config.api_key else {}
        if self.config.default_headers:
            headers.update(self.config.default_headers)
        return headers

    async def _fetch_models(self, endpoint: str) -> List[str]:
        endpoint = endpoint.rstrip("/")
        url = f"{endpoint}/models"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self._headers(), timeout=15) as resp:
                    if resp.status != 200:
                        log.debug("Model fetch failed for %s: status %s", url, resp.status)
                        return []
                    data = await resp.json()
                    models = data.get("data", []) if isinstance(data, dict) else []
                    names = [m.get("id") or m.get("name") for m in models if isinstance(m, dict)]
                    return [n for n in names if n]
        except Exception:  # noqa: BLE001
            log.exception("Unexpected exception during model fetch for %s", url)
            return []

    async def _check_endpoint(self, endpoint: str) -> Tuple[bool, List[str]]:
        models = await self._fetch_models(endpoint)
        healthy = bool(models)
        if healthy:
            log.debug("Health check ok for %s (%s models)", endpoint, len(models))
        else:
            log.debug("Health check failed for %s", endpoint)
        return healthy, models

    async def check_health(self, endpoint: Optional[str] = None) -> Dict[str, Tuple[bool, List[str]]]:
        targets = [endpoint.rstrip("/")] if endpoint else list(self.endpoints or [self.config.base_url])
        results: Dict[str, Tuple[bool, List[str]]] = {}
        for target in targets:
            healthy, models = await self._check_endpoint(target)
            results[target] = (healthy, models)
        return results

    async def discover_models(self, endpoint: Optional[str] = None) -> List[str]:
        target = (endpoint or self.config.base_url).rstrip("/")
        models = await self._fetch_models(target)
        log.debug("Discovered %s models from %s", len(models), target)
        return models

    @tasks.loop(seconds=60)
    async def health_loop(self) -> None:
        if not self.config.health_check_enabled:
            return

        if not self.endpoints:
            log.warning("Health loop skipped: no endpoints configured")
            return

        try:
            try:
                await self.bot.change_presence(status=discord.Status.idle)
            except Exception:  # noqa: BLE001
                log.debug("Failed to set bot presence to idle during health check", exc_info=True)

            results = await self.check_health()
            any_healthy = False
            for endpoint, (healthy, models) in results.items():
                self.config.update_health(endpoint, healthy, models)
                if healthy:
                    any_healthy = True

            try:
                await self.bot.change_presence(status=discord.Status.online if any_healthy else discord.Status.dnd)
            except Exception:  # noqa: BLE001
                log.debug("Failed to update bot presence after health check", exc_info=True)

            for endpoint, (healthy, models) in results.items():
                if healthy:
                    log.debug("OpenRouter endpoint healthy: %s (%s models)", endpoint, len(models))
                else:
                    log.warning("OpenRouter endpoint unhealthy: %s", endpoint)
        except Exception:  # noqa: BLE001
            log.exception("Unhandled exception in OpenRouter health loop")

    @health_loop.before_loop
    async def before_health_loop(self) -> None:
        await self.bot.wait_until_red_ready()
        log.info("Starting OpenRouter health monitoring for %s endpoints", len(self.endpoints))

    def start(self) -> None:
        if not self.config.health_check_enabled:
            log.debug("Health monitoring not started (disabled)")
            return

        try:
            self.health_loop.change_interval(seconds=self.config.health_check_interval)
            if not self.health_loop.is_running():
                self.health_loop.start()
        except Exception:  # noqa: BLE001
            log.exception("Failed to start health monitoring")

    def stop(self) -> None:
        try:
            if self.health_loop.is_running():
                self.health_loop.cancel()
        except Exception:  # noqa: BLE001
            log.exception("Failed to stop health monitoring")

    def update_interval(self, seconds: int) -> None:
        try:
            self.health_loop.change_interval(seconds=seconds)
            self.config.health_check_interval = seconds
            log.info("Health check interval updated to %ss", seconds)
        except Exception:  # noqa: BLE001
            log.exception("Failed to update health check interval")
