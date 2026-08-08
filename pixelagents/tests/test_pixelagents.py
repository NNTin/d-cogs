"""Unit tests for the pixelagents cog.

Stubs for discord / redbot / aiohttp are installed by conftest.py.
"""
from __future__ import annotations

import json
import asyncio
import base64
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch

from pixelagents.pixelagents import (
    _discord_id_to_agent_id,
    _JS_MAX_SAFE,
    _VISIBLE_STATUSES,
    pixelagents as PixelAgentsCog,
)
from pixelagents.tests.conftest import (
    _FakeConfig,
    _FakeInteraction,
    _FakeInteractionResponse,
    _FakeClientWebSocketResponse,
    _FakeWSMessage,
    _WSMsgType,
)

import discord  # stubbed by conftest
import aiohttp  # stubbed by conftest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _activity(activity_type, name="Some Game"):
    a = MagicMock()
    a.type = activity_type
    # `name` must be a real string: MagicMock's auto-attribute is not JSON
    # serializable, and presence labels are serialized onto the wire.
    a.name = name
    return a


def _member(guild_id=100, user_id=1, display_name="Tin", status="online",
            is_bot=False, activities=()):
    m = MagicMock()
    m.guild.id = guild_id
    m.id = user_id
    m.display_name = display_name
    m.status = status
    m.bot = is_bot
    m.activities = list(activities)
    return m


def _make_cog():
    bot = MagicMock()
    bot.guilds = []
    bot.is_owner = AsyncMock(return_value=False)
    cog = PixelAgentsCog.__new__(PixelAgentsCog)
    cog.bot = bot
    cfg = _FakeConfig()
    cfg._global = {
        "ws_host": "0.0.0.0",
        "ws_port": 3210,
        "message_tool_clear_delay": 2.0,
        "editor_role_id": None,
        "broadcast_rich_presence": True,
        "broadcast_messages": True,
        "layout": None,
        "seats": {},
    }
    cog.config = cfg
    cog._agents = {}
    cog._sync_task = None
    cog._presence_cache = {}
    cog._logged_collisions = set()
    cog._runner = None
    cog._clients = {}
    cog._tickets = {}
    cog._assets = {}
    cog._closing = False
    return cog


def _connect(cog, authorized=False):
    """Attach a fake office client to the cog and return it."""
    socket = _FakeClientWebSocketResponse()
    cog._clients[socket] = authorized
    return socket


def _sent_types(socket):
    return [json.loads(raw)["type"] for raw in socket._sent]


def _make_enabled_cog():
    cog = _make_cog()

    class _EnabledGuildConfig:
        def __getattr__(self, name):
            from pixelagents.tests.conftest import _FakeGuildConfigAttr
            data = {"enabled": True, "include_bots": True}
            return _FakeGuildConfigAttr(data, name)

    cog.config.guild = lambda guild: _EnabledGuildConfig()
    return cog


def _valid_layout():
    return {
        "version": 1,
        "cols": 2,
        "rows": 2,
        "tiles": [1, 1, 1, 1],
        "furniture": [],
    }


def _layout_ctx(user_id=12345):
    ctx = MagicMock()
    ctx.interaction = None
    ctx.send = AsyncMock()
    ctx.author.id = user_id
    ctx.guild.id = 100
    return ctx


# ---------------------------------------------------------------------------
# Tests: ID mapping
# ---------------------------------------------------------------------------

class TestDiscordIdToAgentId(unittest.TestCase):
    def test_output_is_negative(self):
        for uid in (1, 123456789, 987654321012345678):
            self.assertLess(_discord_id_to_agent_id(uid), 0)

    def test_within_js_safe_range(self):
        for uid in (1, 123456789, 987654321012345678):
            result = _discord_id_to_agent_id(uid)
            self.assertGreaterEqual(result, -_JS_MAX_SAFE)

    def test_stable_across_calls(self):
        uid = 123456789012345678
        self.assertEqual(_discord_id_to_agent_id(uid), _discord_id_to_agent_id(uid))

    def test_different_users_different_ids(self):
        self.assertNotEqual(_discord_id_to_agent_id(1), _discord_id_to_agent_id(2))

    def test_zero_modulo_edge_case(self):
        result = _discord_id_to_agent_id(_JS_MAX_SAFE)
        self.assertEqual(result, -_JS_MAX_SAFE)


# ---------------------------------------------------------------------------
# Tests: visible statuses
# ---------------------------------------------------------------------------

class TestVisibleStatuses(unittest.TestCase):
    def test_visible(self):
        for s in ("online", "idle", "dnd"):
            self.assertIn(s, _VISIBLE_STATUSES)

    def test_not_visible(self):
        for s in ("offline", "invisible"):
            self.assertNotIn(s, _VISIBLE_STATUSES)


# ---------------------------------------------------------------------------
# Tests: status/inclusion helpers
# ---------------------------------------------------------------------------

class TestStatusMapping(unittest.TestCase):
    def setUp(self):
        self.cog = _make_cog()

    def test_online_is_visible(self):
        self.assertEqual(self.cog._status_str(_member(status="online")), "online")

    def test_idle_is_visible(self):
        self.assertEqual(self.cog._status_str(_member(status="idle")), "idle")

    def test_dnd_is_visible(self):
        self.assertEqual(self.cog._status_str(_member(status="dnd")), "dnd")

    def test_offline_is_none(self):
        self.assertIsNone(self.cog._status_str(_member(status="offline")))

    def test_invisible_is_none(self):
        self.assertIsNone(self.cog._status_str(_member(status="invisible")))


class TestBotInclusion(unittest.TestCase):
    def setUp(self):
        self.cog = _make_cog()

    def test_bot_included_by_default(self):
        self.assertTrue(self.cog._is_included(_member(is_bot=True), include_bots=True))

    def test_bot_excluded_when_disabled(self):
        self.assertFalse(self.cog._is_included(_member(is_bot=True), include_bots=False))

    def test_human_always_included(self):
        self.assertTrue(self.cog._is_included(_member(is_bot=False), include_bots=False))


class TestRichPresence(unittest.TestCase):
    def setUp(self):
        self.cog = _make_cog()

    def test_no_activities_is_waiting(self):
        m = _member(activities=[])
        self.assertEqual(self.cog._agent_status(m), "waiting")

    def test_game_activity_is_active(self):
        m = _member(activities=[_activity(discord.ActivityType.playing)])
        self.assertEqual(self.cog._agent_status(m), "active")

    def test_custom_activity_only_is_waiting(self):
        m = _member(activities=[_activity(discord.ActivityType.custom)])
        self.assertEqual(self.cog._agent_status(m), "waiting")

    def test_custom_plus_game_is_active(self):
        m = _member(activities=[
            _activity(discord.ActivityType.custom),
            _activity(discord.ActivityType.playing),
        ])
        self.assertEqual(self.cog._agent_status(m), "active")


# ---------------------------------------------------------------------------
# Tests: WebSocket send
# ---------------------------------------------------------------------------

class TestSend(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_cog()
        self.ws = _connect(self.cog)

    async def test_send_serializes_json(self):
        await self.cog._send({"type": "agentClosed", "id": -42})
        self.assertEqual(len(self.ws._sent), 1)
        parsed = json.loads(self.ws._sent[0])
        self.assertEqual(parsed["type"], "agentClosed")

    async def test_send_reaches_every_client(self):
        other = _connect(self.cog)
        await self.cog._send({"type": "agentClosed", "id": -42})
        self.assertEqual(len(self.ws._sent), 1)
        self.assertEqual(len(other._sent), 1)

    async def test_send_noop_when_no_clients(self):
        self.cog._clients = {}
        await self.cog._send({"type": "test"})

    async def test_send_drops_closed_clients(self):
        self.ws.closed = True
        await self.cog._send({"type": "test"})
        self.assertEqual(len(self.ws._sent), 0)
        self.assertNotIn(self.ws, self.cog._clients)


# ---------------------------------------------------------------------------
# Tests: bootstrap on webviewReady
# ---------------------------------------------------------------------------

class TestBootstrap(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_cog()
        self.cog._assets = {
            "characters": [{"down": [], "up": [], "right": []}],
            "floors": [[["#000"]]],
            "walls": [[[["#000"]]]],
            "carpets": [[[["#000"]]]],
            "furniture": {"DESK": [["#000"]]},
            "catalog": [{"id": "DESK", "name": "Desk", "label": "Desk", "category": "desks",
                         "file": "DESK.png", "width": 16, "height": 16, "footprintW": 1,
                         "footprintH": 1, "isDesk": True, "canPlaceOnWalls": False}],
        }
        self.cog._default_layout = lambda: _valid_layout()
        self.ws = _connect(self.cog)

    async def test_capabilities_arrive_first(self):
        await self.cog._send_bootstrap(self.ws)
        self.assertEqual(_sent_types(self.ws)[0], "providerCapabilities")

    async def test_layout_arrives_after_existing_agents(self):
        """The webview buffers existingAgents and only builds characters on
        layoutLoaded, so a layout-first bootstrap renders an empty office."""
        await self.cog._send_bootstrap(self.ws)
        types = _sent_types(self.ws)
        self.assertLess(types.index("existingAgents"), types.index("layoutLoaded"))

    async def test_sends_every_asset_family(self):
        await self.cog._send_bootstrap(self.ws)
        types = _sent_types(self.ws)
        for expected in (
            "characterSpritesLoaded", "floorTilesLoaded", "wallTilesLoaded",
            "carpetTilesLoaded", "furnitureAssetsLoaded", "settingsLoaded",
        ):
            self.assertIn(expected, types)

    async def test_replays_presence_bubbles_after_layout(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        self.cog._presence_cache[(100, 1)] = "Spotify"
        await self.cog._send_bootstrap(self.ws)
        types = _sent_types(self.ws)
        self.assertGreater(types.index("agentToolStart"), types.index("layoutLoaded"))


# ---------------------------------------------------------------------------
# Tests: dashboard webview hosting
# ---------------------------------------------------------------------------

class TestDashboardWebviewHosting(unittest.IsolatedAsyncioTestCase):
    async def test_static_asset_returns_raw_response(self):
        cog = _make_cog()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "assets").mkdir()
            (root / "assets" / "index-test.js").write_text("console.log('ok');", encoding="utf-8")
            cog._webview_dist_root = lambda: root

            result = await cog.dashboard_static("assets/index-test.js")

        self.assertEqual(result["status"], 0)
        raw = result["raw_response"]
        self.assertEqual(raw["content_type"], "text/javascript; charset=utf-8")
        self.assertEqual(raw["headers"]["Cache-Control"], "public, max-age=3600")
        self.assertEqual(base64.b64decode(raw["body_base64"]).decode("utf-8"), "console.log('ok');")

    async def test_static_asset_rejects_path_traversal(self):
        cog = _make_cog()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "index.html").write_text("ok", encoding="utf-8")
            cog._webview_dist_root = lambda: root

            result = await cog.dashboard_static("../index.html")

        self.assertEqual(result["status"], 1)
        self.assertEqual(result["error_code"], 404)

    async def test_dashboard_webview_returns_index_html(self):
        cog = _make_cog()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "index.html").write_text("<!doctype html><div id=\"root\"></div>", encoding="utf-8")
            cog._webview_dist_root = lambda: root

            result = await cog.dashboard_webview()

        self.assertEqual(result["status"], 0)
        self.assertTrue(result["web_content"]["standalone"])
        self.assertIn("root", result["web_content"]["source"])


# ---------------------------------------------------------------------------
# Tests: send_existing_agents
# ---------------------------------------------------------------------------

class TestSendExistingAgents(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_cog()
        self.ws = _connect(self.cog)

    async def test_empty_agents(self):
        await self.cog._send_existing_agents()
        msg = json.loads(self.ws._sent[0])
        self.assertEqual(msg["type"], "existingAgents")
        self.assertEqual(msg["agents"], [])

    async def test_single_agent(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        await self.cog._send_existing_agents()
        msg = json.loads(self.ws._sent[0])
        expected_id = _discord_id_to_agent_id(1)
        self.assertIn(expected_id, msg["agents"])
        self.assertEqual(msg["folderNames"][str(expected_id)], "online")

    async def test_same_user_two_guilds_deduplicated(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        self.cog._agents[(200, 1)] = ("idle", "Tin")
        await self.cog._send_existing_agents()
        msg = json.loads(self.ws._sent[0])
        self.assertEqual(len(msg["agents"]), 1)


# ---------------------------------------------------------------------------
# Tests: reconcile member
# ---------------------------------------------------------------------------

class TestReconcileMember(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_cog()
        self.ws = _connect(self.cog)

    async def test_new_visible_member_spawns(self):
        m = _member(status="online")
        await self.cog._reconcile_member(m, include_bots=True)
        sent_types = [json.loads(s)["type"] for s in self.ws._sent]
        self.assertIn("agentCreated", sent_types)

    async def test_spawn_sets_folder_name(self):
        m = _member(status="dnd")
        await self.cog._reconcile_member(m, include_bots=True)
        created = next(json.loads(s) for s in self.ws._sent if json.loads(s)["type"] == "agentCreated")
        self.assertEqual(created["folderName"], "dnd")

    async def test_spawn_sets_agent_name_via_team_info(self):
        m = _member(status="online", display_name="Alice")
        await self.cog._reconcile_member(m, include_bots=True)
        team_info = next(json.loads(s) for s in self.ws._sent if json.loads(s)["type"] == "agentTeamInfo")
        self.assertEqual(team_info["agentName"], "Alice")

    async def test_spawn_sends_status(self):
        m = _member(status="online", activities=[_activity(discord.ActivityType.playing)])
        await self.cog._reconcile_member(m, include_bots=True)
        status_msg = next(json.loads(s) for s in self.ws._sent if json.loads(s)["type"] == "agentStatus")
        self.assertEqual(status_msg["status"], "active")

    async def test_offline_member_not_spawned(self):
        m = _member(status="offline")
        await self.cog._reconcile_member(m, include_bots=True)
        sent_types = [json.loads(s)["type"] for s in self.ws._sent]
        self.assertNotIn("agentCreated", sent_types)

    async def test_offline_cached_member_closed(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        m = _member(status="offline")
        await self.cog._reconcile_member(m, include_bots=True)
        sent_types = [json.loads(s)["type"] for s in self.ws._sent]
        self.assertIn("agentClosed", sent_types)
        self.assertNotIn((100, 1), self.cog._agents)

    async def test_folder_change_closes_and_respawns(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        m = _member(status="dnd")
        await self.cog._reconcile_member(m, include_bots=True)
        sent_types = [json.loads(s)["type"] for s in self.ws._sent]
        self.assertIn("agentClosed", sent_types)
        self.assertIn("agentCreated", sent_types)

    async def test_name_change_only_sends_team_info(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        m = _member(status="online", display_name="Newname")
        await self.cog._reconcile_member(m, include_bots=True)
        sent_types = [json.loads(s)["type"] for s in self.ws._sent]
        self.assertNotIn("agentClosed", sent_types)
        self.assertNotIn("agentCreated", sent_types)
        self.assertIn("agentTeamInfo", sent_types)

    async def test_no_change_sends_nothing(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        m = _member(status="online", display_name="Tin")
        await self.cog._reconcile_member(m, include_bots=True)
        self.assertEqual(len(self.ws._sent), 0)

    async def test_bot_excluded_when_include_bots_false(self):
        m = _member(status="online", is_bot=True)
        await self.cog._reconcile_member(m, include_bots=False)
        self.assertNotIn((100, 1), self.cog._agents)

    async def test_bot_cached_excluded_closes(self):
        self.cog._agents[(100, 99)] = ("online", "BotName")
        m = _member(user_id=99, status="online", is_bot=True)
        await self.cog._reconcile_member(m, include_bots=False)
        sent_types = [json.loads(s)["type"] for s in self.ws._sent]
        self.assertIn("agentClosed", sent_types)


# ---------------------------------------------------------------------------
# Tests: close agent
# ---------------------------------------------------------------------------

class TestCloseAgent(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_cog()
        self.ws = _connect(self.cog)

    async def test_close_sends_agent_closed(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        await self.cog._close_agent(100, 1)
        sent_types = [json.loads(s)["type"] for s in self.ws._sent]
        self.assertIn("agentClosed", sent_types)

    async def test_close_removes_from_registry(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        await self.cog._close_agent(100, 1)
        self.assertNotIn((100, 1), self.cog._agents)

    async def test_close_nonexistent_is_noop(self):
        await self.cog._close_agent(100, 999)
        self.assertEqual(len(self.ws._sent), 0)

    async def test_close_user_active_in_other_guild_does_not_send_closed(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        self.cog._agents[(200, 1)] = ("idle", "Tin")
        await self.cog._close_agent(100, 1)
        sent_types = [json.loads(s)["type"] for s in self.ws._sent]
        self.assertNotIn("agentClosed", sent_types)


# ---------------------------------------------------------------------------
# Tests: auth check
# ---------------------------------------------------------------------------

class TestCheckAuth(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_cog()

    async def test_zero_user_id_denied(self):
        self.assertFalse(await self.cog._check_auth(0))

    async def test_bot_owner_allowed(self):
        self.cog.bot.is_owner = AsyncMock(return_value=True)
        self.assertTrue(await self.cog._check_auth(12345))

    async def test_no_role_configured_denied(self):
        self.cog.config._global["editor_role_id"] = None
        self.assertFalse(await self.cog._check_auth(12345))

    async def test_role_match_allows(self):
        role_id = 999
        self.cog.config._global["editor_role_id"] = role_id

        role = MagicMock()
        role.id = role_id

        member = MagicMock()
        member.roles = [role]

        guild = MagicMock()
        guild.get_member = MagicMock(return_value=member)

        self.cog.bot.guilds = [guild]

        async def _enabled():
            return True

        guild_cfg = MagicMock()
        guild_cfg.enabled = _enabled
        self.cog.config.guild = MagicMock(return_value=guild_cfg)

        self.assertTrue(await self.cog._check_auth(12345))
        guild.fetch_member.assert_not_called()

    async def test_uncached_role_match_fetches_member_and_allows(self):
        role_id = 999
        self.cog.config._global["editor_role_id"] = role_id

        role = MagicMock()
        role.id = role_id

        member = MagicMock()
        member.roles = [role]
        member.guild_permissions.administrator = False

        guild = MagicMock()
        guild.get_member = MagicMock(return_value=None)
        guild.fetch_member = AsyncMock(return_value=member)

        self.cog.bot.guilds = [guild]

        async def _enabled():
            return True

        guild_cfg = MagicMock()
        guild_cfg.enabled = _enabled
        self.cog.config.guild = MagicMock(return_value=guild_cfg)

        self.assertTrue(await self.cog._check_auth(12345))
        guild.fetch_member.assert_awaited_once_with(12345)

    async def test_enabled_guild_admin_allows(self):
        member = MagicMock()
        member.guild_permissions.administrator = True
        member.roles = []

        guild = MagicMock()
        guild.get_member = MagicMock(return_value=member)
        self.cog.bot.guilds = [guild]

        async def _enabled():
            return True

        guild_cfg = MagicMock()
        guild_cfg.enabled = _enabled
        self.cog.config.guild = MagicMock(return_value=guild_cfg)

        self.assertTrue(await self.cog._check_auth(12345))

    async def test_uncached_admin_fetches_member_and_allows(self):
        member = MagicMock()
        member.guild_permissions.administrator = True
        member.roles = []

        guild = MagicMock()
        guild.get_member = MagicMock(return_value=None)
        guild.fetch_member = AsyncMock(return_value=member)
        self.cog.bot.guilds = [guild]

        async def _enabled():
            return True

        guild_cfg = MagicMock()
        guild_cfg.enabled = _enabled
        self.cog.config.guild = MagicMock(return_value=guild_cfg)

        self.assertTrue(await self.cog._check_auth(12345))
        guild.fetch_member.assert_awaited_once_with(12345)

    async def test_no_role_match_denied(self):
        role_id = 999
        self.cog.config._global["editor_role_id"] = role_id

        other_role = MagicMock()
        other_role.id = 888

        member = MagicMock()
        member.roles = [other_role]

        guild = MagicMock()
        guild.get_member = MagicMock(return_value=member)
        self.cog.bot.guilds = [guild]

        async def _enabled():
            return True

        guild_cfg = MagicMock()
        guild_cfg.enabled = _enabled
        self.cog.config.guild = MagicMock(return_value=guild_cfg)

        self.assertFalse(await self.cog._check_auth(12345))

    async def test_uncached_member_fetch_failure_denied(self):
        role_id = 999
        self.cog.config._global["editor_role_id"] = role_id

        guild = MagicMock()
        guild.get_member = MagicMock(return_value=None)
        guild.fetch_member = AsyncMock(side_effect=Exception("not found"))
        self.cog.bot.guilds = [guild]

        async def _enabled():
            return True

        guild_cfg = MagicMock()
        guild_cfg.enabled = _enabled
        self.cog.config.guild = MagicMock(return_value=guild_cfg)

        self.assertFalse(await self.cog._check_auth(12345))
        guild.fetch_member.assert_awaited_once_with(12345)


# ---------------------------------------------------------------------------
# Tests: handle_server_message
# ---------------------------------------------------------------------------

class TestHandleClientMessage(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_cog()
        self.viewer = _connect(self.cog, authorized=False)
        self.editor = _connect(self.cog, authorized=True)

    async def test_webview_ready_triggers_bootstrap(self):
        self.cog._send_bootstrap = AsyncMock()
        await self.cog._handle_client_message(self.viewer, {"type": "webviewReady"})
        self.cog._send_bootstrap.assert_awaited_once_with(self.viewer)

    async def test_viewer_cannot_save_layout(self):
        await self.cog._handle_client_message(
            self.viewer, {"type": "saveLayout", "layout": _valid_layout()}
        )
        self.assertIsNone(await self.cog.config.layout())

    async def test_editor_can_save_layout(self):
        layout = _valid_layout()
        await self.cog._handle_client_message(self.editor, {"type": "saveLayout", "layout": layout})
        self.assertEqual(await self.cog.config.layout(), layout)

    async def test_saved_layout_is_mirrored_to_other_tabs(self):
        await self.cog._handle_client_message(
            self.editor, {"type": "saveLayout", "layout": _valid_layout()}
        )
        self.assertIn("layoutLoaded", _sent_types(self.viewer))
        # The saving client already applied it locally; echoing would be noise.
        self.assertEqual(_sent_types(self.editor), [])

    async def test_invalid_layout_is_rejected(self):
        await self.cog._handle_client_message(
            self.editor, {"type": "saveLayout", "layout": {"version": 99}}
        )
        self.assertIsNone(await self.cog.config.layout())

    async def test_viewer_cannot_save_seats(self):
        await self.cog._handle_client_message(
            self.viewer, {"type": "saveAgentSeats", "seats": {"-1": {"seatId": "a"}}}
        )
        self.assertEqual(await self.cog.config.seats(), {})

    async def test_editor_seats_are_persisted(self):
        await self.cog._handle_client_message(
            self.editor,
            {"type": "saveAgentSeats", "seats": {"-1": {"seatId": "chair:1", "palette": 2}}},
        )
        seats = await self.cog.config.seats()
        self.assertEqual(seats["-1"]["seatId"], "chair:1")
        self.assertEqual(seats["-1"]["palette"], 2)

    async def test_out_of_range_palette_is_ignored(self):
        await self.cog._handle_client_message(
            self.editor, {"type": "saveAgentSeats", "seats": {"-1": {"palette": 999}}}
        )
        self.assertNotIn("palette", (await self.cog.config.seats())["-1"])


class TestTicketInjection(unittest.IsolatedAsyncioTestCase):
    async def _render(self, html):
        cog = _make_cog()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "index.html").write_text(html, encoding="utf-8")
            cog._webview_dist_root = lambda: root
            result = await cog.dashboard_webview()
        return cog, result["web_content"]["source"]

    async def test_shim_is_injected_before_the_bundle(self):
        html = '<!doctype html><head><script src="/app.js"></script></head><body></body>'
        _, source = await self._render(html)
        # The constructor must be patched before the module bundle runs, or the
        # socket is opened without a ticket.
        self.assertLess(source.index("window.WebSocket = Patched"), source.index("/app.js"))

    async def test_office_page_mints_no_ticket(self):
        """The office is public; identity only exists on the editor page."""
        cog, source = await self._render("<!doctype html><head></head><body></body>")
        self.assertEqual(cog._tickets, {})
        self.assertIn("localStorage.getItem", source)

    async def test_headless_document_still_gets_the_shim(self):
        _, source = await self._render("<div id='root'></div>")
        self.assertIn("window.WebSocket = Patched", source)


class TestEditorPage(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = _make_cog()
        self.cog.bot.is_owner = AsyncMock(return_value=True)

    async def test_authorized_user_gets_a_working_ticket(self):
        result = await self.cog.dashboard_editor(
            user_id=777, request_url="https://pico.nntin.xyz/third-party/pixelagents/editor"
        )
        source = result["web_content"]["source"]
        ticket = next(iter(self.cog._tickets))
        self.assertIn(ticket, source)
        self.assertIn("localStorage.setItem", source)
        self.assertEqual(self.cog._resolve_ticket(ticket), 777)

    async def test_unauthorized_user_gets_no_ticket(self):
        self.cog.bot.is_owner = AsyncMock(return_value=False)
        result = await self.cog.dashboard_editor(
            user_id=777, request_url="https://pico.nntin.xyz/third-party/pixelagents/editor"
        )
        self.assertEqual(self.cog._tickets, {})
        self.assertIn("not authorized", result["web_content"]["source"])

    async def test_redirects_back_to_the_office(self):
        result = await self.cog.dashboard_editor(
            user_id=777, request_url="https://pico.nntin.xyz/third-party/pixelagents/editor"
        )
        self.assertIn("/third-party/pixelagents", result["web_content"]["source"])
        self.assertNotIn("/editor", result["web_content"]["source"])


class TestOfficeUrlDerivation(unittest.TestCase):
    def setUp(self):
        self.cog = _make_cog()

    def test_strips_editor_segment(self):
        self.assertEqual(
            self.cog._office_url("https://pico.nntin.xyz/third-party/pixelagents/editor"),
            "/third-party/pixelagents",
        )

    def test_ignores_query_string(self):
        self.assertEqual(
            self.cog._office_url("https://pico.nntin.xyz/third-party/pixelagents/editor?x=1"),
            "/third-party/pixelagents",
        )

    def test_tolerates_trailing_slash(self):
        self.assertEqual(
            self.cog._office_url("https://pico.nntin.xyz/third-party/pixelagents/editor/"),
            "/third-party/pixelagents",
        )

    def test_falls_back_when_url_is_missing(self):
        self.assertEqual(self.cog._office_url(None), "/third-party/pixelagents")


class TestEditorTickets(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = _make_cog()

    def test_minted_ticket_resolves_to_user(self):
        ticket = self.cog._mint_ticket(4242)
        self.assertEqual(self.cog._resolve_ticket(ticket), 4242)

    def test_unknown_ticket_resolves_to_none(self):
        self.assertIsNone(self.cog._resolve_ticket("nope"))

    def test_expired_ticket_is_rejected_and_dropped(self):
        ticket = self.cog._mint_ticket(1)
        user_id, _ = self.cog._tickets[ticket]
        self.cog._tickets[ticket] = (user_id, 0.0)
        self.assertIsNone(self.cog._resolve_ticket(ticket))
        self.assertNotIn(ticket, self.cog._tickets)

    def test_minting_evicts_expired_tickets(self):
        stale = self.cog._mint_ticket(1)
        self.cog._tickets[stale] = (1, 0.0)
        self.cog._mint_ticket(2)
        self.assertNotIn(stale, self.cog._tickets)


class TestLayoutOwnership(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_cog()

    async def test_saved_layout_wins_over_bundled_default(self):
        layout = _valid_layout()
        await self.cog.config.layout.set(layout)
        self.cog._default_layout = lambda: {"version": 1, "cols": 9, "rows": 9,
                                            "tiles": [0] * 81, "furniture": []}
        self.assertEqual(await self.cog._current_layout(), layout)

    async def test_falls_back_to_bundled_default(self):
        default = _valid_layout()
        self.cog._default_layout = lambda: default
        self.assertEqual(await self.cog._current_layout(), default)


# ---------------------------------------------------------------------------
# Tests: listener routing
# ---------------------------------------------------------------------------

class TestMemberUpdateListener(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_enabled_cog()
        self.cog._reconcile_member = AsyncMock()

    async def test_name_change_reconciles(self):
        before = _member(display_name="Old")
        after = _member(display_name="New")
        await self.cog.on_member_update(before, after)
        self.cog._reconcile_member.assert_awaited_once()

    async def test_no_name_change_skips(self):
        before = _member(display_name="Same", status="online")
        after = _member(display_name="Same", status="dnd")
        await self.cog.on_member_update(before, after)
        self.cog._reconcile_member.assert_not_awaited()


class TestPresenceUpdateListener(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_enabled_cog()
        self.cog._reconcile_member = AsyncMock()

    async def test_status_change_reconciles(self):
        before = _member(status="online")
        after = _member(status="idle")
        await self.cog.on_presence_update(before, after)
        self.cog._reconcile_member.assert_awaited_once()

    async def test_activity_change_reconciles(self):
        before = _member(activities=[])
        after = _member(activities=[_activity(discord.ActivityType.playing)])
        await self.cog.on_presence_update(before, after)
        self.cog._reconcile_member.assert_awaited_once()

    async def test_no_change_skips(self):
        before = _member(status="online", activities=[])
        after = _member(status="online", activities=[])
        await self.cog.on_presence_update(before, after)
        self.cog._reconcile_member.assert_not_awaited()

    async def test_disabled_guild_skips(self):
        cog = _make_cog()
        cog._reconcile_member = AsyncMock()
        before = _member(status="online")
        after = _member(status="dnd")
        await cog.on_presence_update(before, after)
        cog._reconcile_member.assert_not_awaited()


class TestMemberJoinListener(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_enabled_cog()
        self.cog._reconcile_member = AsyncMock()

    async def test_visible_member_reconciles(self):
        m = _member(status="online")
        await self.cog.on_member_join(m)
        self.cog._reconcile_member.assert_awaited_once()

    async def test_offline_member_skips(self):
        m = _member(status="offline")
        await self.cog.on_member_join(m)
        self.cog._reconcile_member.assert_not_awaited()


class TestMemberRemoveListener(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_enabled_cog()
        self.cog._close_agent = AsyncMock()

    async def test_remove_calls_close(self):
        m = _member(guild_id=100, user_id=42)
        await self.cog.on_member_remove(m)
        self.cog._close_agent.assert_awaited_once_with(100, 42)


# ---------------------------------------------------------------------------
# Tests: on_message
# ---------------------------------------------------------------------------

class TestOnMessage(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_enabled_cog()
        self.ws = _connect(self.cog)

    async def test_message_sends_tool_start(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        msg = MagicMock()
        msg.guild.id = 100
        msg.author.id = 1
        msg.content = "Hello world"
        msg.id = 999
        await self.cog.on_message(msg)
        sent_types = [json.loads(s)["type"] for s in self.ws._sent]
        self.assertIn("agentToolStart", sent_types)

    async def test_message_truncates_long_content(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        msg = MagicMock()
        msg.guild.id = 100
        msg.author.id = 1
        msg.content = "x" * 100
        msg.id = 1
        await self.cog.on_message(msg)
        tool_msg = next(json.loads(s) for s in self.ws._sent if json.loads(s)["type"] == "agentToolStart")
        self.assertLessEqual(len(tool_msg["status"]), 45)

    async def test_message_ignored_if_not_tracked(self):
        msg = MagicMock()
        msg.guild.id = 100
        msg.author.id = 999
        msg.content = "hi"
        await self.cog.on_message(msg)
        self.assertEqual(len(self.ws._sent), 0)

    async def test_message_ignored_in_dm(self):
        msg = MagicMock()
        msg.guild = None
        await self.cog.on_message(msg)
        self.assertEqual(len(self.ws._sent), 0)


# ---------------------------------------------------------------------------
# Tests: commands
# ---------------------------------------------------------------------------

class TestToolClearDelayCommand(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = _make_cog()

    async def test_set_valid_delay(self):
        ctx = MagicMock()
        ctx.interaction = None
        ctx.send = AsyncMock()
        await self.cog.cmd_toolcleardelay(ctx, 5.0)
        self.assertEqual(await self.cog.config.message_tool_clear_delay(), 5.0)
        ctx.send.assert_awaited_once()
        self.assertIn("5.0", ctx.send.call_args[0][0])

    async def test_negative_delay_rejected(self):
        ctx = MagicMock()
        ctx.interaction = None
        ctx.send = AsyncMock()
        await self.cog.cmd_toolcleardelay(ctx, -1.0)
        self.assertEqual(await self.cog.config.message_tool_clear_delay(), 2.0)


class TestWsPortCommand(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = _make_cog()

    def _ctx(self):
        ctx = MagicMock()
        ctx.interaction = None
        ctx.send = AsyncMock()
        return ctx

    async def test_sets_port(self):
        await self.cog.cmd_wsport(self._ctx(), 4300)
        self.assertEqual(await self.cog.config.ws_port(), 4300)

    async def test_rejects_out_of_range_port(self):
        await self.cog.cmd_wsport(self._ctx(), 70000)
        self.assertEqual(await self.cog.config.ws_port(), 3210)


class TestLayoutCommands(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = _make_cog()
        self.cog.bot.is_owner = AsyncMock(return_value=True)

    async def test_save_stores_snapshot(self):
        self.cog._current_layout = AsyncMock(return_value=_valid_layout())
        ctx = _layout_ctx()

        await self.cog.cmd_layout_save(ctx, "Office")

        layouts = await self.cog.config.user(ctx.author).layouts()
        self.assertIn("office", layouts)
        self.assertEqual(layouts["office"]["display_name"], "Office")
        ctx.send.assert_awaited()

    async def test_save_rejects_duplicate_without_overwrite(self):
        self.cog._current_layout = AsyncMock(return_value=_valid_layout())
        ctx = _layout_ctx()

        await self.cog.cmd_layout_save(ctx, "Office")
        await self.cog.cmd_layout_save(ctx, "office")

        self.assertIn("already exists", ctx.send.call_args[0][0])

    async def test_save_overwrites_existing(self):
        self.cog._current_layout = AsyncMock(return_value=_valid_layout())
        ctx = _layout_ctx()

        await self.cog.cmd_layout_save(ctx, "Office")
        await self.cog.cmd_layout_save(ctx, "Office", overwrite=True)

        layouts = await self.cog.config.user(ctx.author).layouts()
        self.assertEqual(len(layouts), 1)
        self.assertIn("Overwrote", ctx.send.call_args[0][0])

    async def test_load_stores_layout_and_pushes_it_to_open_tabs(self):
        layout = _valid_layout()
        self.cog._current_layout = AsyncMock(return_value=layout)
        client = _connect(self.cog)
        ctx = _layout_ctx()

        await self.cog.cmd_layout_save(ctx, "Office")
        await self.cog.cmd_layout_load(ctx, "Office")

        self.assertEqual(await self.cog.config.layout(), layout)
        self.assertIn("layoutLoaded", _sent_types(client))
        self.assertIn("Loaded", ctx.send.call_args[0][0])

    async def test_delete_removes_only_requested_layout(self):
        self.cog._current_layout = AsyncMock(return_value=_valid_layout())
        ctx = _layout_ctx()

        await self.cog.cmd_layout_save(ctx, "Office")
        await self.cog.cmd_layout_delete(ctx, "Office")

        layouts = await self.cog.config.user(ctx.author).layouts()
        self.assertEqual(layouts, {})

    async def test_share_uploads_public_file(self):
        self.cog._current_layout = AsyncMock(return_value=_valid_layout())
        ctx = _layout_ctx()

        await self.cog.cmd_layout_save(ctx, "Office")
        await self.cog.cmd_layout_share(ctx, "Office")

        _, kwargs = ctx.send.call_args
        self.assertIn("file", kwargs)

    async def test_unauthorized_user_cannot_save(self):
        self.cog.bot.is_owner = AsyncMock(return_value=False)
        self.cog._current_layout = AsyncMock()
        ctx = _layout_ctx()

        await self.cog.cmd_layout_save(ctx, "Office")

        self.cog._current_layout.assert_not_awaited()
        self.assertIn("not authorized", ctx.send.call_args[0][0])


class TestReplyHelper(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = _make_cog()

    async def test_prefix_uses_ctx_send(self):
        ctx = MagicMock()
        ctx.interaction = None
        ctx.send = AsyncMock()
        await self.cog._reply(ctx, "hello")
        ctx.send.assert_awaited_once_with("hello")

    async def test_slash_uses_response_send_message(self):
        ctx = MagicMock()
        interaction = _FakeInteraction(guild=MagicMock())
        ctx.interaction = interaction
        sent = []

        async def _capture(*args, **kwargs):
            sent.append((args, kwargs))
            interaction.response._done = True

        interaction.response.send_message = _capture
        await self.cog._reply(ctx, "hello")
        self.assertEqual(len(sent), 1)
        _, kwargs = sent[0]
        self.assertTrue(kwargs.get("ephemeral"))

    async def test_slash_after_defer_uses_followup(self):
        ctx = MagicMock()
        interaction = _FakeInteraction(guild=MagicMock())
        interaction.response._done = True
        ctx.interaction = interaction
        sent = []

        async def _capture(*args, **kwargs):
            sent.append((args, kwargs))

        interaction.followup.send = _capture
        await self.cog._reply(ctx, "after defer")
        self.assertEqual(len(sent), 1)
        _, kwargs = sent[0]
        self.assertTrue(kwargs.get("ephemeral"))


if __name__ == "__main__":
    unittest.main()
