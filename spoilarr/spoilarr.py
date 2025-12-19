import asyncio
import logging
from typing import Any, Callable, Dict, List, Literal, Optional

import aiohttp
import discord
import toon_format
from langchain_core.messages import AIMessage, ToolMessage, convert_to_messages
from redbot.core import Config, commands
from redbot.core.bot import Red

from langcore.models import Conversation

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


class SpoilarrManager:
    """Sub-agent responsible for orchestrating Spoilarr TMDb tool usage."""

    SYSTEM_PROMPT = (
        "Spoilarr TMDb expert. Use tools step-by-step. Final answer: ONLY `toon.dumps(JSON)` with relevant data "
        "(title, cast, etc.). No prose."
    )

    def __init__(self, spoilarr_cog, langcore_cog) -> None:
        self.spoilarr_cog = spoilarr_cog
        self.langcore_cog = langcore_cog
        self.logger = logging.getLogger("red.d_cogs.spoilarr.manager")

        self._tool_schemas: List[Dict[str, Any]] = [
            {
                "name": "search_movies",
                "description": "Search for movies by title.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string", "description": "Movie title to search for"}},
                    "required": ["query"],
                },
            },
            {
                "name": "search_tv",
                "description": "Search for TV shows by title.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string", "description": "TV show title to search for"}},
                    "required": ["query"],
                },
            },
            {
                "name": "movie_details",
                "description": "Get TMDb movie details by ID.",
                "parameters": {
                    "type": "object",
                    "properties": {"tmdb_id": {"type": "number", "description": "TMDb movie ID"}},
                    "required": ["tmdb_id"],
                },
            },
            {
                "name": "tv_details",
                "description": "Get TMDb TV show details by ID.",
                "parameters": {
                    "type": "object",
                    "properties": {"tmdb_id": {"type": "number", "description": "TMDb TV show ID"}},
                    "required": ["tmdb_id"],
                },
            },
            {
                "name": "movie_credits",
                "description": "Get TMDb movie credits by ID.",
                "parameters": {
                    "type": "object",
                    "properties": {"tmdb_id": {"type": "number", "description": "TMDb movie ID"}},
                    "required": ["tmdb_id"],
                },
            },
            {
                "name": "tv_credits",
                "description": "Get TMDb TV show credits by ID.",
                "parameters": {
                    "type": "object",
                    "properties": {"tmdb_id": {"type": "number", "description": "TMDb TV show ID"}},
                    "required": ["tmdb_id"],
                },
            },
        ]

    def _build_callbacks(self, guild_id: int) -> Dict[str, Callable[..., Any]]:
        async def _search_movies(query: str) -> Any:
            return await self.spoilarr_cog._internal_search_movies(query=query, guild_id=guild_id)

        async def _search_tv(query: str) -> Any:
            return await self.spoilarr_cog._internal_search_tv(query=query, guild_id=guild_id)

        async def _movie_details(tmdb_id: int) -> Any:
            return await self.spoilarr_cog._internal_movie_details(tmdb_id=tmdb_id, guild_id=guild_id)

        async def _tv_details(tmdb_id: int) -> Any:
            return await self.spoilarr_cog._internal_tv_details(tmdb_id=tmdb_id, guild_id=guild_id)

        async def _movie_credits(tmdb_id: int) -> Any:
            return await self.spoilarr_cog._internal_movie_credits(tmdb_id=tmdb_id, guild_id=guild_id)

        async def _tv_credits(tmdb_id: int) -> Any:
            return await self.spoilarr_cog._internal_tv_credits(tmdb_id=tmdb_id, guild_id=guild_id)

        return {
            "search_movies": _search_movies,
            "search_tv": _search_tv,
            "movie_details": _movie_details,
            "tv_details": _tv_details,
            "movie_credits": _movie_credits,
            "tv_credits": _tv_credits,
        }

    async def handle_query(self, query: str, guild_id: int) -> str:
        provider = self.langcore_cog.get_provider("ollama")
        if not provider:
            raise RuntimeError("SpoilarrManager could not find the ollama provider")

        llm = await provider.get_chat_llm(guild_id=guild_id)

        conversation = Conversation()
        conversation.add_assistant_message(self.SYSTEM_PROMPT)
        conversation.update_messages(query, role="user")

        try:
            messages = convert_to_messages(conversation.messages)
            callbacks = self._build_callbacks(guild_id)
            max_iterations = 10
            iteration = 0

            while iteration < max_iterations:
                iteration += 1

                bound_llm = llm.bind_tools(self._tool_schemas)
                ai_msg: AIMessage = await bound_llm.ainvoke(messages)
                messages.append(ai_msg)

                if not ai_msg.tool_calls:
                    break

                for tool_index, tool_call in enumerate(ai_msg.tool_calls):
                    if isinstance(tool_call, dict):
                        tool_name = tool_call.get("name")
                        tool_args = tool_call.get("args")
                        tool_id = tool_call.get("id")
                    else:
                        tool_name = getattr(tool_call, "name", None)
                        tool_args = getattr(tool_call, "args", None)
                        tool_id = getattr(tool_call, "id", None)

                        if (tool_name is None or tool_args is None or tool_id is None) and hasattr(tool_call, "get"):
                            tool_name = tool_name or tool_call.get("name")
                            tool_args = tool_args if tool_args is not None else tool_call.get("args")
                            tool_id = tool_id or tool_call.get("id")

                    if tool_name is None:
                        self.logger.warning("Tool call missing name (type=%s): %r", type(tool_call), tool_call)
                        tool_name = ""

                    if tool_args is None:
                        tool_args = {}

                    if not isinstance(tool_args, dict):
                        self.logger.warning("Tool %s args expected dict, got %s", tool_name, type(tool_args))
                        tool_args = {}

                    if tool_id is None:
                        tool_id = f"tool_call_{iteration}_{tool_index}"

                    callback = callbacks.get(tool_name, lambda **_: "Tool not found")

                    try:
                        result = (
                            await callback(**tool_args)
                            if asyncio.iscoroutinefunction(callback)
                            else callback(**tool_args)
                        )
                        tool_result = str(result)
                    except Exception as exc:
                        self.logger.error("Tool %s execution failed: %s", tool_name, exc)
                        tool_result = f"Error executing {tool_name}: {exc}"

                    messages.append(ToolMessage(content=tool_result, tool_call_id=tool_id))

            if iteration >= max_iterations:
                self.logger.warning("Spoilarr agent loop reached max iterations (%s)", max_iterations)
        except Exception as exc:
            self.logger.error("Spoilarr agent loop failed: %s", exc)
            try:
                return toon_format.dumps({"error": f"Spoilarr agent failed: {exc}"}, indent=2)
            except Exception:
                return f'{{"error": "Spoilarr agent failed: {exc}"}}'

        final_response = ""
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                final_response = str(msg.content) if msg.content else ""
                break

        if not final_response:
            self.logger.warning("Spoilarr agent loop produced no AI response content")
            try:
                return toon_format.dumps({"error": "Spoilarr agent produced no response"}, indent=2)
            except Exception:
                return '{"error": "Spoilarr agent produced no response"}'

        return final_response.strip()


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
                "Query TMDb via Spoilarr sub-agent. Handles movie/TV search, details, and credits "
                "intelligently from natural language queries. Returns toon-formatted JSON data. "
                "Spoilers are automatically censored based on server settings."
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

        self.manager = SpoilarrManager(self, langcore_cog)

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

    async def _run_internal_tool(
        self,
        guild_id: Optional[int],
        fetcher: Callable[[TMDbClient], Any],
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
            data = await fetcher(client)
        except Exception as exc:
            self.logger.error("TMDb API error: %s", exc)
            return f"TMDb API error: {str(exc)}"

        if not spoiler_mode:
            data = censor_spoilers(data)

        try:
            # todo: re-enable toon-format output once fixed
            # return toon_format.dumps(data, indent=2)
            return data
        except Exception as exc:
            self.logger.error("Failed to format TMDb data: %s", exc)
            return f"TMDb data formatting error: {str(exc)}"

    async def _internal_search_movies(self, query: str, guild_id: int) -> str:
        return await self._run_internal_tool(guild_id, lambda client: client.search_movies(query))

    async def _internal_search_tv(self, query: str, guild_id: int) -> str:
        return await self._run_internal_tool(guild_id, lambda client: client.search_tv(query))

    async def _internal_movie_details(self, tmdb_id: int, guild_id: int) -> str:
        return await self._run_internal_tool(guild_id, lambda client: client.movie_details(tmdb_id))

    async def _internal_tv_details(self, tmdb_id: int, guild_id: int) -> str:
        return await self._run_internal_tool(guild_id, lambda client: client.tv_details(tmdb_id))

    async def _internal_movie_credits(self, tmdb_id: int, guild_id: int) -> str:
        return await self._run_internal_tool(guild_id, lambda client: client.movie_credits(tmdb_id))

    async def _internal_tv_credits(self, tmdb_id: int, guild_id: int) -> str:
        return await self._run_internal_tool(guild_id, lambda client: client.tv_credits(tmdb_id))

    async def query_spoilarr(
        self,
        query: str,
        guild_id: Optional[int] = None,
        channel_id: Optional[int] = None,
        member_id: Optional[int] = None,
    ) -> str:
        """Query TMDb via SpoilarrManager sub-agent and inject results into conversation."""

        self.logger.info("Spoilarr query received: %s", query)

        if guild_id is None or channel_id is None or member_id is None:
            return "Missing context parameters (guild_id, channel_id, member_id). Cannot query TMDb."

        settings = await self._get_settings(guild_id)
        if not settings["api_key"]:
            return "TMDb API key not configured. Use [p]spoilarr apikey <key>"

        if not self.manager:
            langcore_cog = self.bot.get_cog("langcore")
            self.manager = SpoilarrManager(self, langcore_cog)

        try:
            toon_str = await self.manager.handle_query(query, guild_id)
        except Exception as exc:
            self.logger.error("SpoilarrManager query failed: %s", exc)
            return f"Failed to query TMDb: {str(exc)}"

        langcore_cog = self.bot.get_cog("langcore")
        if not langcore_cog:
            return "Langcore cog unavailable; cannot inject TMDb data."
        conv_manager = getattr(langcore_cog, "conversation_manager", None)
        if not conv_manager:
            return "Conversation manager unavailable; cannot inject TMDb data."

        conversation = conv_manager.get_conversation(member_id, channel_id, guild_id)
        lock = conv_manager.get_conversation_lock(member_id, channel_id, guild_id)
        async with lock:
            conversation.add_assistant_message(toon_str)

        await self._handle_spoiler_instruction(member_id, channel_id, guild_id, settings["spoiler_mode"])

        return "✅ Spoilarr retrieved TMDb data (toon-formatted JSON added to conversation context)."

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
