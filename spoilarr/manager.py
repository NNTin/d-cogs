import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional

import toon_format
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage, convert_to_messages
from . import internal_tools
from .prompts import SYSTEM_PROMPT


class SpoilarrManager:
    """Sub-agent responsible for orchestrating Spoilarr TMDb tool usage."""

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
            {
                "name": "discover_movies",
                "description": "Discover movies with optional filters and pagination.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "page": {
                            "type": "number",
                            "description": "Page number for pagination (default: 1)",
                        }
                    },
                    "required": [],
                },
            },
            {
                "name": "discover_tv",
                "description": "Discover TV shows with optional filters and pagination.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "page": {
                            "type": "number",
                            "description": "Page number for pagination (default: 1)",
                        }
                    },
                    "required": [],
                },
            },
            {
                "name": "tv_airing_today",
                "description": "Get TV shows airing today.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "page": {
                            "type": "number",
                            "description": "Page number for pagination (default: 1)",
                        }
                    },
                    "required": [],
                },
            },
            {
                "name": "tv_on_the_air",
                "description": "Get TV shows currently on the air.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "page": {
                            "type": "number",
                            "description": "Page number for pagination (default: 1)",
                        }
                    },
                    "required": [],
                },
            },
            {
                "name": "tv_popular",
                "description": "Get popular TV shows.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "page": {
                            "type": "number",
                            "description": "Page number for pagination (default: 1)",
                        }
                    },
                    "required": [],
                },
            },
            {
                "name": "tv_top_rated",
                "description": "Get top-rated TV shows.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "page": {
                            "type": "number",
                            "description": "Page number for pagination (default: 1)",
                        }
                    },
                    "required": [],
                },
            },
        ]

    def _build_callbacks(self, guild_id: int) -> Dict[str, Callable[..., Any]]:
        """Build callback dictionary that delegates to internal_tools functions."""

        async def _search_movies(query: str) -> Any:
            return await internal_tools.internal_search_movies(self.spoilarr_cog, query=query, guild_id=guild_id)

        async def _search_tv(query: str) -> Any:
            return await internal_tools.internal_search_tv(self.spoilarr_cog, query=query, guild_id=guild_id)

        async def _movie_details(tmdb_id: int) -> Any:
            return await internal_tools.internal_movie_details(self.spoilarr_cog, tmdb_id=tmdb_id, guild_id=guild_id)

        async def _tv_details(tmdb_id: int) -> Any:
            return await internal_tools.internal_tv_details(self.spoilarr_cog, tmdb_id=tmdb_id, guild_id=guild_id)

        async def _movie_credits(tmdb_id: int) -> Any:
            return await internal_tools.internal_movie_credits(self.spoilarr_cog, tmdb_id=tmdb_id, guild_id=guild_id)

        async def _tv_credits(tmdb_id: int) -> Any:
            return await internal_tools.internal_tv_credits(self.spoilarr_cog, tmdb_id=tmdb_id, guild_id=guild_id)

        async def _discover_movies(page: int = 1) -> Any:
            return await internal_tools.internal_discover_movies(self.spoilarr_cog, page=page, guild_id=guild_id)

        async def _discover_tv(page: int = 1) -> Any:
            return await internal_tools.internal_discover_tv(self.spoilarr_cog, page=page, guild_id=guild_id)

        async def _tv_airing_today(page: int = 1) -> Any:
            return await internal_tools.internal_tv_airing_today(self.spoilarr_cog, page=page, guild_id=guild_id)

        async def _tv_on_the_air(page: int = 1) -> Any:
            return await internal_tools.internal_tv_on_the_air(self.spoilarr_cog, page=page, guild_id=guild_id)

        async def _tv_popular(page: int = 1) -> Any:
            return await internal_tools.internal_tv_popular(self.spoilarr_cog, page=page, guild_id=guild_id)

        async def _tv_top_rated(page: int = 1) -> Any:
            return await internal_tools.internal_tv_top_rated(self.spoilarr_cog, page=page, guild_id=guild_id)

        return {
            "search_movies": _search_movies,
            "search_tv": _search_tv,
            "movie_details": _movie_details,
            "tv_details": _tv_details,
            "movie_credits": _movie_credits,
            "tv_credits": _tv_credits,
            "discover_movies": _discover_movies,
            "discover_tv": _discover_tv,
            "tv_airing_today": _tv_airing_today,
            "tv_on_the_air": _tv_on_the_air,
            "tv_popular": _tv_popular,
            "tv_top_rated": _tv_top_rated,
        }

    async def handle_query(self, query: str, guild_id: int) -> str:
        provider = self.langcore_cog.get_provider("ollama")
        if not provider:
            raise RuntimeError("SpoilarrManager could not find the ollama provider")

        llm = await provider.get_chat_llm(guild_id=guild_id)

        # maintaining own message list to track tool calls and responses
        # in future worth abstracting to a Conversation class so other ExtensionCogs can reuse
        messages_dict: List[Dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ]

        def _is_json_like(content: Any) -> bool:
            if isinstance(content, (dict, list)):
                return True
            if isinstance(content, str):
                stripped = content.strip()
                return stripped.startswith("{") or stripped.startswith("[")
            return False

        try:
            messages = convert_to_messages(messages_dict)
            callbacks = self._build_callbacks(guild_id)
            max_iterations = 10
            iteration = 0

            while iteration < max_iterations:
                iteration += 1

                bound_llm = llm.bind_tools(self._tool_schemas)
                ai_msg: AIMessage = await bound_llm.ainvoke(messages)
                messages.append(ai_msg)

                if not ai_msg.tool_calls:
                    if not _is_json_like(ai_msg.content):
                        self.logger.warning(
                            "Spoilarr agent produced non-JSON response, requesting JSON-only output (iteration %s)",
                            iteration,
                        )
                        messages.append(
                            SystemMessage(
                                content=(
                                    "Final reply must be toon.dumps(JSON) only. If you still need data, call the tools "
                                    "(search -> details -> credits). No prose—return the JSON string."
                                )
                            )
                        )
                        continue
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

        final_content: Any = ""
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                final_content = msg.content if msg.content else ""
                break

        if isinstance(final_content, (dict, list)):
            try:
                return toon_format.dumps(final_content, indent=2)
            except Exception:
                pass

        final_response = str(final_content).strip()

        if not final_response:
            self.logger.warning("Spoilarr agent loop produced no AI response content")
            try:
                return toon_format.dumps({"error": "Spoilarr agent produced no response"}, indent=2)
            except Exception:
                return '{"error": "Spoilarr agent produced no response"}'

        if not _is_json_like(final_response):
            self.logger.warning("Spoilarr agent returned non-JSON final response")
            try:
                return toon_format.dumps({"error": "Spoilarr agent returned non-JSON content"}, indent=2)
            except Exception:
                return '{"error": "Spoilarr agent returned non-JSON content"}'

        return final_response.strip()
