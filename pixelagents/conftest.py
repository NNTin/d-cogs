"""Install stubs before any pixelagents module is imported."""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock


def _make_stub_module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


# --- discord ---
_discord = _make_stub_module("discord")
_discord.Member = MagicMock
_discord.Guild = MagicMock
_discord.Color = MagicMock(blurple=MagicMock(return_value=None))
_discord.Embed = MagicMock
_discord.HTTPException = Exception
_discord.File = MagicMock


class _ActivityType:
    playing = 0
    streaming = 1
    listening = 2
    watching = 3
    custom = 4
    competing = 5


_discord.ActivityType = _ActivityType
sys.modules["discord"] = _discord


# --- redbot ---
_redbot = _make_stub_module("redbot")
_redbot_core = _make_stub_module("redbot.core")


class _FakeConfigAttr:
    def __init__(self, value):
        self._value = value

    async def __call__(self):
        return self._value

    async def set(self, value):
        self._value = value


class _FakeGuildConfigAttr:
    def __init__(self, data, key):
        self._data = data
        self._key = key

    async def __call__(self):
        return self._data.get(self._key)

    async def set(self, value):
        self._data[self._key] = value


class _FakeGuildConfig:
    def __init__(self, guild_id):
        self._data = {"enabled": False, "include_bots": True}

    def __getattr__(self, name):
        return _FakeGuildConfigAttr(self._data, name)


class _FakeUserConfig:
    def __init__(self, data):
        self._data = data

    def __getattr__(self, name):
        return _FakeGuildConfigAttr(self._data, name)


class _FakeConfig:
    _global: dict

    def __init__(self):
        self._global = {}
        self._user_defaults = {}
        self._users = {}

    @classmethod
    def get_conf(cls, cog, identifier=0, force_registration=False):
        return cls()

    def register_global(self, **defaults):
        for k, v in defaults.items():
            self._global.setdefault(k, v)

    def register_guild(self, **defaults):
        pass

    def register_user(self, **defaults):
        self._user_defaults.update(defaults)

    def guild(self, guild):
        return _FakeGuildConfig(guild.id if hasattr(guild, "id") else guild)

    def guild_from_id(self, guild_id):
        return _FakeGuildConfig(guild_id)

    def user(self, user):
        user_id = user.id if hasattr(user, "id") else int(user)
        data = self._users.setdefault(
            user_id,
            {key: (value.copy() if isinstance(value, dict) else value) for key, value in self._user_defaults.items()},
        )
        return _FakeUserConfig(data)

    def user_from_id(self, user_id):
        data = self._users.setdefault(
            user_id,
            {key: (value.copy() if isinstance(value, dict) else value) for key, value in self._user_defaults.items()},
        )
        return _FakeUserConfig(data)

    def __getattr__(self, name):
        return _FakeConfigAttr(self._global.get(name))


class _FakeGroup:
    """Stub for a Red command Group — supports `.command()` sub-decorator."""

    def __init__(self, func):
        self.__wrapped__ = func
        self.__name__ = getattr(func, "__name__", "group")
        self.__doc__ = getattr(func, "__doc__", "")

    def __call__(self, *args, **kwargs):
        return self.__wrapped__(*args, **kwargs)

    def command(self, **kw):
        def deco(f):
            return f
        return deco

    def group(self, **kw):
        def deco(f):
            return _FakeGroup(f)
        return deco


class _FakeListener:
    def __init__(self, func=None):
        self._func = func

    def __call__(self, func):
        return func


class _FakeCog:
    @staticmethod
    def listener(func=None):
        if func is not None:
            return func

        def deco(f):
            return f
        return deco


class _FakeCommands:
    Cog = _FakeCog

    class Context:
        pass

    @staticmethod
    def admin_or_permissions(**kw):
        def deco(f): return f
        return deco

    @staticmethod
    def guild_only():
        def deco(f): return f
        return deco

    @staticmethod
    def group(**kw):
        def deco(f):
            return _FakeGroup(f)
        return deco

    @staticmethod
    def command(**kw):
        def deco(f): return f
        return deco


_redbot_core.Config = _FakeConfig
_redbot_core.commands = _FakeCommands()
_redbot_core_bot = _make_stub_module("redbot.core.bot")
_redbot_core_bot.Red = object

sys.modules["redbot"] = _redbot
sys.modules["redbot.core"] = _redbot_core
sys.modules["redbot.core.bot"] = _redbot_core_bot
