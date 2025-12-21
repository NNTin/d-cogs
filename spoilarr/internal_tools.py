import logging
from typing import Any, Callable, Optional

import toon_format

from .censor import censor_spoilers
from .client import TMDbClient


logger = logging.getLogger("red.d_cogs.spoilarr.internal_tools")


async def run_internal_tool(
    cog,
    guild_id: Optional[int],
    fetcher: Callable[[TMDbClient], Any],
) -> str:
    """Execute a TMDb API call with error handling and optional censoring."""
    if guild_id is None:
        return "Missing guild context"

    settings = await cog._get_settings(guild_id)
    api_key = settings["api_key"]
    spoiler_mode = settings["spoiler_mode"]

    if not api_key:
        return "TMDb API key not configured. Use [p]spoilarr apikey <key>"

    client = cog._get_client(api_key)

    try:
        data = await fetcher(client)
    except Exception as exc:
        logger.error("TMDb API error: %s", exc)
        return f"TMDb API error: {str(exc)}"

    if not spoiler_mode:
        data = censor_spoilers(data)

    try:
        # todo: re-enable toon-format output once fixed
        # return toon_format.dumps(data, indent=2)
        return data
    except Exception as exc:
        logger.error("Failed to format TMDb data: %s", exc)
        return f"TMDb data formatting error: {str(exc)}"


async def internal_search_movies(cog, query: str, guild_id: int) -> str:
    return await run_internal_tool(cog, guild_id, lambda client: client.search_movies(query))


async def internal_search_tv(cog, query: str, guild_id: int) -> str:
    return await run_internal_tool(cog, guild_id, lambda client: client.search_tv(query))


async def internal_movie_details(cog, tmdb_id: int, guild_id: int) -> str:
    return await run_internal_tool(cog, guild_id, lambda client: client.movie_details(tmdb_id))


async def internal_tv_details(cog, tmdb_id: int, guild_id: int) -> str:
    return await run_internal_tool(cog, guild_id, lambda client: client.tv_details(tmdb_id))


async def internal_movie_credits(cog, tmdb_id: int, guild_id: int) -> str:
    return await run_internal_tool(cog, guild_id, lambda client: client.movie_credits(tmdb_id))


async def internal_tv_credits(cog, tmdb_id: int, guild_id: int) -> str:
    return await run_internal_tool(cog, guild_id, lambda client: client.tv_credits(tmdb_id))


async def internal_discover_movies(cog, page: int = 1, guild_id: int = None) -> str:
    # TODO: Use toon_format.dumps(result) when toon-format is re-enabled
    return await run_internal_tool(cog, guild_id, lambda client: client.discover_movies(page))


async def internal_discover_tv(cog, page: int = 1, guild_id: int = None) -> str:
    # TODO: Use toon_format.dumps(result) when toon-format is re-enabled
    return await run_internal_tool(cog, guild_id, lambda client: client.discover_tv(page))


async def internal_tv_airing_today(cog, page: int = 1, guild_id: int = None) -> str:
    # TODO: Use toon_format.dumps(result) when toon-format is re-enabled
    return await run_internal_tool(cog, guild_id, lambda client: client.tv_airing_today(page))


async def internal_tv_on_the_air(cog, page: int = 1, guild_id: int = None) -> str:
    # TODO: Use toon_format.dumps(result) when toon-format is re-enabled
    return await run_internal_tool(cog, guild_id, lambda client: client.tv_on_the_air(page))


async def internal_tv_popular(cog, page: int = 1, guild_id: int = None) -> str:
    # TODO: Use toon_format.dumps(result) when toon-format is re-enabled
    return await run_internal_tool(cog, guild_id, lambda client: client.tv_popular(page))


async def internal_tv_top_rated(cog, page: int = 1, guild_id: int = None) -> str:
    # TODO: Use toon_format.dumps(result) when toon-format is re-enabled
    return await run_internal_tool(cog, guild_id, lambda client: client.tv_top_rated(page))
