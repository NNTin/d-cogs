"""Ollama endpoint health monitoring"""

import logging
from typing import Dict, List, Optional, Tuple

import discord
from discord.ext import tasks
from ollama import AsyncClient, ResponseError
from redbot.core.bot import Red

from .models import OllamaConfig

log = logging.getLogger("red.tin.ollama.health")


class HealthMonitor:
    def __init__(self, bot: Red, config: OllamaConfig, endpoints: List[str]) -> None:
        self.bot = bot
        self.config = config
        self.endpoints = endpoints

        self.health_loop.change_interval(seconds=self.config.health_check_interval)

    async def check_endpoint_health(self, endpoint: str) -> Tuple[bool, List[str]]:
        try:
            client = AsyncClient(host=endpoint)
            response = await client.list()
            models = response.get("models", [])
            model_names = [model.get("model") or model.get("name") for model in models]
            log.debug("Health check ok for %s (%s models)", endpoint, len(model_names))
            return True, model_names
        except ResponseError as exc:
            log.debug("Health check error for %s: %s", endpoint, exc)
            return False, []
        except Exception:  # noqa: BLE001
            log.exception("Unexpected exception during health check for %s", endpoint)
            return False, []

    async def check_health(self, endpoint: Optional[str] = None) -> Dict[str, Tuple[bool, List[str]]]:
        targets = [endpoint] if endpoint else list(self.endpoints or [self.config.endpoint])
        results: Dict[str, Tuple[bool, List[str]]] = {}
        for target in targets:
            healthy, models = await self.check_endpoint_health(target)
            results[target] = (healthy, models)
        return results

    async def discover_models(self, endpoint: Optional[str] = None) -> List[str]:
        target = endpoint or self.config.endpoint
        try:
            client = AsyncClient(host=target)
            response = await client.list()
            models = response.get("models", [])
            model_names = [model.get("model") or model.get("name") for model in models]
            log.debug("Discovered %s models from %s", len(model_names), target)
            return model_names
        except ResponseError as exc:
            log.debug("Model discovery error for %s: %s", target, exc)
            return []
        except Exception:  # noqa: BLE001
            log.exception("Unexpected exception during model discovery for %s", target)
            return []

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
                    log.debug("Ollama endpoint healthy: %s (%s models)", endpoint, len(models))
                else:
                    log.warning("Ollama endpoint unhealthy: %s", endpoint)
        except Exception:  # noqa: BLE001
            log.exception("Unhandled exception in health loop")

    @health_loop.before_loop
    async def before_health_loop(self) -> None:
        await self.bot.wait_until_red_ready()
        log.info("Starting Ollama health monitoring for %s endpoints", len(self.endpoints))

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
