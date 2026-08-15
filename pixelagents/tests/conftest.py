"""Install stubs before any pixelagents module is imported."""
from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock


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
_discord.Message = MagicMock
_discord.File = MagicMock


class _ActivityType:
    playing = 0
    streaming = 1
    listening = 2
    watching = 3
    custom = 4
    competing = 5


_discord.ActivityType = _ActivityType
_discord.Object = MagicMock
_discord.Role = MagicMock


# discord.Interaction stub
class _FakeInteractionResponse:
    def __init__(self):
        self._done = False

    def is_done(self):
        return self._done

    async def send_message(self, *args, **kwargs):
        self._done = True

    async def send_modal(self, modal):
        self._done = True

    async def defer(self, **kwargs):
        self._done = True


class _FakeInteractionFollowup:
    async def send(self, *args, **kwargs):
        pass


class _FakeInteraction:
    def __init__(self, guild=None, user=None):
        self.guild = guild
        self.user = user or MagicMock()
        self.response = _FakeInteractionResponse()
        self.followup = _FakeInteractionFollowup()


_discord.Interaction = _FakeInteraction


# discord.ui stub
class _MockModal:
    title: str = ""

    def __init_subclass__(cls, title: str = "", **kwargs):
        cls.title = title
        super().__init_subclass__(**kwargs)

    def __init__(self, *args, **kwargs):
        super().__init__()

    async def on_submit(self, interaction):
        pass

    async def on_error(self, interaction, error):
        pass


class _MockTextInput:
    def __init__(self, *, label: str = "", placeholder: str = "", required: bool = True,
                 min_length: int = 0, max_length: int = 4000, **kwargs):
        self.label = label
        self.placeholder = placeholder
        self.required = required
        self.min_length = min_length
        self.max_length = max_length
        self.value = ""

    def __set_name__(self, owner, name):
        self._name = name


class _MockLayoutView:
    def __init__(self, *, timeout=180.0):
        self.timeout = timeout

    def add_item(self, item):
        pass


def _stub_ui_item(*args, **kwargs):
    # A bare MagicMock class can't stand in for these constructors: MagicMock's
    # own __init__ treats a first positional arg as `spec`, which would silently
    # restrict the returned mock's attributes (e.g. dropping `.add_item`).
    return MagicMock()


_discord_ui = _make_stub_module("discord.ui")
_discord_ui.Modal = _MockModal
_discord_ui.TextInput = _MockTextInput
_discord_ui.LayoutView = _MockLayoutView
_discord_ui.Container = _stub_ui_item
_discord_ui.Section = _stub_ui_item
_discord_ui.Thumbnail = _stub_ui_item
_discord_ui.TextDisplay = _stub_ui_item
_discord_ui.MediaGallery = _stub_ui_item
_discord_ui.ActionRow = _stub_ui_item
_discord_ui.Select = _stub_ui_item
_discord_ui.Button = _stub_ui_item
_discord.ui = _discord_ui
sys.modules["discord.ui"] = _discord_ui
_discord.SelectOption = _stub_ui_item
_discord.MediaGalleryItem = _stub_ui_item
_discord.ButtonStyle = MagicMock(secondary=None, primary=None, link=None)


# discord.app_commands stub
_discord_app_commands = _make_stub_module("discord.app_commands")
_discord_app_commands.describe = lambda **kwargs: (lambda f: f)
_discord_app_commands.choices = lambda **kwargs: (lambda f: f)
_discord_app_commands.Choice = lambda **kwargs: MagicMock(**kwargs)
_discord.app_commands = _discord_app_commands
sys.modules["discord.app_commands"] = _discord_app_commands

sys.modules["discord"] = _discord


# --- aiohttp ---
class _WSMsgType:
    TEXT = 1
    BINARY = 2
    PING = 9
    PONG = 10
    CLOSE = 8
    CLOSED = 256
    ERROR = 257


class _FakeWSMessage:
    def __init__(self, data: str = "", msg_type: int = 1):
        self.type = msg_type
        self.data = data


class _FakeClientWebSocketResponse:
    def __init__(self, messages=None):
        self._messages = list(messages or [])
        self.closed = False
        self._sent: list = []

    async def send_str(self, data: str) -> None:
        self._sent.append(data)

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._messages:
            self.closed = True
            raise StopAsyncIteration
        return self._messages.pop(0)


class _FakeClientSession:
    def __init__(self, ws_response=None, timeout=None, **kwargs):
        self._ws_response = ws_response or _FakeClientWebSocketResponse()
        self.closed = False

    async def ws_connect(self, url: str, **kwargs):
        return self._ws_response

    async def close(self) -> None:
        self.closed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()


_aiohttp = _make_stub_module(
    "aiohttp",
    ClientSession=_FakeClientSession,
    ClientWebSocketResponse=_FakeClientWebSocketResponse,
    WSMsgType=_WSMsgType,
    ClientTimeout=lambda **kwargs: kwargs,
)
sys.modules["aiohttp"] = _aiohttp


# --- redbot ---
_redbot = _make_stub_module("redbot")
_redbot_core = _make_stub_module("redbot.core")


class _FakeConfigAttr:
    def __init__(self, data, key):
        self._data = data
        self._key = key

    async def __call__(self):
        return self._data.get(self._key)

    async def set(self, value):
        self._data[self._key] = value


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
        return _FakeConfigAttr(self._data, name)


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
        return _FakeConfigAttr(self._global, name)


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
        interaction = None

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
    def hybrid_group(**kw):
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
