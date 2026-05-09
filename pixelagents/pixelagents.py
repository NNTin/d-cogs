from __future__ import annotations

import asyncio
import logging
from typing import Dict, Optional, Tuple
from urllib.parse import urljoin, urlparse

import aiohttp
import discord
from redbot.core import Config, commands
from redbot.core.bot import Red

log = logging.getLogger("red.d_cogs.pixelagents")

_VISIBLE_STATUSES = {"online", "idle", "dnd"}

# (folderName, agentName, agentStatus) per guild/member
_Cache = Dict[Tuple[int, int], Tuple[str, str, str]]


def _agent_key(guild_id: int, user_id: int) -> str:
    return f"discord:{guild_id}:{user_id}"


def _normalize_base_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path if parsed.path.endswith("/") else parsed.path + "/"
    return parsed._replace(path=path).geturl()


class pixelagents(commands.Cog):
    """Mirror Discord guild presence into Pixelpipes via the Agent Control API."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0x706978656C61, force_registration=True)
        self.config.register_global(
            base_url="https://pp.lair.nntin.xyz/",
            api_key="",
            timeout_seconds=10,
        )
        self.config.register_guild(
            enabled=False,
            include_bots=True,
        )
        self._cache: _Cache = {}

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    async def _headers(self) -> dict:
        key = await self.config.api_key()
        return {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

    async def _base(self) -> str:
        return _normalize_base_url(await self.config.base_url())

    async def _spawn(self, session: aiohttp.ClientSession, base: str, headers: dict,
                     guild_id: int, user_id: int, name: str, folder: str,
                     agent_status: str) -> int:
        url = urljoin(base, "api/agents")
        payload = {
            "agentKey": _agent_key(guild_id, user_id),
            "agentName": name,
            "folderName": folder,
            "status": agent_status,
            "selected": False,
        }
        timeout = aiohttp.ClientTimeout(total=await self.config.timeout_seconds())
        async with session.post(url, json=payload, headers=headers, timeout=timeout) as resp:
            if resp.status not in (200, 201, 409):
                log.warning("spawn %s -> HTTP %s", _agent_key(guild_id, user_id), resp.status)
            return resp.status

    async def _patch(self, session: aiohttp.ClientSession, base: str, headers: dict,
                     guild_id: int, user_id: int, name: str,
                     agent_status: Optional[str] = None) -> int:
        key = _agent_key(guild_id, user_id)
        url = urljoin(base, f"api/agents/{key}/state")
        payload: dict = {"agentName": name}
        if agent_status is not None:
            payload["status"] = agent_status
        timeout = aiohttp.ClientTimeout(total=await self.config.timeout_seconds())
        async with session.patch(url, json=payload, headers=headers, timeout=timeout) as resp:
            if resp.status != 200:
                log.warning("patch %s -> HTTP %s", key, resp.status)
            return resp.status

    async def _despawn(self, session: aiohttp.ClientSession, base: str, headers: dict,
                       guild_id: int, user_id: int) -> int:
        key = _agent_key(guild_id, user_id)
        url = urljoin(base, f"api/agents/{key}")
        timeout = aiohttp.ClientTimeout(total=await self.config.timeout_seconds())
        async with session.delete(url, headers=headers, timeout=timeout) as resp:
            if resp.status not in (204, 404):
                log.warning("despawn %s -> HTTP %s", key, resp.status)
            return resp.status

    async def _tool_start(self, session: aiohttp.ClientSession, base: str, headers: dict,
                          guild_id: int, user_id: int, tool_id: str, tool_name: str,
                          status: str) -> int:
        key = _agent_key(guild_id, user_id)
        url = urljoin(base, f"api/agents/{key}/tool")
        payload = {"type": "agentToolStart", "toolId": tool_id,
                   "toolName": tool_name, "status": status}
        timeout = aiohttp.ClientTimeout(total=await self.config.timeout_seconds())
        async with session.post(url, json=payload, headers=headers, timeout=timeout) as resp:
            if resp.status != 200:
                log.warning("tool_start %s -> HTTP %s", key, resp.status)
            return resp.status

    async def _tool_clear(self, session: aiohttp.ClientSession, base: str, headers: dict,
                          guild_id: int, user_id: int) -> int:
        key = _agent_key(guild_id, user_id)
        url = urljoin(base, f"api/agents/{key}/tool")
        payload = {"type": "agentToolsClear"}
        timeout = aiohttp.ClientTimeout(total=await self.config.timeout_seconds())
        async with session.post(url, json=payload, headers=headers, timeout=timeout) as resp:
            if resp.status != 200:
                log.warning("tool_clear %s -> HTTP %s", key, resp.status)
            return resp.status

    async def _clear_tool_after_delay(self, guild_id: int, user_id: int, delay: float = 2.0) -> None:
        await asyncio.sleep(delay)
        base = await self._base()
        headers = await self._headers()
        async with aiohttp.ClientSession() as session:
            try:
                await self._tool_clear(session, base, headers, guild_id, user_id)
            except Exception as exc:
                log.error("tool_clear error for %s: %s", user_id, exc)

    async def _list_agents(self, session: aiohttp.ClientSession, base: str, headers: dict) -> Optional[list]:
        url = urljoin(base, "api/agents")
        timeout = aiohttp.ClientTimeout(total=await self.config.timeout_seconds())
        try:
            async with session.get(url, headers=headers, timeout=timeout) as resp:
                if resp.status == 200:
                    return await resp.json()
                log.warning("list agents -> HTTP %s", resp.status)
                return None
        except Exception as exc:
            log.error("list agents error: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Reconciliation
    # ------------------------------------------------------------------

    def _status_str(self, member: discord.Member) -> Optional[str]:
        s = str(member.status)
        return s if s in _VISIBLE_STATUSES else None

    def _is_included(self, member: discord.Member, include_bots: bool) -> bool:
        if member.bot and not include_bots:
            return False
        return True

    def _has_rich_presence(self, member: discord.Member) -> bool:
        return any(
            a.type != discord.ActivityType.custom
            for a in member.activities
        )

    def _agent_status(self, member: discord.Member) -> str:
        return "active" if self._has_rich_presence(member) else "waiting"

    async def _reconcile_member(self, session: aiohttp.ClientSession, base: str, headers: dict,
                                 member: discord.Member, include_bots: bool) -> None:
        guild_id = member.guild.id
        user_id = member.id
        folder = self._status_str(member)
        name = member.display_name
        cache_key = (guild_id, user_id)

        if folder is None or not self._is_included(member, include_bots):
            if cache_key in self._cache:
                await self._despawn(session, base, headers, guild_id, user_id)
                self._cache.pop(cache_key, None)
            return

        agent_status = self._agent_status(member)
        cached = self._cache.get(cache_key)
        if cached is None:
            # Not tracked — spawn fresh
            http_status = await self._spawn(session, base, headers, guild_id, user_id, name, folder, agent_status)
            if http_status in (200, 409):
                # Already exists; patch mutable fields
                await self._patch(session, base, headers, guild_id, user_id, name, agent_status)
            self._cache[cache_key] = (folder, name, agent_status)
            return

        cached_folder, cached_name, cached_status = cached
        folder_changed = folder != cached_folder
        name_changed = name != cached_name
        status_changed = agent_status != cached_status

        if folder_changed:
            # folderName is immutable via PATCH; delete then respawn
            await self._despawn(session, base, headers, guild_id, user_id)
            await self._spawn(session, base, headers, guild_id, user_id, name, folder, agent_status)
            self._cache[cache_key] = (folder, name, agent_status)
        elif name_changed or status_changed:
            await self._patch(
                session, base, headers, guild_id, user_id, name,
                agent_status if status_changed else None,
            )
            self._cache[cache_key] = (folder, name, agent_status)

    async def _full_sync(self, guild: discord.Guild) -> str:
        include_bots = await self.config.guild(guild).include_bots()
        base = await self._base()
        headers = await self._headers()
        if not headers["Authorization"].strip("Bearer ").strip():
            return "API key is not set."
        errors = 0
        async with aiohttp.ClientSession() as session:
            for member in guild.members:
                try:
                    await self._reconcile_member(session, base, headers, member, include_bots)
                except Exception as exc:
                    log.error("sync error for %s: %s", member.id, exc)
                    errors += 1
        return f"Sync complete. Errors: {errors}." if errors else "Sync complete."

    async def _despawn_guild(self, guild: discord.Guild) -> None:
        base = await self._base()
        headers = await self._headers()
        to_remove = [(gid, uid) for (gid, uid) in list(self._cache) if gid == guild.id]
        async with aiohttp.ClientSession() as session:
            for gid, uid in to_remove:
                try:
                    await self._despawn(session, base, headers, gid, uid)
                except Exception as exc:
                    log.error("despawn error for %s:%s: %s", gid, uid, exc)
                self._cache.pop((gid, uid), None)

    # ------------------------------------------------------------------
    # Cog lifecycle
    # ------------------------------------------------------------------

    async def cog_load(self) -> None:
        for guild in self.bot.guilds:
            if await self.config.guild(guild).enabled():
                try:
                    await self._full_sync(guild)
                except Exception as exc:
                    log.error("cog_load sync error for guild %s: %s", guild.id, exc)

    # ------------------------------------------------------------------
    # Listeners
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        # GUILD_MEMBER_UPDATE carries nick/roles/timeout — not status or activities.
        if not await self.config.guild(after.guild).enabled():
            return
        if before.display_name == after.display_name:
            return
        include_bots = await self.config.guild(after.guild).include_bots()
        base = await self._base()
        headers = await self._headers()
        async with aiohttp.ClientSession() as session:
            try:
                await self._reconcile_member(session, base, headers, after, include_bots)
            except Exception as exc:
                log.error("on_member_update error for %s: %s", after.id, exc)

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member) -> None:
        # PRESENCE_UPDATE carries status and activities changes.
        if not await self.config.guild(after.guild).enabled():
            return
        status_changed = before.status != after.status
        activity_changed = before.activities != after.activities
        if not status_changed and not activity_changed:
            return
        include_bots = await self.config.guild(after.guild).include_bots()
        base = await self._base()
        headers = await self._headers()
        async with aiohttp.ClientSession() as session:
            try:
                await self._reconcile_member(session, base, headers, after, include_bots)
            except Exception as exc:
                log.error("on_presence_update error for %s: %s", after.id, exc)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if not await self.config.guild(member.guild).enabled():
            return
        if self._status_str(member) is None:
            return
        include_bots = await self.config.guild(member.guild).include_bots()
        base = await self._base()
        headers = await self._headers()
        async with aiohttp.ClientSession() as session:
            try:
                await self._reconcile_member(session, base, headers, member, include_bots)
            except Exception as exc:
                log.error("on_member_join error for %s: %s", member.id, exc)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if not await self.config.guild(member.guild).enabled():
            return
        base = await self._base()
        headers = await self._headers()
        async with aiohttp.ClientSession() as session:
            try:
                await self._despawn(session, base, headers, member.guild.id, member.id)
            except Exception as exc:
                log.error("on_member_remove error for %s: %s", member.id, exc)
        self._cache.pop((member.guild.id, member.id), None)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        if not await self.config.guild(message.guild).enabled():
            return
        guild_id = message.guild.id
        user_id = message.author.id
        if (guild_id, user_id) not in self._cache:
            return
        content = message.content or ""
        if len(content) > 40:
            content = content[:40] + "…"
        base = await self._base()
        headers = await self._headers()
        async with aiohttp.ClientSession() as session:
            try:
                await self._tool_start(session, base, headers, guild_id, user_id,
                                       "incoming-message", "Message", content)
            except Exception as exc:
                log.error("on_message tool_start error for %s: %s", user_id, exc)
        asyncio.get_event_loop().create_task(
            self._clear_tool_after_delay(guild_id, user_id, delay=2.0)
        )

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @commands.group(name="pixelagents", invoke_without_command=True)
    @commands.admin_or_permissions(administrator=True)
    @commands.guild_only()
    async def pixelagents_group(self, ctx: commands.Context) -> None:
        """Manage Pixelagents presence mirroring."""
        await ctx.send_help()

    @pixelagents_group.command(name="status")
    async def cmd_status(self, ctx: commands.Context) -> None:
        """Show current Pixelagents configuration and guild status."""
        base_url = await self.config.base_url()
        api_key = await self.config.api_key()
        timeout = await self.config.timeout_seconds()
        enabled = await self.config.guild(ctx.guild).enabled()
        include_bots = await self.config.guild(ctx.guild).include_bots()
        tracked = sum(1 for (gid, _) in self._cache if gid == ctx.guild.id)

        embed = discord.Embed(title="Pixelagents Status", color=discord.Color.blurple())
        embed.add_field(name="Base URL", value=base_url, inline=False)
        embed.add_field(name="API Key", value="Set" if api_key else "Not set", inline=True)
        embed.add_field(name="Timeout", value=f"{timeout}s", inline=True)
        embed.add_field(name="Guild Enabled", value="Yes" if enabled else "No", inline=True)
        embed.add_field(name="Include Bots", value="Yes" if include_bots else "No", inline=True)
        embed.add_field(name="Tracked Agents", value=str(tracked), inline=True)

        try:
            base = await self._base()
            headers = await self._headers()
            async with aiohttp.ClientSession() as session:
                agents = await self._list_agents(session, base, headers)
            if agents is not None:
                embed.add_field(name="Remote Agent Count", value=str(len(agents)), inline=True)
            else:
                embed.add_field(name="Remote Agent Count", value="unavailable", inline=True)
        except Exception:
            embed.add_field(name="Remote Agent Count", value="error", inline=True)

        await ctx.send(embed=embed)

    @pixelagents_group.command(name="baseurl")
    async def cmd_baseurl(self, ctx: commands.Context, url: str) -> None:
        """Set the Node-RED base URL."""
        await self.config.base_url.set(url)
        await ctx.send(f"Base URL set to `{url}`.")

    @pixelagents_group.command(name="key")
    async def cmd_key(self, ctx: commands.Context, token: str) -> None:
        """Set the Agent Control API bearer token."""
        await self.config.api_key.set(token)
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass
        await ctx.send("API key set.")

    @pixelagents_group.command(name="enable")
    async def cmd_enable(self, ctx: commands.Context) -> None:
        """Enable Pixelpipes presence mirroring for this guild and run a full sync."""
        await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.send("Enabled. Running full sync…")
        result = await self._full_sync(ctx.guild)
        await ctx.send(result)

    @pixelagents_group.command(name="disable")
    async def cmd_disable(self, ctx: commands.Context) -> None:
        """Disable Pixelpipes presence mirroring for this guild and despawn all agents."""
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("Disabled. Despawning all tracked agents…")
        await self._despawn_guild(ctx.guild)
        await ctx.send("Done.")

    @pixelagents_group.command(name="includebots")
    async def cmd_includebots(self, ctx: commands.Context, value: bool) -> None:
        """Set whether bot users are mirrored (true/false)."""
        await self.config.guild(ctx.guild).include_bots.set(value)
        await ctx.send(f"include_bots set to `{value}`. Running sync…")
        if await self.config.guild(ctx.guild).enabled():
            result = await self._full_sync(ctx.guild)
            await ctx.send(result)

    @pixelagents_group.command(name="sync")
    async def cmd_sync(self, ctx: commands.Context) -> None:
        """Manually reconcile all guild members against their current Discord presence."""
        if not await self.config.guild(ctx.guild).enabled():
            await ctx.send("Guild is not enabled. Use `[p]pixelagents enable` first.")
            return
        await ctx.send("Syncing…")
        result = await self._full_sync(ctx.guild)
        await ctx.send(result)

    @pixelagents_group.command(name="despawnall")
    async def cmd_despawnall(self, ctx: commands.Context) -> None:
        """Despawn all tracked agents for this guild without disabling the cog."""
        await ctx.send("Despawning all tracked agents for this guild…")
        await self._despawn_guild(ctx.guild)
        await ctx.send("Done.")

    async def red_delete_data_for_user(self, *, requester, user_id: int) -> None:
        pass
