"""Unit tests for the pixelagents cog.

Stubs for discord / redbot / aiohttp are installed by conftest.py.
"""
from __future__ import annotations

import json
import unittest
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

def _activity(activity_type):
    a = MagicMock()
    a.type = activity_type
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
        "producer_url": "ws://standalone:3210/ws/producer",
        "message_tool_clear_delay": 2.0,
        "editor_role_id": None,
    }
    cog.config = cfg
    cog._agents = {}
    cog._logged_collisions = set()
    cog._ws = None
    cog._ws_session = None
    cog._connect_task = None
    cog._closing = False
    return cog


def _make_enabled_cog():
    cog = _make_cog()

    class _EnabledGuildConfig:
        def __getattr__(self, name):
            from pixelagents.tests.conftest import _FakeGuildConfigAttr
            data = {"enabled": True, "include_bots": True}
            return _FakeGuildConfigAttr(data, name)

    cog.config.guild = lambda guild: _EnabledGuildConfig()
    return cog


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
        self.ws = _FakeClientWebSocketResponse()
        self.cog._ws = self.ws

    async def test_send_serializes_json(self):
        await self.cog._send({"type": "agentClosed", "id": -42})
        self.assertEqual(len(self.ws._sent), 1)
        parsed = json.loads(self.ws._sent[0])
        self.assertEqual(parsed["type"], "agentClosed")

    async def test_send_noop_when_no_ws(self):
        self.cog._ws = None
        await self.cog._send({"type": "test"})

    async def test_send_noop_when_closed(self):
        self.ws.closed = True
        await self.cog._send({"type": "test"})
        self.assertEqual(len(self.ws._sent), 0)


# ---------------------------------------------------------------------------
# Tests: send_hello
# ---------------------------------------------------------------------------

class TestSendHello(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_cog()
        self.ws = _FakeClientWebSocketResponse()
        self.cog._ws = self.ws

    async def test_hello_includes_auth_check_capability(self):
        await self.cog._send_hello()
        msg = json.loads(self.ws._sent[0])
        self.assertEqual(msg["type"], "producerHello")
        self.assertIn("auth-check", msg["capabilities"])


# ---------------------------------------------------------------------------
# Tests: send_existing_agents
# ---------------------------------------------------------------------------

class TestSendExistingAgents(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_cog()
        self.ws = _FakeClientWebSocketResponse()
        self.cog._ws = self.ws

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
        self.ws = _FakeClientWebSocketResponse()
        self.cog._ws = self.ws

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
        self.ws = _FakeClientWebSocketResponse()
        self.cog._ws = self.ws

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


# ---------------------------------------------------------------------------
# Tests: handle_server_message
# ---------------------------------------------------------------------------

class TestHandleServerMessage(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_cog()
        self.ws = _FakeClientWebSocketResponse()
        self.cog._ws = self.ws

    async def test_bootstrap_request_sends_existing_agents(self):
        await self.cog._handle_server_message({"type": "producerBootstrapRequest"})
        msg = json.loads(self.ws._sent[0])
        self.assertEqual(msg["type"], "existingAgents")

    async def test_auth_check_request_replies(self):
        self.cog._check_auth = AsyncMock(return_value=True)
        await self.cog._handle_server_message({
            "type": "producerAuthCheckRequest",
            "requestId": "req-1",
            "discordUserId": "12345",
        })
        reply = json.loads(self.ws._sent[0])
        self.assertEqual(reply["type"], "producerAuthCheckReply")
        self.assertEqual(reply["requestId"], "req-1")
        self.assertTrue(reply["allowed"])

    async def test_auth_check_denied(self):
        self.cog._check_auth = AsyncMock(return_value=False)
        await self.cog._handle_server_message({
            "type": "producerAuthCheckRequest",
            "requestId": "req-2",
            "discordUserId": "99999",
        })
        reply = json.loads(self.ws._sent[0])
        self.assertFalse(reply["allowed"])

    async def test_auth_check_invalid_user_id_denied(self):
        self.cog._check_auth = AsyncMock(return_value=False)
        await self.cog._handle_server_message({
            "type": "producerAuthCheckRequest",
            "requestId": "req-3",
            "discordUserId": "not-a-number",
        })
        reply = json.loads(self.ws._sent[0])
        self.assertFalse(reply["allowed"])


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
        self.ws = _FakeClientWebSocketResponse()
        self.cog._ws = self.ws

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


class TestProducerUrlCommand(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = _make_cog()

    async def test_sets_url(self):
        ctx = MagicMock()
        ctx.interaction = None
        ctx.send = AsyncMock()
        await self.cog.cmd_producerurl(ctx, "ws://newhost:3210/ws/producer")
        self.assertEqual(
            await self.cog.config.producer_url(), "ws://newhost:3210/ws/producer"
        )


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
