import asyncio
import logging
from typing import Any, Dict, List, Literal, Optional

import aiohttp
import discord
import toon_format
from redbot.core import Config, commands
from redbot.core.bot import Red

RequestType = Literal["discord_deleted_user", "owner", "user", "user_strict"]


def censor_spoilers(data: Dict[str, Any], fields: Optional[List[str]] = None) -> Dict[str, Any]:
    """Return a copy of data with spoiler fields replaced by Discord spoiler markup."""
    if fields is None:
        fields = [
            "overview",
            "tagline",
            "biography",
            "backdrop_path",
            "poster_path",
            "release_date",
            "first_air_date",
        ]

    def _censor(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: ("||spoiler||" if k in fields else _censor(v)) for k, v in value.items()}
        if isinstance(value, list):
            return [_censor(item) for item in value]
        return value

    return _censor(data)


class TMDbClient:
    def __init__(self, api_key: str, rate_limiter: asyncio.Semaphore, logger: logging.Logger):
        self.api_key = api_key
        self.rate_limiter = rate_limiter
        self.logger = logger
        self.base_url = "https://api.themoviedb.org/3"

    async def _request(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        params = params or {}
        params["api_key"] = self.api_key

        backoff = 1.0
        attempts = 0
        last_error: Optional[Exception] = None

        while attempts < 3:
            try:
                async with self.rate_limiter:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(f"{self.base_url}{endpoint}", params=params) as resp:
                            if resp.status == 401:
                                raise ValueError("Invalid TMDb API key")
                            if resp.status == 404:
                                raise ValueError("Resource not found")
                            if resp.status == 429:
                                raise RuntimeError("TMDb rate limit exceeded")
                            if resp.status >= 500:
                                raise RuntimeError(f"TMDb server error ({resp.status})")
                            resp.raise_for_status()
                            return await resp.json()
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, RuntimeError) as exc:
                last_error = exc
                attempts += 1
                if attempts >= 3 or isinstance(exc, ValueError):
                    break
                self.logger.warning("TMDb request failed (%s), retrying in %.1fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff *= 2

        if last_error:
            raise last_error

        raise RuntimeError("TMDb request failed with unknown error")

    async def search_movies(self, query: str) -> Dict[str, Any]:
        return await self._request("/search/movie", params={"query": query})

    async def search_tv(self, query: str) -> Dict[str, Any]:
        return await self._request("/search/tv", params={"query": query})

    async def movie_details(self, movie_id: int) -> Dict[str, Any]:
        return await self._request(f"/movie/{movie_id}")

    async def tv_details(self, tv_id: int) -> Dict[str, Any]:
        return await self._request(f"/tv/{tv_id}")

    async def movie_credits(self, movie_id: int) -> Dict[str, Any]:
        return await self._request(f"/movie/{movie_id}/credits")

    async def tv_credits(self, tv_id: int) -> Dict[str, Any]:
        return await self._request(f"/tv/{tv_id}/credits")


class spoilarr(commands.Cog):
    """TMDb integration with optional spoiler handling."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.logger = logging.getLogger("red.d_cogs.spoilarr")
        self.config = Config.get_conf(self, identifier=257263089, force_registration=True)
        self.config.register_guild(tmdb_api_key="", spoiler_mode=False)
        self._client_cache: Dict[str, TMDbClient] = {}
        self._rate_limiter = asyncio.Semaphore(4)

    def _get_client(self, api_key: str) -> TMDbClient:
        if api_key not in self._client_cache:
            self._client_cache[api_key] = TMDbClient(api_key, self._rate_limiter, self.logger)
        return self._client_cache[api_key]

    @commands.Cog.listener()
    async def on_langcore_cog_add(self, langcore_cog) -> None:
        schemas = [
            {
                "name": "search_movies",
                "description": "Search for movies by title. Returns toon-formatted results with id, title, release_year, and overview (censored if spoiler mode off).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Movie title to search for",
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "search_tv",
                "description": "Search for TV shows by title. Returns toon-formatted results with id, title, release_year, and overview (censored if spoiler mode off).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "TV show title to search for",
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "movie_details",
                "description": "Get detailed information about a movie by TMDb ID. Returns toon-formatted data including title, overview, genres, runtime, cast (censored if spoiler mode off).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tmdb_id": {
                            "type": "number",
                            "description": "TMDb movie ID",
                        },
                    },
                    "required": ["tmdb_id"],
                },
            },
            {
                "name": "tv_details",
                "description": "Get detailed information about a TV show by TMDb ID. Returns toon-formatted data including title, overview, genres, runtime, cast (censored if spoiler mode off).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tmdb_id": {
                            "type": "number",
                            "description": "TMDb TV show ID",
                        },
                    },
                    "required": ["tmdb_id"],
                },
            },
            {
                "name": "movie_credits",
                "description": "Get cast and crew credits for a movie. Returns toon-formatted list of actors, directors, writers (censored if spoiler mode off).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tmdb_id": {
                            "type": "number",
                            "description": "TMDb movie ID",
                        },
                    },
                    "required": ["tmdb_id"],
                },
            },
            {
                "name": "tv_credits",
                "description": "Get cast and crew credits for a TV show. Returns toon-formatted list of actors, directors, writers (censored if spoiler mode off).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tmdb_id": {
                            "type": "number",
                            "description": "TMDb TV show ID",
                        },
                    },
                    "required": ["tmdb_id"],
                },
            },
        ]

        for schema in schemas:
            try:
                langcore_cog.hub.register_function(
                    cog_name=self.qualified_name,
                    schema=schema,
                    permission_level="user",
                )
                self.logger.info("Registered spoilarr tool: %s", schema["name"])
            except Exception as exc:  # pragma: no cover - logging only
                self.logger.error("Failed to register spoilarr tool %s: %s", schema["name"], exc)

    async def _ensure_context(self, guild_id: Optional[int]) -> bool:
        return guild_id is not None

    async def _get_settings(self, guild_id: int) -> Dict[str, Any]:
        guild_conf = self.config.guild_from_id(guild_id)
        api_key = await guild_conf.tmdb_api_key()
        spoiler_mode = await guild_conf.spoiler_mode()
        return {"api_key": api_key, "spoiler_mode": spoiler_mode}

    async def _handle_spoiler_instruction(
        self,
        member_id: Optional[int],
        channel_id: Optional[int],
        guild_id: Optional[int],
        spoiler_mode: bool,
    ) -> None:
        if spoiler_mode:
            return
        if member_id is None or channel_id is None or guild_id is None:
            return
        langcore_cog = self.bot.get_cog("langcore")
        if not langcore_cog:
            return
        conv_manager = getattr(langcore_cog, "conversation_manager", None)
        if not conv_manager:
            return

        conversation = conv_manager.get_conversation(member_id, channel_id, guild_id)
        lock = conv_manager.get_conversation_lock(member_id, channel_id, guild_id)
        async with lock:
            conversation.add_assistant_message(
                "Note: Use Discord spoiler markup ||like this|| when presenting sensitive information like plot details, character deaths, or major reveals to the user."
            )

    async def _handle_tool_response(
        self,
        data_fetcher,
        guild_id: Optional[int],
        channel_id: Optional[int],
        member_id: Optional[int],
        *args,
    ) -> str:
        if guild_id is None:
            return "Missing guild context"

        settings = await self._get_settings(guild_id)
        api_key = settings["api_key"]
        spoiler_mode = settings["spoiler_mode"]

        if not api_key:
            return "TMDb API key not configured. Use [p]spoilarr apikey <key>"

        client = self._get_client(api_key)

        try:
            data = await data_fetcher(client, *args)
        except Exception as exc:
            self.logger.error("TMDb API error: %s", exc)
            return f"TMDb API error: {str(exc)}"

        if not spoiler_mode:
            data = censor_spoilers(data)

        await self._handle_spoiler_instruction(member_id, channel_id, guild_id, spoiler_mode)

        try:
            return toon_format.encode(data)
        except Exception as exc:
            self.logger.error("Failed to encode TMDb response: %s", exc)
            return "TMDb data encoding error"

    async def search_movies(
        self,
        query: str,
        guild_id: Optional[int] = None,
        channel_id: Optional[int] = None,
        member_id: Optional[int] = None,
    ) -> str:
        return await self._handle_tool_response(
            lambda client, q: client.search_movies(q), guild_id, channel_id, member_id, query
        )

    async def search_tv(
        self,
        query: str,
        guild_id: Optional[int] = None,
        channel_id: Optional[int] = None,
        member_id: Optional[int] = None,
    ) -> str:
        return await self._handle_tool_response(
            lambda client, q: client.search_tv(q), guild_id, channel_id, member_id, query
        )

    async def movie_details(
        self,
        tmdb_id: int,
        guild_id: Optional[int] = None,
        channel_id: Optional[int] = None,
        member_id: Optional[int] = None,
    ) -> str:
        return await self._handle_tool_response(
            lambda client, mid: client.movie_details(mid), guild_id, channel_id, member_id, tmdb_id
        )

    async def tv_details(
        self,
        tmdb_id: int,
        guild_id: Optional[int] = None,
        channel_id: Optional[int] = None,
        member_id: Optional[int] = None,
    ) -> str:
        return await self._handle_tool_response(
            lambda client, tid: client.tv_details(tid), guild_id, channel_id, member_id, tmdb_id
        )

    async def movie_credits(
        self,
        tmdb_id: int,
        guild_id: Optional[int] = None,
        channel_id: Optional[int] = None,
        member_id: Optional[int] = None,
    ) -> str:
        return await self._handle_tool_response(
            lambda client, mid: client.movie_credits(mid), guild_id, channel_id, member_id, tmdb_id
        )

    async def tv_credits(
        self,
        tmdb_id: int,
        guild_id: Optional[int] = None,
        channel_id: Optional[int] = None,
        member_id: Optional[int] = None,
    ) -> str:
        return await self._handle_tool_response(
            lambda client, tid: client.tv_credits(tid), guild_id, channel_id, member_id, tmdb_id
        )

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
        self._client_cache.clear()
        self.logger.info("Spoilarr cog unloaded")

    async def red_delete_data_for_user(self, *, requester: RequestType, user_id: int) -> None:
        """Handle user data deletion requests (GDPR compliance)."""
        pass
