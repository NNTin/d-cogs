"""Unit tests for the pixelagents cog.

Stubs for discord / redbot are installed by conftest.py before collection.
"""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, call

# conftest.py has already patched sys.modules so this import resolves cleanly.
from pixelagents.pixelagents import (
    _agent_key,
    _normalize_base_url,
    _VISIBLE_STATUSES,
    PixelAgentsKeyModal,
    pixelagents as PixelAgentsCog,
)
from pixelagents.tests.conftest import _FakeConfig, _FakeInteraction, _FakeInteractionResponse

import discord  # stubbed by conftest


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
    cog = PixelAgentsCog.__new__(PixelAgentsCog)
    cog.bot = bot
    cfg = _FakeConfig()
    cfg._global = {
        "base_url": "https://pp.lair.nntin.xyz/",
        "api_key": "test-token",
        "timeout_seconds": 10,
        "message_tool_clear_delay": 2.0,
    }
    cog.config = cfg
    cog._cache = {}
    return cog


def _make_mock_response(status: int, json_data=None):
    resp = AsyncMock()
    resp.status = status
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    if json_data is not None:
        resp.json = AsyncMock(return_value=json_data)
    return resp


def _make_mock_session(responses: dict):
    session = MagicMock()
    for method, resp in responses.items():
        setattr(session, method, MagicMock(return_value=resp))
    return session


# ---------------------------------------------------------------------------
# Tests: pure helpers
# ---------------------------------------------------------------------------

class TestAgentKey(unittest.TestCase):
    def test_format(self):
        self.assertEqual(_agent_key(123, 456), "discord:123:456")

    def test_different_guilds_no_collision(self):
        self.assertNotEqual(_agent_key(1, 2), _agent_key(2, 1))


class TestNormalizeBaseUrl(unittest.TestCase):
    def test_adds_trailing_slash(self):
        self.assertTrue(_normalize_base_url("https://example.com").endswith("/"))

    def test_preserves_trailing_slash(self):
        url = "https://example.com/"
        self.assertEqual(_normalize_base_url(url), url)

    def test_path_preserved(self):
        self.assertIn("example.com", _normalize_base_url("https://example.com/path"))


class TestVisibleStatuses(unittest.TestCase):
    def test_visible(self):
        for s in ("online", "idle", "dnd"):
            self.assertIn(s, _VISIBLE_STATUSES)

    def test_not_visible(self):
        for s in ("offline", "invisible"):
            self.assertNotIn(s, _VISIBLE_STATUSES)


# ---------------------------------------------------------------------------
# Tests: status mapping / bot inclusion
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


# ---------------------------------------------------------------------------
# Tests: rich presence detection
# ---------------------------------------------------------------------------

class TestRichPresence(unittest.TestCase):
    def setUp(self):
        self.cog = _make_cog()

    def test_no_activities_is_waiting(self):
        m = _member(activities=[])
        self.assertEqual(self.cog._agent_status(m), "waiting")

    def test_game_activity_is_active(self):
        m = _member(activities=[_activity(discord.ActivityType.playing)])
        self.assertEqual(self.cog._agent_status(m), "active")

    def test_streaming_is_active(self):
        m = _member(activities=[_activity(discord.ActivityType.streaming)])
        self.assertEqual(self.cog._agent_status(m), "active")

    def test_listening_is_active(self):
        m = _member(activities=[_activity(discord.ActivityType.listening)])
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
# Tests: HTTP responses (mocked sessions)
# ---------------------------------------------------------------------------

class TestHttpSpawn(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_cog()
        self.base = "https://pp.lair.nntin.xyz/"
        self.headers = {"Authorization": "Bearer test", "Content-Type": "application/json"}

    async def test_spawn_201(self):
        session = _make_mock_session({"post": _make_mock_response(201)})
        self.assertEqual(
            await self.cog._spawn(session, self.base, self.headers, 100, 1, "Tin", "online", "waiting"), 201
        )

    async def test_spawn_sends_status_field(self):
        session = _make_mock_session({"post": _make_mock_response(201)})
        await self.cog._spawn(session, self.base, self.headers, 100, 1, "Tin", "online", "active")
        _, kwargs = session.post.call_args
        self.assertEqual(kwargs["json"]["status"], "active")

    async def test_spawn_409_duplicate(self):
        session = _make_mock_session({"post": _make_mock_response(409)})
        self.assertEqual(
            await self.cog._spawn(session, self.base, self.headers, 100, 1, "Tin", "online", "waiting"), 409
        )

    async def test_patch_200(self):
        session = _make_mock_session({"patch": _make_mock_response(200)})
        self.assertEqual(await self.cog._patch(session, self.base, self.headers, 100, 1, "NewName"), 200)

    async def test_patch_with_status(self):
        session = _make_mock_session({"patch": _make_mock_response(200)})
        await self.cog._patch(session, self.base, self.headers, 100, 1, "Tin", "active")
        _, kwargs = session.patch.call_args
        self.assertEqual(kwargs["json"]["status"], "active")
        self.assertEqual(kwargs["json"]["agentName"], "Tin")

    async def test_patch_without_status_omits_field(self):
        session = _make_mock_session({"patch": _make_mock_response(200)})
        await self.cog._patch(session, self.base, self.headers, 100, 1, "Tin")
        _, kwargs = session.patch.call_args
        self.assertNotIn("status", kwargs["json"])

    async def test_despawn_204(self):
        session = _make_mock_session({"delete": _make_mock_response(204)})
        self.assertEqual(await self.cog._despawn(session, self.base, self.headers, 100, 1), 204)

    async def test_despawn_404_treated_as_success(self):
        session = _make_mock_session({"delete": _make_mock_response(404)})
        self.assertEqual(await self.cog._despawn(session, self.base, self.headers, 100, 1), 404)

    async def test_list_200(self):
        session = _make_mock_session({"get": _make_mock_response(200, json_data=[{"agentKey": "discord:100:1"}])})
        result = await self.cog._list_agents(session, self.base, self.headers)
        self.assertEqual(len(result), 1)

    async def test_spawn_401_logged(self):
        session = _make_mock_session({"post": _make_mock_response(401)})
        with self.assertLogs("red.d_cogs.pixelagents", level="WARNING"):
            status = await self.cog._spawn(session, self.base, self.headers, 100, 1, "Tin", "online", "waiting")
        self.assertEqual(status, 401)


# ---------------------------------------------------------------------------
# Tests: reconcile logic
# ---------------------------------------------------------------------------

class TestReconcile(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_cog()
        self.base = "https://pp.lair.nntin.xyz/"
        self.headers = {"Authorization": "Bearer test", "Content-Type": "application/json"}

    async def _reconcile(self, member, include_bots=True, spawn_status=201,
                          patch_status=200, despawn_status=204):
        session = _make_mock_session({
            "post": _make_mock_response(spawn_status),
            "patch": _make_mock_response(patch_status),
            "delete": _make_mock_response(despawn_status),
        })
        await self.cog._reconcile_member(session, self.base, self.headers, member, include_bots)
        return session

    async def test_new_visible_member_spawns(self):
        session = await self._reconcile(_member(status="online"))
        session.post.assert_called_once()

    async def test_spawn_includes_agent_status(self):
        m = _member(status="online", activities=[_activity(discord.ActivityType.playing)])
        session = await self._reconcile(m)
        _, kwargs = session.post.call_args
        self.assertEqual(kwargs["json"]["status"], "active")

    async def test_spawn_waiting_when_no_rich_presence(self):
        m = _member(status="online", activities=[])
        session = await self._reconcile(m)
        _, kwargs = session.post.call_args
        self.assertEqual(kwargs["json"]["status"], "waiting")

    async def test_409_fallback_to_patch(self):
        session = await self._reconcile(_member(status="online"), spawn_status=409)
        session.post.assert_called_once()
        session.patch.assert_called_once()

    async def test_200_spawn_fallback_to_patch(self):
        session = await self._reconcile(_member(status="online"), spawn_status=200)
        session.post.assert_called_once()
        session.patch.assert_called_once()

    async def test_offline_member_not_spawned(self):
        session = await self._reconcile(_member(status="offline"))
        session.post.assert_not_called()

    async def test_offline_member_in_cache_is_despawned(self):
        self.cog._cache[(100, 1)] = ("online", "Tin", "waiting")
        session = await self._reconcile(_member(status="offline"))
        session.delete.assert_called_once()
        self.assertNotIn((100, 1), self.cog._cache)

    async def test_folder_change_uses_delete_then_spawn(self):
        self.cog._cache[(100, 1)] = ("online", "Tin", "waiting")
        session = await self._reconcile(_member(status="dnd"))
        session.delete.assert_called_once()
        session.post.assert_called_once()

    async def test_folder_and_status_change_respawns_with_new_status(self):
        self.cog._cache[(100, 1)] = ("online", "Tin", "waiting")
        m = _member(status="dnd", activities=[_activity(discord.ActivityType.playing)])
        session = await self._reconcile(m)
        session.delete.assert_called_once()
        _, kwargs = session.post.call_args
        self.assertEqual(kwargs["json"]["status"], "active")
        self.assertEqual(kwargs["json"]["folderName"], "dnd")

    async def test_name_change_only_patches(self):
        self.cog._cache[(100, 1)] = ("online", "Tin", "waiting")
        session = await self._reconcile(_member(status="online", display_name="Newname"))
        session.delete.assert_not_called()
        session.post.assert_not_called()
        session.patch.assert_called_once()

    async def test_agent_status_change_only_patches(self):
        self.cog._cache[(100, 1)] = ("online", "Tin", "waiting")
        m = _member(status="online", activities=[_activity(discord.ActivityType.playing)])
        session = await self._reconcile(m)
        session.delete.assert_not_called()
        session.post.assert_not_called()
        session.patch.assert_called_once()
        _, kwargs = session.patch.call_args
        self.assertEqual(kwargs["json"]["status"], "active")

    async def test_name_change_no_status_change_omits_status_from_patch(self):
        self.cog._cache[(100, 1)] = ("online", "Tin", "waiting")
        session = await self._reconcile(_member(status="online", display_name="Newname", activities=[]))
        _, kwargs = session.patch.call_args
        self.assertNotIn("status", kwargs["json"])

    async def test_no_change_no_http(self):
        self.cog._cache[(100, 1)] = ("online", "Tin", "waiting")
        session = await self._reconcile(_member(status="online", display_name="Tin", activities=[]))
        session.post.assert_not_called()
        session.patch.assert_not_called()
        session.delete.assert_not_called()

    async def test_bot_excluded_when_include_bots_false(self):
        session = await self._reconcile(_member(status="online", is_bot=True), include_bots=False)
        session.post.assert_not_called()

    async def test_bot_excluded_despawns_if_cached(self):
        self.cog._cache[(100, 99)] = ("online", "BotName", "waiting")
        session = await self._reconcile(_member(user_id=99, status="online", is_bot=True), include_bots=False)
        session.delete.assert_called_once()
        self.assertNotIn((100, 99), self.cog._cache)


# ---------------------------------------------------------------------------
# Tests: listener routing
# ---------------------------------------------------------------------------

def _make_enabled_cog():
    """Cog with a guild already marked enabled in its config."""
    cog = _make_cog()
    # Patch guild config to return enabled=True
    cog.config._guilds_enabled = True

    class _EnabledGuildConfig:
        def __getattr__(self, name):
            from pixelagents.tests.conftest import _FakeGuildConfigAttr
            data = {"enabled": True, "include_bots": True}
            return _FakeGuildConfigAttr(data, name)

    original_guild = cog.config.guild

    def _guild(guild):
        return _EnabledGuildConfig()

    cog.config.guild = _guild
    return cog


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
        cog = _make_cog()  # guild enabled=False by default
        cog._reconcile_member = AsyncMock()
        before = _member(status="online")
        after = _member(status="dnd")
        await cog.on_presence_update(before, after)
        cog._reconcile_member.assert_not_awaited()


# ---------------------------------------------------------------------------
# Tests: _tool_start / _tool_clear
# ---------------------------------------------------------------------------

class TestToolHelpers(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = _make_cog()
        self.cog.config.guild = MagicMock(return_value=MagicMock(
            enabled=AsyncMock(return_value=True),
            include_bots=AsyncMock(return_value=True),
        ))

    async def test_tool_start_200(self):
        resp = _make_mock_response(200)
        session = _make_mock_session({"post": resp})
        status = await self.cog._tool_start(
            session, "https://pp.lair.nntin.xyz/",
            {"Authorization": "Bearer test-token", "Content-Type": "application/json"},
            100, 1, "incoming-message", "Message", "discord message",
        )
        self.assertEqual(status, 200)
        session.post.assert_called_once()
        call_kwargs = session.post.call_args
        self.assertIn("api/agents/discord:100:1/tool", call_kwargs[0][0])

    async def test_tool_clear_200(self):
        resp = _make_mock_response(200)
        session = _make_mock_session({"post": resp})
        status = await self.cog._tool_clear(
            session, "https://pp.lair.nntin.xyz/",
            {"Authorization": "Bearer test-token", "Content-Type": "application/json"},
            100, 1,
        )
        self.assertEqual(status, 200)
        session.post.assert_called_once()
        payload = session.post.call_args[1]["json"]
        self.assertEqual(payload["type"], "agentToolsClear")


# ---------------------------------------------------------------------------
# Tests: toolcleardelay command
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

    async def test_set_zero_delay_allowed(self):
        ctx = MagicMock()
        ctx.interaction = None
        ctx.send = AsyncMock()
        await self.cog.cmd_toolcleardelay(ctx, 0.0)
        self.assertEqual(await self.cog.config.message_tool_clear_delay(), 0.0)

    async def test_negative_delay_rejected(self):
        ctx = MagicMock()
        ctx.interaction = None
        ctx.send = AsyncMock()
        await self.cog.cmd_toolcleardelay(ctx, -1.0)
        # config should remain unchanged
        self.assertEqual(await self.cog.config.message_tool_clear_delay(), 2.0)
        ctx.send.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tests: on_message
# ---------------------------------------------------------------------------

class TestOnMessage(unittest.IsolatedAsyncioTestCase):
    def _make_message(self, guild_id=100, user_id=1, has_guild=True):
        msg = MagicMock()
        if has_guild:
            msg.guild = MagicMock()
            msg.guild.id = guild_id
        else:
            msg.guild = None
        msg.author = MagicMock()
        msg.author.id = user_id
        return msg

    async def test_on_message_untracked_skips(self):
        cog = _make_cog()
        cog.config.guild = MagicMock(return_value=MagicMock(
            enabled=AsyncMock(return_value=True),
        ))
        cog._tool_start = AsyncMock()
        msg = self._make_message(guild_id=100, user_id=99)
        # cache is empty — user 99 not tracked
        await cog.on_message(msg)
        cog._tool_start.assert_not_awaited()

    async def test_on_message_no_guild_skips(self):
        cog = _make_cog()
        cog._tool_start = AsyncMock()
        msg = self._make_message(has_guild=False)
        await cog.on_message(msg)
        cog._tool_start.assert_not_awaited()

    async def test_on_message_disabled_guild_skips(self):
        cog = _make_cog()
        cog.config.guild = MagicMock(return_value=MagicMock(
            enabled=AsyncMock(return_value=False),
        ))
        cog._tool_start = AsyncMock()
        cog._cache[(100, 1)] = ("online", "Tin", "waiting")
        msg = self._make_message(guild_id=100, user_id=1)
        await cog.on_message(msg)
        cog._tool_start.assert_not_awaited()

    def _make_message_with_content(self, content, guild_id=100, user_id=1):
        msg = self._make_message(guild_id=guild_id, user_id=user_id)
        msg.content = content
        return msg

    async def _run_on_message(self, cog, msg):
        import asyncio as _asyncio
        tasks = []
        loop = _asyncio.get_event_loop()
        orig_create_task = loop.create_task

        def _capture_task(coro, **kw):
            t = orig_create_task(coro, **kw)
            tasks.append(t)
            return t

        loop.create_task = _capture_task
        try:
            await cog.on_message(msg)
        finally:
            loop.create_task = orig_create_task
            for t in tasks:
                t.cancel()
                try:
                    await t
                except (_asyncio.CancelledError, Exception):
                    pass

    async def test_on_message_tracked_sends_tool_start(self):
        cog = _make_cog()
        cog.config.guild = MagicMock(return_value=MagicMock(
            enabled=AsyncMock(return_value=True),
        ))
        cog._cache[(100, 1)] = ("online", "Tin", "waiting")
        cog._tool_start = AsyncMock(return_value=200)

        msg = self._make_message_with_content("hello world", guild_id=100, user_id=1)
        await self._run_on_message(cog, msg)

        cog._tool_start.assert_awaited_once()
        call_args = cog._tool_start.call_args
        # positional: session, base, headers, guild_id, user_id, tool_id, tool_name, status
        self.assertEqual(call_args[0][3], 100)
        self.assertEqual(call_args[0][4], 1)
        self.assertEqual(call_args[0][5], "incoming-message")
        self.assertEqual(call_args[0][6], "Message")
        self.assertEqual(call_args[0][7], "hello world")

    async def test_on_message_uses_configured_delay(self):
        cog = _make_cog()
        cog.config._global["message_tool_clear_delay"] = 7.5
        cog.config.guild = MagicMock(return_value=MagicMock(
            enabled=AsyncMock(return_value=True),
        ))
        cog._cache[(100, 1)] = ("online", "Tin", "waiting")
        cog._tool_start = AsyncMock(return_value=200)

        captured_delays = []
        orig_clear = cog._clear_tool_after_delay

        async def _fake_clear(guild_id, user_id, delay=2.0):
            captured_delays.append(delay)

        cog._clear_tool_after_delay = _fake_clear

        import asyncio as _asyncio
        tasks = []
        loop = _asyncio.get_event_loop()
        orig_create_task = loop.create_task

        def _capture(coro, **kw):
            t = orig_create_task(coro, **kw)
            tasks.append(t)
            return t

        loop.create_task = _capture
        try:
            msg = self._make_message_with_content("hi", guild_id=100, user_id=1)
            await cog.on_message(msg)
            # drain the task
            for t in tasks:
                await t
        finally:
            loop.create_task = orig_create_task

        self.assertEqual(captured_delays, [7.5])

    async def test_on_message_truncates_long_content(self):
        cog = _make_cog()
        cog.config.guild = MagicMock(return_value=MagicMock(
            enabled=AsyncMock(return_value=True),
        ))
        cog._cache[(100, 1)] = ("online", "Tin", "waiting")
        cog._tool_start = AsyncMock(return_value=200)

        long_msg = "a" * 80
        msg = self._make_message_with_content(long_msg, guild_id=100, user_id=1)
        await self._run_on_message(cog, msg)

        status_sent = cog._tool_start.call_args[0][7]
        self.assertEqual(status_sent, "a" * 40 + "…")

    async def test_on_message_exact_40_chars_not_truncated(self):
        cog = _make_cog()
        cog.config.guild = MagicMock(return_value=MagicMock(
            enabled=AsyncMock(return_value=True),
        ))
        cog._cache[(100, 1)] = ("online", "Tin", "waiting")
        cog._tool_start = AsyncMock(return_value=200)

        exact_msg = "b" * 40
        msg = self._make_message_with_content(exact_msg, guild_id=100, user_id=1)
        await self._run_on_message(cog, msg)

        status_sent = cog._tool_start.call_args[0][7]
        self.assertEqual(status_sent, exact_msg)


# ---------------------------------------------------------------------------
# Tests: hybrid_group loading
# ---------------------------------------------------------------------------

class TestHybridGroupLoads(unittest.TestCase):
    def test_cog_class_exists(self):
        """PixelAgentsCog class is importable after conversion to hybrid_group."""
        self.assertTrue(callable(PixelAgentsCog))

    def test_modal_class_exists(self):
        """PixelAgentsKeyModal is importable."""
        self.assertTrue(callable(PixelAgentsKeyModal))

    def test_cog_instantiates(self):
        """Cog instantiates without error with hybrid_group in place."""
        cog = _make_cog()
        self.assertIsNotNone(cog)

    def test_command_methods_present(self):
        """All expected command handler methods are present on the cog."""
        cog = _make_cog()
        for name in ("cmd_status", "cmd_baseurl", "cmd_key", "cmd_toolcleardelay",
                     "cmd_enable", "cmd_disable", "cmd_includebots", "cmd_sync", "cmd_despawnall"):
            self.assertTrue(hasattr(cog, name), f"Missing method: {name}")


# ---------------------------------------------------------------------------
# Tests: PixelAgentsKeyModal
# ---------------------------------------------------------------------------

def _make_guild_member(is_admin=True):
    member = MagicMock()
    member.guild_permissions = MagicMock()
    member.guild_permissions.administrator = is_admin
    return member


def _make_interaction(has_guild=True, is_admin=True):
    guild = MagicMock() if has_guild else None
    user = _make_guild_member(is_admin=is_admin)
    return _FakeInteraction(guild=guild, user=user)


class TestPixelAgentsKeyModal(unittest.IsolatedAsyncioTestCase):
    def _make_modal(self, token_value="secret-token"):
        cog = _make_cog()
        modal = PixelAgentsKeyModal(cog)
        modal.token = MagicMock()
        modal.token.value = token_value
        return modal, cog

    async def test_submit_saves_api_key(self):
        modal, cog = self._make_modal("my-secret-key")
        interaction = _make_interaction(has_guild=True, is_admin=True)
        await modal.on_submit(interaction)
        saved = await cog.config.api_key()
        self.assertEqual(saved, "my-secret-key")

    async def test_submit_replies_ephemerally(self):
        modal, cog = self._make_modal("my-secret-key")
        interaction = _make_interaction(has_guild=True, is_admin=True)
        sent_messages = []
        orig_send = interaction.response.send_message

        async def _capture(*args, **kwargs):
            sent_messages.append((args, kwargs))
            await orig_send(*args, **kwargs)

        interaction.response.send_message = _capture
        await modal.on_submit(interaction)
        self.assertEqual(len(sent_messages), 1)
        _, kwargs = sent_messages[0]
        self.assertTrue(kwargs.get("ephemeral"), "Response must be ephemeral")

    async def test_submit_replies_api_key_set(self):
        modal, cog = self._make_modal("my-secret-key")
        interaction = _make_interaction(has_guild=True, is_admin=True)
        sent = []

        async def _capture(*args, **kwargs):
            sent.append(args[0] if args else kwargs.get("content", ""))

        interaction.response.send_message = _capture
        await modal.on_submit(interaction)
        self.assertIn("API key set", sent[0])

    async def test_submit_rejects_missing_guild(self):
        modal, cog = self._make_modal("token")
        interaction = _make_interaction(has_guild=False, is_admin=True)
        await modal.on_submit(interaction)
        # API key must NOT be saved
        saved = await cog.config.api_key()
        self.assertNotEqual(saved, "token")

    async def test_submit_rejects_missing_guild_sends_error(self):
        modal, cog = self._make_modal("token")
        interaction = _make_interaction(has_guild=False)
        sent = []

        async def _capture(*args, **kwargs):
            sent.append((args, kwargs))

        interaction.response.send_message = _capture
        await modal.on_submit(interaction)
        self.assertEqual(len(sent), 1)
        _, kwargs = sent[0]
        self.assertTrue(kwargs.get("ephemeral"))

    async def test_submit_rejects_non_admin(self):
        modal, cog = self._make_modal("secret")
        interaction = _make_interaction(has_guild=True, is_admin=False)
        await modal.on_submit(interaction)
        saved = await cog.config.api_key()
        self.assertNotEqual(saved, "secret")

    async def test_submit_non_admin_sends_error(self):
        modal, cog = self._make_modal("secret")
        interaction = _make_interaction(has_guild=True, is_admin=False)
        sent = []

        async def _capture(*args, **kwargs):
            sent.append((args, kwargs))

        interaction.response.send_message = _capture
        await modal.on_submit(interaction)
        self.assertEqual(len(sent), 1)
        _, kwargs = sent[0]
        self.assertTrue(kwargs.get("ephemeral"))

    async def test_token_not_echoed_in_reply(self):
        token = "super-secret-bearer-token-xyz"
        modal, cog = self._make_modal(token)
        interaction = _make_interaction(has_guild=True, is_admin=True)
        sent = []

        async def _capture(*args, **kwargs):
            sent.append(args[0] if args else "")

        interaction.response.send_message = _capture
        await modal.on_submit(interaction)
        for msg in sent:
            self.assertNotIn(token, str(msg), "Token must not appear in any response")

    async def test_on_error_does_not_raise(self):
        modal, _ = self._make_modal()
        interaction = _make_interaction()
        # on_error must not raise even when response.send_message fails
        async def _fail(*args, **kwargs):
            raise RuntimeError("discord error")
        interaction.response.send_message = _fail
        try:
            await modal.on_error(interaction, ValueError("test error"))
        except Exception as exc:
            self.fail(f"on_error raised unexpectedly: {exc}")


# ---------------------------------------------------------------------------
# Tests: cmd_key slash vs prefix routing
# ---------------------------------------------------------------------------

class TestCmdKeyRouting(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = _make_cog()

    async def test_slash_opens_modal(self):
        ctx = MagicMock()
        ctx.interaction = _FakeInteraction(guild=MagicMock(), user=_make_guild_member())
        opened_modals = []

        async def _capture_modal(modal):
            opened_modals.append(modal)
            ctx.interaction.response._done = True

        ctx.interaction.response.send_modal = _capture_modal
        await self.cog.cmd_key(ctx)
        self.assertEqual(len(opened_modals), 1)
        self.assertIsInstance(opened_modals[0], PixelAgentsKeyModal)

    async def test_prefix_without_token_shows_usage(self):
        ctx = MagicMock()
        ctx.interaction = None
        ctx.send = AsyncMock()
        await self.cog.cmd_key(ctx, token=None)
        ctx.send.assert_awaited_once()
        msg = ctx.send.call_args[0][0]
        self.assertIn("pixelagents key", msg)

    async def test_prefix_with_token_saves_key(self):
        ctx = MagicMock()
        ctx.interaction = None
        ctx.send = AsyncMock()
        ctx.message = MagicMock()
        ctx.message.delete = AsyncMock()
        await self.cog.cmd_key(ctx, token="mytoken")
        saved = await self.cog.config.api_key()
        self.assertEqual(saved, "mytoken")
        ctx.send.assert_awaited_once_with("API key set.")

    async def test_prefix_token_not_echoed(self):
        ctx = MagicMock()
        ctx.interaction = None
        ctx.send = AsyncMock()
        ctx.message = MagicMock()
        ctx.message.delete = AsyncMock()
        await self.cog.cmd_key(ctx, token="top-secret-token")
        for call_args in ctx.send.call_args_list:
            args, kwargs = call_args
            for a in args:
                self.assertNotIn("top-secret-token", str(a))


# ---------------------------------------------------------------------------
# Tests: ephemeral reply helper
# ---------------------------------------------------------------------------

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
        interaction.response._done = True  # simulate deferred
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
