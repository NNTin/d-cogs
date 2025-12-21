import asyncio
import logging
from typing import Any, Dict, Optional

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red
from cogchain.interfaces import ExtensionContext, LangcoreProtocol

from .client import TMDbClient
from .manager import SpoilarrManager
from .types import RequestType


class spoilarr(commands.Cog):
    """TMDb integration with optional spoiler handling."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.logger = logging.getLogger("red.d_cogs.spoilarr")
        self.config = Config.get_conf(self, identifier=257263089, force_registration=True)
        self.config.register_guild(tmdb_api_key="", spoiler_mode=False)
        self._client_cache: Dict[str, TMDbClient] = {}
        self._rate_limiter = asyncio.Semaphore(4)
        self.manager: Optional[SpoilarrManager] = None

    def _get_client(self, api_key: str) -> TMDbClient:
        if api_key not in self._client_cache:
            self._client_cache[api_key] = TMDbClient(api_key, self._rate_limiter, self.logger)
        return self._client_cache[api_key]

    @commands.Cog.listener()
    async def on_langcore_cog_add(self, langcore_cog) -> None:
        schema = {
            "name": "query_spoilarr",
            "description": (
                "Answer movie/TV questions by calling TMDb via Spoilarr (search, details, credits). "
                "Use for natural-language requests like cast lookups, release info, or plot questions; "
                "returns structured data from TMDb instead of guessing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Natural language TMDb question (e.g., 'cast of Inception', "
                            "'search for The Matrix', 'details about Breaking Bad')"
                        ),
                    },
                },
                "required": ["query"],
            },
        }

        try:
            langcore_cog.hub.register_function(
                cog_name=self.qualified_name,
                schema=schema,
                permission_level="user",
            )
            self.logger.info("Registered spoilarr tool: %s", schema["name"])
        except Exception as exc:  # pragma: no cover - logging only
            self.logger.error("Failed to register spoilarr tool %s: %s", schema["name"], exc)

        if hasattr(langcore_cog, "conversation_manager"):
            prompt = (
                "Handle movie and TV questions by calling the `query_spoilarr` tool. "
                "Stick strictly to Spoilarr-provided data as the source of truth; do not add plot details or facts unless the tool returned them."
            )
            try:
                langcore_cog.conversation_manager.register_cog_system_prompt(self.qualified_name, prompt)
                self.logger.info("Registered Spoilarr system prompt with conversation manager")
            except Exception as exc:
                self.logger.warning("Failed to register Spoilarr system prompt: %s", exc)

        if not self.manager:
            self.manager = SpoilarrManager(self)

    async def _ensure_context(self, guild_id: Optional[int]) -> bool:
        return guild_id is not None

    async def _get_settings(self, guild_id: int) -> Dict[str, Any]:
        guild_conf = self.config.guild_from_id(guild_id)
        api_key = await guild_conf.tmdb_api_key()
        spoiler_mode = await guild_conf.spoiler_mode()
        return {"api_key": api_key, "spoiler_mode": spoiler_mode}

    async def _handle_spoiler_instruction(self, ctx: ExtensionContext, spoiler_mode: bool) -> None:
        if spoiler_mode:
            return
        try:
            await ctx.add_to_conversation(
                "Note: Use Discord spoiler markup ||like this|| when presenting sensitive information like plot details, character deaths, or major reveals to the user."
            )
        except Exception:
            # Non-blocking best-effort helper; logging stays minimal to avoid noisy tool failures.
            return

    async def query_spoilarr(
        self,
        query: str,
        ctx: Optional[ExtensionContext] = None,
        guild_id: Optional[int] = None,
        channel_id: Optional[int] = None,
        member_id: Optional[int] = None,
    ) -> str:
        """Query TMDb via SpoilarrManager sub-agent and inject results into conversation."""

        self.logger.info("Spoilarr query received: %s", query)

        if ctx is None and (guild_id is None or channel_id is None or member_id is None):
            return "Missing context parameters (guild_id, channel_id, member_id). Cannot query TMDb."

        if ctx is None:
            langcore_cog = self.bot.get_cog("langcore")
            if not langcore_cog:
                return "Langcore cog unavailable; cannot query TMDb."
            ctx = ExtensionContext(
                guild_id=int(guild_id),
                channel_id=int(channel_id),
                member_id=int(member_id),
                langcore=langcore_cog,
                default_provider=langcore_cog.DEFAULT_PROVIDER_FALLBACK,
            )

        settings = await self._get_settings(ctx.guild_id)
        if not settings["api_key"]:
            return "TMDb API key not configured. Use [p]spoilarr apikey <key>"

        if not self.manager:
            self.manager = SpoilarrManager(self)

        try:
            toon_str = await self.manager.handle_query(query, ctx)
        except Exception as exc:
            self.logger.error("SpoilarrManager query failed: %s", exc)
            return f"Failed to query TMDb: {str(exc)}"

        try:
            await ctx.add_to_conversation(
                content=toon_str,
                role="tool",
                tool_call_id="spoilarr_query",
                name="query_spoilarr",
            )
        except Exception as exc:
            self.logger.warning("Failed to inject Spoilarr data into conversation: %s", exc)
            return f"{toon_str}\n\n⚠️ Conversation context was not updated due to an injection error."

        await self._handle_spoiler_instruction(ctx, settings["spoiler_mode"])

        # return "✅ Spoilarr retrieved TMDb data (toon-formatted JSON added to conversation context)."
        # todo: see TODO.md
        return toon_str

    @commands.group(name="spoilarr")
    @commands.admin_or_permissions(administrator=True)
    @commands.guild_only()
    async def spoilarr_config(self, ctx: commands.Context):
        """Manage Spoilarr TMDb integration settings."""
        pass

    @spoilarr_config.command(name="apikey")
    async def set_apikey(self, ctx: commands.Context, api_key: str):
        """Set the TMDb API key for this server."""
        await self.config.guild(ctx.guild).tmdb_api_key.set(api_key)
        await ctx.send("✅ TMDb API key has been set.")
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    @spoilarr_config.command(name="spoilermode")
    async def toggle_spoiler_mode(self, ctx: commands.Context):
        """Toggle spoiler mode (enabled = no censoring, disabled = censor sensitive info)."""
        current = await self.config.guild(ctx.guild).spoiler_mode()
        await self.config.guild(ctx.guild).spoiler_mode.set(not current)
        status = "enabled (no censoring)" if not current else "disabled (censoring active)"
        await ctx.send(f"Spoiler mode is now {status}.")

    @spoilarr_config.command(name="settings")
    async def view_settings(self, ctx: commands.Context):
        """View current Spoilarr configuration."""
        api_key = await self.config.guild(ctx.guild).tmdb_api_key()
        spoiler_mode = await self.config.guild(ctx.guild).spoiler_mode()

        embed = discord.Embed(title="Spoilarr Configuration", color=discord.Color.blue())
        embed.add_field(name="API Key", value="✅ Configured" if api_key else "❌ Not set", inline=False)
        embed.add_field(
            name="Spoiler Mode",
            value="Enabled (no censoring)" if spoiler_mode else "Disabled (censoring active)",
            inline=False,
        )
        await ctx.send(embed=embed)

    async def cog_unload(self) -> None:
        await super().cog_unload()
        langcore_cog = self.bot.get_cog("langcore")
        if isinstance(langcore_cog, LangcoreProtocol):
            try:
                langcore_cog.conversation_manager.unregister_cog_system_prompt(self.qualified_name)
            except Exception as exc:
                self.logger.debug("Failed to unregister Spoilarr system prompt on unload: %s", exc)
        self._client_cache.clear()
        self.logger.info("Spoilarr cog unloaded")

    @commands.Cog.listener()
    async def on_langcore_cog_remove(self, langcore_cog=None) -> None:
        """Remove the Spoilarr system prompt if langcore unloads."""
        langcore_cog = langcore_cog or self.bot.get_cog("langcore")
        if isinstance(langcore_cog, LangcoreProtocol):
            try:
                langcore_cog.conversation_manager.unregister_cog_system_prompt(self.qualified_name)
                self.logger.info("Unregistered Spoilarr system prompt after langcore removal")
            except Exception as exc:
                self.logger.debug("Failed to unregister Spoilarr system prompt after langcore removal: %s", exc)

    async def red_delete_data_for_user(self, *, requester: RequestType, user_id: int) -> None:
        """Handle user data deletion requests (GDPR compliance)."""
        pass
