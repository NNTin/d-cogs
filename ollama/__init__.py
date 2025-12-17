from __future__ import annotations

import os
import sys
from importlib.machinery import PathFinder

# NOTE:
# This cog is named `ollama`, which collides with the upstream PyPI package
# `ollama` used by `langchain_ollama`. When `langchain_ollama` does
# `from ollama import AsyncClient, Client, Message`, Python resolves it to this
# cog package first, causing a circular import.
#
# To avoid that, we extend this package's `__path__` to also include the
# site-packages `ollama` distribution, then re-export the upstream symbols
# expected by `langchain_ollama`.


def _extend_path_with_upstream_ollama() -> None:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))

    search_paths: list[str] = []
    for entry in sys.path:
        if not entry:
            entry_abs = os.path.abspath(os.getcwd())
        else:
            entry_abs = os.path.abspath(entry)

        if entry_abs == repo_root:
            continue
        search_paths.append(entry)

    spec = PathFinder.find_spec("ollama", search_paths)
    if not spec or not spec.submodule_search_locations:
        return

    for location in spec.submodule_search_locations:
        if location and location not in __path__:
            __path__.append(location)


_extend_path_with_upstream_ollama()

try:
    # Upstream (PyPI) `ollama` exports. These are required by `langchain_ollama`.
    from ollama._client import AsyncClient, Client  # type: ignore[import-not-found]
    from ollama._types import Message, Options, ResponseError  # type: ignore[import-not-found]
except Exception:  # noqa: BLE001
    # If upstream `ollama` isn't installed, let the cog fail later with a clear
    # error when its functionality is invoked.
    AsyncClient = None  # type: ignore[assignment]
    Client = None  # type: ignore[assignment]
    Message = None  # type: ignore[assignment]
    Options = None  # type: ignore[assignment]
    ResponseError = None  # type: ignore[assignment]

try:
    from redbot.core.bot import Red
    from redbot.core.utils import get_end_user_data_statement_or_raise
except Exception:  # noqa: BLE001
    Red = None  # type: ignore[assignment,misc]

    def get_end_user_data_statement_or_raise(_: str) -> str:  # type: ignore[override]
        return ""

from .ollama import ollama
from .models import OllamaConfig, OllamaGuildConfig

__red_end_user_data_statement__ = get_end_user_data_statement_or_raise(__file__)

__all__ = [
    "ollama",
    "OllamaGuildConfig",
    "OllamaConfig",
    "AsyncClient",
    "Client",
    "Message",
    "Options",
    "ResponseError",
]


async def setup(bot: Red) -> None:
    await bot.add_cog(ollama(bot))
