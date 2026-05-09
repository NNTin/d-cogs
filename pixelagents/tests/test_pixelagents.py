"""Unit tests for the pixelagents cog.

Stubs for discord / redbot are installed by conftest.py before collection.
"""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock

# conftest.py has already patched sys.modules so this import resolves cleanly.
from pixelagents.pixelagents import (
    _agent_key,
    _normalize_base_url,
    _VISIBLE_STATUSES,
    pixelagents as PixelAgentsCog,
)
from pixelagents.tests.conftest import _FakeConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _member(guild_id=100, user_id=1, display_name="Tin", status="online", is_bot=False):
    m = MagicMock()
    m.guild.id = guild_id
    m.id = user_id
    m.display_name = display_name
    m.status = status
    m.bot = is_bot
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
# Tests: HTTP responses (mocked sessions)
# ---------------------------------------------------------------------------

class TestHttpSpawn(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_cog()
        self.base = "https://pp.lair.nntin.xyz/"
        self.headers = {"Authorization": "Bearer test", "Content-Type": "application/json"}

    async def test_spawn_201(self):
        session = _make_mock_session({"post": _make_mock_response(201)})
        self.assertEqual(await self.cog._spawn(session, self.base, self.headers, 100, 1, "Tin", "online"), 201)

    async def test_spawn_409_duplicate(self):
        session = _make_mock_session({"post": _make_mock_response(409)})
        self.assertEqual(await self.cog._spawn(session, self.base, self.headers, 100, 1, "Tin", "online"), 409)

    async def test_patch_200(self):
        session = _make_mock_session({"patch": _make_mock_response(200)})
        self.assertEqual(await self.cog._patch(session, self.base, self.headers, 100, 1, "NewName"), 200)

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
            status = await self.cog._spawn(session, self.base, self.headers, 100, 1, "Tin", "online")
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
        self.cog._cache[(100, 1)] = ("online", "Tin")
        session = await self._reconcile(_member(status="offline"))
        session.delete.assert_called_once()
        self.assertNotIn((100, 1), self.cog._cache)

    async def test_folder_change_uses_delete_then_spawn(self):
        self.cog._cache[(100, 1)] = ("online", "Tin")
        session = await self._reconcile(_member(status="dnd"))
        session.delete.assert_called_once()
        session.post.assert_called_once()

    async def test_name_change_only_patches(self):
        self.cog._cache[(100, 1)] = ("online", "Tin")
        session = await self._reconcile(_member(status="online", display_name="Newname"))
        session.delete.assert_not_called()
        session.post.assert_not_called()
        session.patch.assert_called_once()

    async def test_no_change_no_http(self):
        self.cog._cache[(100, 1)] = ("online", "Tin")
        session = await self._reconcile(_member(status="online", display_name="Tin"))
        session.post.assert_not_called()
        session.patch.assert_not_called()
        session.delete.assert_not_called()

    async def test_bot_excluded_when_include_bots_false(self):
        session = await self._reconcile(_member(status="online", is_bot=True), include_bots=False)
        session.post.assert_not_called()

    async def test_bot_excluded_despawns_if_cached(self):
        self.cog._cache[(100, 99)] = ("online", "BotName")
        session = await self._reconcile(_member(user_id=99, status="online", is_bot=True), include_bots=False)
        session.delete.assert_called_once()
        self.assertNotIn((100, 99), self.cog._cache)


if __name__ == "__main__":
    unittest.main()
