"""Function registry hub for Chain providers.

This registry is intended to be instantiated by the langcore cog and kept in sync
with cog lifecycle events. Provider cogs should call register_function during
their initialization, and guild configuration should be passed to get_functions
to honor enable/disable state per guild. The langcore cog can unregister cogs
inside on_cog_remove to keep the registry clean.
"""

from typing import Callable, Dict, List, Optional, Tuple, Literal
import logging

import discord
from redbot.core import commands
from redbot.core.bot import Red

from .models import GuildConfig
from .utils import validate_function_schema

log = logging.getLogger("red.tin.langcore.hub")


class ChainHub:
    """In-memory registry for function schemas and callables."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self._registry: Dict[str, Dict[str, Dict[str, object]]] = {}

    def _validate_cog(self, cog_name: str) -> Optional[commands.Cog]:
        """Return a loaded cog instance or None when missing."""
        cog = self.bot.get_cog(cog_name)
        if not cog:
            log.warning("Registry rejected %s: cog is not loaded or does not exist", cog_name)
        return cog

    def _check_function_conflict(self, function_name: str, exclude_cog: str) -> Optional[str]:
        """Return the cog name that already registered the function, if any."""
        for cog_name, functions in self._registry.items():
            if cog_name == exclude_cog:
                continue
            if function_name in functions:
                return cog_name
        return None

    def register_function(
        self,
        cog_name: str,
        schema: dict,
        permission_level: Literal["user", "mod", "admin", "owner"] = "user",
    ) -> bool:
        """Register a function schema and callable reference for a cog."""
        cog = self._validate_cog(cog_name)
        if not cog:
            return False

        if not schema:
            log.warning("Function registry failed for %s: empty schema provided", cog_name)
            return False

        missing = validate_function_schema(schema)
        if missing:
            log.debug("Schema validation failed for %s: %s", cog_name, missing.strip())
            log.warning("Function registry failed for %s: invalid json schema", cog_name)
            return False

        function_name = schema["name"]
        conflict = self._check_function_conflict(function_name, cog_name)
        if conflict:
            log.warning(
                "Function registry failed for %s: %s already registered %s",
                cog_name,
                conflict,
                function_name,
            )
            return False

        if not hasattr(cog, function_name):
            log.warning(
                "Function registry failed for %s: cog does not have a function called %s",
                cog_name,
                function_name,
            )
            return False

        allowed_permissions = {"user", "mod", "admin", "owner"}
        if permission_level not in allowed_permissions:
            log.warning(
                "Function registry failed for %s: invalid permission level %s",
                cog_name,
                permission_level,
            )
            return False

        if cog_name not in self._registry:
            self._registry[cog_name] = {}

        self._registry[cog_name][function_name] = {
            "permission_level": permission_level,
            "schema": schema,
        }
        log.info("Registered function %s for cog %s", function_name, cog_name)
        return True

    def unregister_function(self, cog_name: str, function_name: str) -> None:
        """Remove a specific function from the registry."""
        if cog_name not in self._registry:
            log.debug("%s not in registry", cog_name)
            return
        if function_name not in self._registry[cog_name]:
            log.debug("%s not in %s registry", function_name, cog_name)
            return
        del self._registry[cog_name][function_name]
        log.info("Unregistered function %s for cog %s", function_name, cog_name)

    def unregister_cog(self, cog_name: str) -> None:
        """Remove all functions registered by a cog."""
        if cog_name not in self._registry:
            log.debug("%s not in registry", cog_name)
            return
        del self._registry[cog_name]
        log.info("Unregistered cog %s from registry", cog_name)

    async def get_functions(
        self,
        guild_id: int,
        guild_config: GuildConfig,
        member: Optional[discord.Member] = None,
        permission_filter: bool = True,
    ) -> Tuple[List[dict], Dict[str, Callable]]:
        """Return enabled function schemas and callable map for a guild."""

        async def can_use(perm_level: str) -> bool:
            if not permission_filter:
                return True
            if perm_level == "user":
                return True
            if member is None:
                return False
            if perm_level == "mod":
                return any(
                    [
                        member.guild_permissions.manage_messages,
                        await self.bot.is_mod(member),
                    ]
                )
            if perm_level == "admin":
                return any(
                    [
                        member.guild_permissions.administrator,
                        await self.bot.is_admin(member),
                    ]
                )
            if perm_level == "owner":
                return await self.bot.is_owner(member)
            return False

        function_calls: List[dict] = []
        function_map: Dict[str, Callable] = {}

        for cog_name, functions in self._registry.items():
            cog = self._validate_cog(cog_name)
            if not cog:
                continue
            for function_name, data in functions.items():
                if not guild_config.function_statuses.get(function_name, True):
                    log.debug(
                        "Function %s disabled for guild %s; skipping",
                        function_name,
                        guild_id,
                    )
                    continue
                if function_name in function_map:
                    log.debug("Function %s already prepared; skipping duplicate", function_name)
                    continue
                function_obj = getattr(cog, function_name, None)
                if function_obj is None:
                    log.warning("%s is missing callable %s; skipping", cog_name, function_name)
                    continue
                if not await can_use(data["permission_level"]):
                    log.debug(
                        "Skipping %s for member %s due to permission level %s",
                        function_name,
                        getattr(member, "id", "unknown"),
                        data["permission_level"],
                    )
                    continue
                function_calls.append(data["schema"])
                function_map[function_name] = function_obj

        return function_calls, function_map

    def get_registered_cogs(self) -> List[str]:
        """Return a list of cog names that registered functions."""
        return list(self._registry.keys())

    def get_cog_functions(self, cog_name: str) -> List[str]:
        """Return function names registered by a specific cog."""
        return list(self._registry.get(cog_name, {}).keys())
