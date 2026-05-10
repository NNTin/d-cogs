from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Dict, Optional, Set, Tuple

import aiohttp
import discord
from discord import app_commands
from redbot.core import Config, commands
from redbot.core.bot import Red

log = logging.getLogger("red.d_cogs.pixelagents")

_VISIBLE_STATUSES = {"online", "idle", "dnd"}

# JavaScript Number.MAX_SAFE_INTEGER = 2^53 - 1 = 9007199254740991
_JS_MAX_SAFE = (1 << 53) - 1


def _discord_id_to_agent_id(user_id: int) -> int:
    """Map a Discord user ID to a stable negative JavaScript-safe integer.

    Discord snowflakes are up to 64 bits. We take user_id modulo JS_MAX_SAFE
    and negate. If the result is 0 (user_id is a multiple of JS_MAX_SAFE),
    we use -JS_MAX_SAFE to guarantee negativity.
    """
    mapped = user_id % _JS_MAX_SAFE
    return -(mapped if mapped != 0 else _JS_MAX_SAFE)


class pixelagents(commands.Cog):
    """Mirror Discord guild presence into Pixelpipes via the producer WebSocket."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0x706978656C61, force_registration=True)
        self.config.register_global(
            producer_url="ws://standalone:3210/ws/producer",
            message_tool_clear_delay=2.0,
            editor_role_id=None,
        )
        self.config.register_guild(
            enabled=False,
            include_bots=True,
        )
        # Active agents: (guild_id, user_id) -> (folder_name, display_name)
        self._agents: Dict[Tuple[int, int], Tuple[str, str]] = {}
        # Known collisions (agent_id) already logged
        self._logged_collisions: Set[int] = set()
        # WebSocket state
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._ws_session: Optional[aiohttp.ClientSession] = None
        self._connect_task: Optional[asyncio.Task] = None
        self._closing = False

    # ------------------------------------------------------------------
    # ID helpers
    # ------------------------------------------------------------------

    def _agent_id(self, user_id: int) -> int:
        return _discord_id_to_agent_id(user_id)

    def _detect_collision(self, user_id: int) -> None:
        agent_id = self._agent_id(user_id)
        for (_, uid) in self._agents:
            if uid != user_id and self._agent_id(uid) == agent_id:
                if agent_id not in self._logged_collisions:
                    self._logged_collisions.add(agent_id)
                    log.warning(
                        "pixelagents: agent ID collision — user %d and user %d both map to %d",
                        user_id, uid, agent_id,
                    )
                break

    # ------------------------------------------------------------------
    # WebSocket send helper
    # ------------------------------------------------------------------

    async def _send(self, message: dict) -> None:
        if self._ws is not None and not self._ws.closed:
            try:
                await self._ws.send_str(json.dumps(message))
            except Exception as exc:
                log.error("pixelagents: send error: %s", exc)

    # ------------------------------------------------------------------
    # Producer protocol messages
    # ------------------------------------------------------------------

    async def _send_hello(self) -> None:
        await self._send({"type": "producerHello", "capabilities": ["auth-check"]})

    async def _send_existing_agents(self) -> None:
        seen: Set[int] = set()
        agent_ids = []
        folder_names: Dict[int, str] = {}
        for (_, uid), (folder, _) in sorted(self._agents.items()):
            if uid in seen:
                continue
            seen.add(uid)
            aid = self._agent_id(uid)
            agent_ids.append(aid)
            folder_names[aid] = folder
        await self._send({
            "type": "existingAgents",
            "agents": agent_ids,
            "agentMeta": {},
            "folderNames": folder_names,
            "externalAgents": {},
        })

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def cog_load(self) -> None:
        self._closing = False
        self._connect_task = asyncio.get_event_loop().create_task(self._connect_loop())

    async def cog_unload(self) -> None:
        self._closing = True
        if self._connect_task is not None:
            self._connect_task.cancel()
            try:
                await self._connect_task
            except asyncio.CancelledError:
                pass
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        if self._ws_session is not None:
            await self._ws_session.close()

    async def _connect_loop(self) -> None:
        delay = 1.0
        while not self._closing:
            try:
                url = await self.config.producer_url()
                self._ws_session = aiohttp.ClientSession()
                self._ws = await self._ws_session.ws_connect(url)
                log.info("pixelagents: connected to %s", url)
                delay = 1.0

                await self._send_hello()
                await self._sync_all_guilds()

                async for msg in self._ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        try:
                            await self._handle_server_message(json.loads(msg.data))
                        except Exception as exc:
                            log.error("pixelagents: message handler error: %s", exc)
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.warning("pixelagents: connection error: %s", exc)
            finally:
                if self._ws is not None and not self._ws.closed:
                    await self._ws.close()
                self._ws = None
                if self._ws_session is not None:
                    await self._ws_session.close()
                self._ws_session = None

            if not self._closing:
                log.info("pixelagents: reconnecting in %.1fs", delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60.0)

    async def _handle_server_message(self, data: dict) -> None:
        msg_type = data.get("type")
        if msg_type == "producerBootstrapRequest":
            await self._send_existing_agents()
            # Resend display names so the server has current data after a reconnect or restart.
            # _send_existing_agents only sends IDs and folderNames; agentTeamInfo carries the name.
            seen: Set[int] = set()
            for (_, uid), (_, name) in sorted(self._agents.items()):
                if uid in seen:
                    continue
                seen.add(uid)
                await self._send({"type": "agentTeamInfo", "id": self._agent_id(uid), "agentName": name})
        elif msg_type == "producerAuthCheckRequest":
            request_id = data.get("requestId", "")
            user_id_str = data.get("discordUserId", "")
            try:
                user_id = int(user_id_str)
            except (ValueError, TypeError):
                user_id = 0
            allowed = await self._check_auth(user_id)
            await self._send({
                "type": "producerAuthCheckReply",
                "requestId": request_id,
                "allowed": allowed,
            })

    # ------------------------------------------------------------------
    # Editor authorization
    # ------------------------------------------------------------------

    async def _check_auth(self, user_id: int) -> bool:
        if user_id == 0:
            return False
        if await self.bot.is_owner(discord.Object(id=user_id)):
            return True
        role_id = await self.config.editor_role_id()
        if role_id is None:
            return False
        for guild in self.bot.guilds:
            if not await self.config.guild(guild).enabled():
                continue
            member = guild.get_member(user_id)
            if member is None:
                continue
            if any(r.id == role_id for r in member.roles):
                return True
        return False

    # ------------------------------------------------------------------
    # Presence sync
    # ------------------------------------------------------------------

    async def _sync_all_guilds(self) -> None:
        for guild in self.bot.guilds:
            if await self.config.guild(guild).enabled():
                try:
                    await self._full_sync(guild)
                except Exception as exc:
                    log.error("pixelagents: sync error for guild %s: %s", guild.id, exc)

    async def _full_sync(self, guild: discord.Guild) -> str:
        include_bots = await self.config.guild(guild).include_bots()
        errors = 0
        current_user_ids = {m.id for m in guild.members}

        # Close agents that are no longer in the guild
        stale = [(gid, uid) for (gid, uid) in list(self._agents) if gid == guild.id and uid not in current_user_ids]
        for key in stale:
            await self._close_agent(*key)

        for member in guild.members:
            try:
                await self._reconcile_member(member, include_bots)
            except Exception as exc:
                log.error("pixelagents: reconcile error for %s: %s", member.id, exc)
                errors += 1
        return f"Sync complete. Errors: {errors}." if errors else "Sync complete."

    def _status_str(self, member: discord.Member) -> Optional[str]:
        s = str(member.status)
        return s if s in _VISIBLE_STATUSES else None

    def _is_included(self, member: discord.Member, include_bots: bool) -> bool:
        return not (member.bot and not include_bots)

    def _has_rich_presence(self, member: discord.Member) -> bool:
        return any(a.type != discord.ActivityType.custom for a in member.activities)

    def _agent_status(self, member: discord.Member) -> str:
        return "active" if self._has_rich_presence(member) else "waiting"

    async def _reconcile_member(self, member: discord.Member, include_bots: bool) -> None:
        guild_id = member.guild.id
        user_id = member.id
        folder = self._status_str(member)

        if folder is None or not self._is_included(member, include_bots):
            if (guild_id, user_id) in self._agents:
                await self._close_agent(guild_id, user_id)
            return

        name = member.display_name
        cached = self._agents.get((guild_id, user_id))

        if cached is None:
            await self._spawn_agent(guild_id, user_id, name, folder, member)
            return

        cached_folder, cached_name = cached
        if folder != cached_folder:
            self._agents[(guild_id, user_id)] = (folder, name)
            agent_id = self._agent_id(user_id)
            await self._send({"type": "agentCreated", "id": agent_id, "folderName": folder})
            await self._send_existing_agents()
        elif name != cached_name:
            self._agents[(guild_id, user_id)] = (folder, name)
            await self._send({"type": "agentTeamInfo", "id": self._agent_id(user_id), "agentName": name})

    def _is_user_active_in_other_guild(self, guild_id: int, user_id: int) -> bool:
        return any(gid != guild_id and uid == user_id for (gid, uid) in self._agents)

    async def _spawn_agent(
        self, guild_id: int, user_id: int, name: str, folder: str, member: discord.Member
    ) -> None:
        self._detect_collision(user_id)
        agent_id = self._agent_id(user_id)
        already_active = self._is_user_active_in_other_guild(guild_id, user_id)
        self._agents[(guild_id, user_id)] = (folder, name)

        if not already_active:
            await self._send({"type": "agentCreated", "id": agent_id, "folderName": folder})
            await self._send({"type": "agentTeamInfo", "id": agent_id, "agentName": name})
            status = self._agent_status(member)
            await self._send({"type": "agentStatus", "id": agent_id, "status": status})
        await self._send_existing_agents()

    async def _close_agent(self, guild_id: int, user_id: int) -> None:
        if (guild_id, user_id) not in self._agents:
            return
        agent_id = self._agent_id(user_id)
        del self._agents[(guild_id, user_id)]
        if not self._is_user_active_in_other_guild(guild_id, user_id):
            await self._send({"type": "agentClosed", "id": agent_id})
        await self._send_existing_agents()

    async def _despawn_guild(self, guild: discord.Guild) -> None:
        keys = [(gid, uid) for (gid, uid) in list(self._agents) if gid == guild.id]
        for key in keys:
            await self._close_agent(*key)

    async def _clear_tool_after_delay(self, agent_id: int, delay: float) -> None:
        await asyncio.sleep(delay)
        await self._send({"type": "agentToolsClear", "id": agent_id})

    # ------------------------------------------------------------------
    # Reply helper
    # ------------------------------------------------------------------

    async def _reply(self, ctx: commands.Context, content=None, **kwargs) -> None:
        if ctx.interaction:
            kwargs["ephemeral"] = True
            if not ctx.interaction.response.is_done():
                await ctx.interaction.response.send_message(content, **kwargs)
            else:
                await ctx.interaction.followup.send(content, **kwargs)
        else:
            await ctx.send(content, **kwargs)

    # ------------------------------------------------------------------
    # Discord event listeners
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if not await self.config.guild(after.guild).enabled():
            return
        if before.display_name == after.display_name:
            return
        include_bots = await self.config.guild(after.guild).include_bots()
        try:
            await self._reconcile_member(after, include_bots)
        except Exception as exc:
            log.error("on_member_update error for %s: %s", after.id, exc)

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member) -> None:
        if not await self.config.guild(after.guild).enabled():
            return
        if before.status == after.status and before.activities == after.activities:
            return
        include_bots = await self.config.guild(after.guild).include_bots()
        try:
            await self._reconcile_member(after, include_bots)
        except Exception as exc:
            log.error("on_presence_update error for %s: %s", after.id, exc)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if not await self.config.guild(member.guild).enabled():
            return
        if self._status_str(member) is None:
            return
        include_bots = await self.config.guild(member.guild).include_bots()
        try:
            await self._reconcile_member(member, include_bots)
        except Exception as exc:
            log.error("on_member_join error for %s: %s", member.id, exc)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if not await self.config.guild(member.guild).enabled():
            return
        try:
            await self._close_agent(member.guild.id, member.id)
        except Exception as exc:
            log.error("on_member_remove error for %s: %s", member.id, exc)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        if not await self.config.guild(message.guild).enabled():
            return
        guild_id = message.guild.id
        user_id = message.author.id
        if (guild_id, user_id) not in self._agents:
            return
        agent_id = self._agent_id(user_id)
        content = message.content or ""
        if len(content) > 40:
            content = content[:40] + "…"
        tool_id = f"msg-{message.id}"
        await self._send({
            "type": "agentToolStart",
            "id": agent_id,
            "toolId": tool_id,
            "toolName": "Message",
            "status": content,
        })
        delay = await self.config.message_tool_clear_delay()
        asyncio.get_event_loop().create_task(self._clear_tool_after_delay(agent_id, delay))

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @commands.hybrid_group(name="pixelagents", invoke_without_command=True)
    @commands.admin_or_permissions(administrator=True)
    @commands.guild_only()
    async def pixelagents_group(self, ctx: commands.Context) -> None:
        """Manage Pixelagents presence mirroring."""
        await ctx.send_help()

    @pixelagents_group.command(name="status")
    async def cmd_status(self, ctx: commands.Context) -> None:
        """Show current Pixelagents configuration and connection status."""
        producer_url = await self.config.producer_url()
        clear_delay = await self.config.message_tool_clear_delay()
        editor_role_id = await self.config.editor_role_id()
        enabled = await self.config.guild(ctx.guild).enabled()
        include_bots = await self.config.guild(ctx.guild).include_bots()
        tracked = sum(1 for (gid, _) in self._agents if gid == ctx.guild.id)
        connected = self._ws is not None and not self._ws.closed

        embed = discord.Embed(title="Pixelagents Status", color=discord.Color.blurple())
        embed.add_field(name="Producer URL", value=producer_url, inline=False)
        embed.add_field(name="Connected", value="Yes" if connected else "No", inline=True)
        embed.add_field(name="Msg Tool Clear Delay", value=f"{clear_delay}s", inline=True)
        embed.add_field(
            name="Editor Role ID",
            value=str(editor_role_id) if editor_role_id else "Not set",
            inline=True,
        )
        embed.add_field(name="Guild Enabled", value="Yes" if enabled else "No", inline=True)
        embed.add_field(name="Include Bots", value="Yes" if include_bots else "No", inline=True)
        embed.add_field(name="Tracked Agents", value=str(tracked), inline=True)

        await self._reply(ctx, embed=embed)

    @pixelagents_group.command(name="producerurl")
    @app_commands.describe(url="Producer WebSocket URL (default: ws://standalone:3210/ws/producer)")
    async def cmd_producerurl(self, ctx: commands.Context, url: str) -> None:
        """Set the producer WebSocket URL."""
        await self.config.producer_url.set(url)
        await self._reply(ctx, f"Producer URL set to `{url}`.")

    @pixelagents_group.command(name="toolcleardelay")
    @app_commands.describe(seconds="Seconds to keep the message activity indicator visible")
    async def cmd_toolcleardelay(self, ctx: commands.Context, seconds: float) -> None:
        """Set how long (in seconds) a message tool indicator stays visible (default: 2.0)."""
        if seconds < 0:
            await self._reply(ctx, "Delay must be 0 or greater.")
            return
        await self.config.message_tool_clear_delay.set(seconds)
        await self._reply(ctx, f"Message tool clear delay set to `{seconds}s`.")

    @pixelagents_group.command(name="editorrole")
    @app_commands.describe(role="Discord role that grants webview editor access (omit to clear)")
    async def cmd_editorrole(self, ctx: commands.Context, role: Optional[discord.Role] = None) -> None:
        """Set the Discord role that grants webview editor access. Omit to clear."""
        if role is None:
            await self.config.editor_role_id.set(None)
            await self._reply(ctx, "Editor role cleared.")
        else:
            await self.config.editor_role_id.set(role.id)
            await self._reply(ctx, f"Editor role set to `{role.name}` (ID: {role.id}).")

    @pixelagents_group.command(name="enable")
    async def cmd_enable(self, ctx: commands.Context) -> None:
        """Enable Pixelpipes presence mirroring for this guild and run a full sync."""
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
        await self.config.guild(ctx.guild).enabled.set(True)
        await self._reply(ctx, "Enabled. Running full sync…")
        result = await self._full_sync(ctx.guild)
        await self._reply(ctx, result)

    @pixelagents_group.command(name="disable")
    async def cmd_disable(self, ctx: commands.Context) -> None:
        """Disable Pixelpipes presence mirroring for this guild and despawn all agents."""
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
        await self.config.guild(ctx.guild).enabled.set(False)
        await self._reply(ctx, "Disabled. Despawning all tracked agents…")
        await self._despawn_guild(ctx.guild)
        await self._reply(ctx, "Done.")

    @pixelagents_group.command(name="includebots")
    @app_commands.describe(value="Whether bot users should be mirrored")
    async def cmd_includebots(self, ctx: commands.Context, value: bool) -> None:
        """Set whether bot users are mirrored (true/false)."""
        await self.config.guild(ctx.guild).include_bots.set(value)
        await self._reply(ctx, f"include_bots set to `{value}`. Running sync…")
        if await self.config.guild(ctx.guild).enabled():
            result = await self._full_sync(ctx.guild)
            await self._reply(ctx, result)

    @pixelagents_group.command(name="sync")
    async def cmd_sync(self, ctx: commands.Context) -> None:
        """Manually reconcile all guild members against their current Discord presence."""
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
        if not await self.config.guild(ctx.guild).enabled():
            await self._reply(ctx, "Guild is not enabled. Use `[p]pixelagents enable` first.")
            return
        await self._reply(ctx, "Syncing…")
        result = await self._full_sync(ctx.guild)
        await self._reply(ctx, result)

    @pixelagents_group.command(name="despawnall")
    async def cmd_despawnall(self, ctx: commands.Context) -> None:
        """Despawn all tracked agents for this guild without disabling the cog."""
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
        await self._reply(ctx, "Despawning all tracked agents for this guild…")
        await self._despawn_guild(ctx.guild)
        await self._reply(ctx, "Done.")

    async def red_delete_data_for_user(self, *, requester, user_id: int) -> None:
        pass
