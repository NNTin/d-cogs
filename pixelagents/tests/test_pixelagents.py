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
    pixelagents as PixelAgentsCog,
)
from pixelagents.tests.conftest import _FakeConfig

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


if __name__ == "__main__":
    unittest.main()
