from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import time
import uuid
from typing import Any, Dict, Optional, Set, Tuple

import aiohttp
import discord
from discord import app_commands
from redbot.core import Config, commands
from redbot.core.bot import Red

log = logging.getLogger("red.d_cogs.pixelagents")

_VISIBLE_STATUSES = {"online", "idle", "dnd"}
_LAYOUT_REQUEST_TIMEOUT = 10.0
_MAX_LAYOUTS_PER_USER = 20
_MAX_LAYOUT_BYTES = 1024 * 1024
_LAYOUT_NAME_RE = re.compile(r"^[A-Za-z0-9 _.-]{1,64}$")

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
            broadcast_rich_presence=True,
            broadcast_messages=True,
        )
        self.config.register_guild(
            enabled=False,
            include_bots=True,
        )
        self.config.register_user(
            layouts={},
        )
        # Active agents: (guild_id, user_id) -> (folder_name, display_name)
        self._agents: Dict[Tuple[int, int], Tuple[str, str]] = {}
        # Current rich presence label per agent, absent when no presence
        self._presence_cache: Dict[Tuple[int, int], str] = {}
        # Known collisions (agent_id) already logged
        self._logged_collisions: Set[int] = set()
        # WebSocket state
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._ws_session: Optional[aiohttp.ClientSession] = None
        self._connect_task: Optional[asyncio.Task] = None
        self._pending_layout_requests: Dict[str, asyncio.Future] = {}
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
        await self._send({"type": "producerHello", "capabilities": ["auth-check", "layout-control"]})

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

    async def _request_layout_snapshot(self) -> dict:
        return await self._request_layout_control({"type": "producerLayoutSnapshotRequest"})

    async def _request_layout_load(self, layout: dict) -> dict:
        return await self._request_layout_control({"type": "producerLayoutLoadRequest", "layout": layout})

    async def _request_layout_control(self, message: dict) -> dict:
        if self._ws is None or self._ws.closed:
            raise RuntimeError("Pixelpipes producer WebSocket is not connected.")

        request_id = str(uuid.uuid4())
        message["requestId"] = request_id
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._pending_layout_requests[request_id] = future
        await self._send(message)
        try:
            return await asyncio.wait_for(future, timeout=_LAYOUT_REQUEST_TIMEOUT)
        except asyncio.TimeoutError as exc:
            self._pending_layout_requests.pop(request_id, None)
            raise RuntimeError("Timed out waiting for Pixelpipes layout reply.") from exc

    def _normalize_layout_name(self, name: str) -> Optional[str]:
        clean = name.strip()
        if not _LAYOUT_NAME_RE.fullmatch(clean):
            return None
        return clean.casefold()

    def _layout_size(self, layout: dict) -> int:
        return len(json.dumps(layout, separators=(",", ":"), sort_keys=True).encode("utf-8"))

    def _validate_layout(self, layout: Any) -> bool:
        if not isinstance(layout, dict):
            return False
        if layout.get("version") != 1:
            return False
        cols = layout.get("cols")
        rows = layout.get("rows")
        tiles = layout.get("tiles")
        furniture = layout.get("furniture")
        if not isinstance(cols, int) or cols <= 0:
            return False
        if not isinstance(rows, int) or rows <= 0:
            return False
        if not isinstance(tiles, list) or len(tiles) != cols * rows:
            return False
        if not isinstance(furniture, list):
            return False
        tile_colors = layout.get("tileColors")
        if tile_colors is not None and (not isinstance(tile_colors, list) or len(tile_colors) != cols * rows):
            return False
        return True

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
        for future in self._pending_layout_requests.values():
            if not future.done():
                future.set_exception(RuntimeError("pixelagents cog unloaded"))
        self._pending_layout_requests.clear()

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
        elif msg_type in ("producerLayoutSnapshotReply", "producerLayoutLoadReply"):
            request_id = data.get("requestId", "")
            future = self._pending_layout_requests.pop(request_id, None)
            if future is not None and not future.done():
                future.set_result(data)

    # ------------------------------------------------------------------
    # Editor authorization
    # ------------------------------------------------------------------

    async def _check_auth(self, user_id: int) -> bool:
        return await self._can_edit_layout_user(user_id)

    async def _can_edit_layout_user(self, user_id: int) -> bool:
        if user_id == 0:
            return False
        if await self.bot.is_owner(discord.Object(id=user_id)):
            return True
        role_id = await self.config.editor_role_id()
        for guild in self.bot.guilds:
            if not await self.config.guild(guild).enabled():
                continue
            member = guild.get_member(user_id)
            if member is None:
                continue
            permissions = getattr(member, "guild_permissions", None)
            if getattr(permissions, "administrator", False) is True:
                return True
            if role_id is not None and any(r.id == role_id for r in getattr(member, "roles", [])):
                return True
        return False

    async def _can_edit_layout_ctx(self, ctx: commands.Context) -> bool:
        author = getattr(ctx, "author", None)
        user_id = getattr(author, "id", 0)
        return await self._can_edit_layout_user(user_id)

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

    def _pick_presence_activity(self, member: discord.Member) -> Optional[discord.Activity]:
        activities = [a for a in member.activities if a.type != discord.ActivityType.custom]
        for a in activities:
            if a.type == discord.ActivityType.listening:
                return a
        return activities[0] if activities else None

    def _build_presence_label(self, member: discord.Member) -> Optional[str]:
        activity = self._pick_presence_activity(member)
        if activity is None:
            return None
        if activity.type == discord.ActivityType.listening:
            if isinstance(activity, discord.Spotify) and activity.title and activity.artist:
                return f"{activity.title} — {activity.artist}"
            details = getattr(activity, "details", None)
            state = getattr(activity, "state", None)
            if details and state:
                return f"{details} — {state}"
            return activity.name or None
        return activity.name or None

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
            if name != cached_name:
                await self._send({"type": "agentTeamInfo", "id": agent_id, "agentName": name})
            await self._send_existing_agents()
        elif name != cached_name:
            self._agents[(guild_id, user_id)] = (folder, name)
            await self._send({"type": "agentTeamInfo", "id": self._agent_id(user_id), "agentName": name})
        await self._update_presence_tool(guild_id, user_id, member)

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
        if await self.config.broadcast_rich_presence():
            label = self._build_presence_label(member)
            if label:
                self._presence_cache[(guild_id, user_id)] = label
                await self._send_presence_tool(agent_id, label)

    async def _close_agent(self, guild_id: int, user_id: int) -> None:
        if (guild_id, user_id) not in self._agents:
            return
        agent_id = self._agent_id(user_id)
        del self._agents[(guild_id, user_id)]
        self._presence_cache.pop((guild_id, user_id), None)
        if not self._is_user_active_in_other_guild(guild_id, user_id):
            await self._send({"type": "agentClosed", "id": agent_id})
        await self._send_existing_agents()

    async def _despawn_guild(self, guild: discord.Guild) -> None:
        keys = [(gid, uid) for (gid, uid) in list(self._agents) if gid == guild.id]
        for key in keys:
            await self._close_agent(*key)

    async def _send_presence_tool(self, agent_id: int, label: str) -> None:
        await self._send({
            "type": "agentToolStart",
            "id": agent_id,
            "toolId": f"rp-{agent_id}",
            "toolName": "Activity",
            "status": label,
        })

    async def _update_presence_tool(
        self, guild_id: int, user_id: int, member: discord.Member
    ) -> None:
        if not await self.config.broadcast_rich_presence():
            return
        agent_id = self._agent_id(user_id)
        label = self._build_presence_label(member)
        cached = self._presence_cache.get((guild_id, user_id))
        if label == cached:
            return
        if label:
            self._presence_cache[(guild_id, user_id)] = label
            await self._send_presence_tool(agent_id, label)
        else:
            self._presence_cache.pop((guild_id, user_id), None)
            await self._send({"type": "agentToolsClear", "id": agent_id})

    async def _clear_tool_after_delay(
        self, agent_id: int, delay: float, guild_id: int = 0, user_id: int = 0
    ) -> None:
        await asyncio.sleep(delay)
        await self._send({"type": "agentToolsClear", "id": agent_id})
        if guild_id and user_id:
            label = self._presence_cache.get((guild_id, user_id))
            if label:
                await self._send_presence_tool(agent_id, label)

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

    async def _send_public(self, ctx: commands.Context, content=None, **kwargs) -> None:
        if ctx.interaction:
            kwargs["ephemeral"] = False
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
        if not await self.config.broadcast_messages():
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
        asyncio.get_event_loop().create_task(
            self._clear_tool_after_delay(agent_id, delay, guild_id, user_id)
        )

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @commands.hybrid_group(name="pixelagents", invoke_without_command=True)
    @commands.guild_only()
    async def pixelagents_group(self, ctx: commands.Context) -> None:
        """Manage Pixelagents presence mirroring."""
        await ctx.send_help()

    @pixelagents_group.command(name="status")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_status(self, ctx: commands.Context) -> None:
        """Show current Pixelagents configuration and connection status."""
        producer_url = await self.config.producer_url()
        clear_delay = await self.config.message_tool_clear_delay()
        editor_role_id = await self.config.editor_role_id()
        broadcast_rp = await self.config.broadcast_rich_presence()
        broadcast_msg = await self.config.broadcast_messages()
        enabled = await self.config.guild(ctx.guild).enabled()
        include_bots = await self.config.guild(ctx.guild).include_bots()
        tracked = sum(1 for (gid, _) in self._agents if gid == ctx.guild.id)
        connected = self._ws is not None and not self._ws.closed

        def yn(value: bool) -> str:
            return "✅" if value else "🛑"

        embed = discord.Embed(title="Pixelagents Status", color=discord.Color.blurple())
        embed.add_field(name="Producer URL", value=producer_url, inline=False)
        embed.add_field(name="Connected", value=yn(connected), inline=True)
        embed.add_field(name="Msg Tool Clear Delay", value=f"{clear_delay}s", inline=True)
        embed.add_field(
            name="Editor Role ID",
            value=str(editor_role_id) if editor_role_id else "⚠️ Not set",
            inline=True,
        )
        embed.add_field(name="Guild Enabled", value=yn(enabled), inline=True)
        embed.add_field(name="Include Bots", value=yn(include_bots), inline=True)
        embed.add_field(name="Tracked Agents", value=str(tracked), inline=True)
        embed.add_field(name="Broadcast Rich Presence", value=yn(broadcast_rp), inline=True)
        embed.add_field(name="Broadcast Messages", value=yn(broadcast_msg), inline=True)

        await self._reply(ctx, embed=embed)

    @pixelagents_group.command(name="producerurl")
    @commands.admin_or_permissions(administrator=True)
    @app_commands.describe(url="Producer WebSocket URL (default: ws://standalone:3210/ws/producer)")
    async def cmd_producerurl(self, ctx: commands.Context, url: str) -> None:
        """Set the producer WebSocket URL."""
        await self.config.producer_url.set(url)
        await self._reply(ctx, f"Producer URL set to `{url}`.")

    @pixelagents_group.command(name="toolcleardelay")
    @commands.admin_or_permissions(administrator=True)
    @app_commands.describe(seconds="Seconds to keep the message activity indicator visible")
    async def cmd_toolcleardelay(self, ctx: commands.Context, seconds: float) -> None:
        """Set how long (in seconds) a message tool indicator stays visible (default: 2.0)."""
        if seconds < 0:
            await self._reply(ctx, "Delay must be 0 or greater.")
            return
        await self.config.message_tool_clear_delay.set(seconds)
        await self._reply(ctx, f"Message tool clear delay set to `{seconds}s`.")

    @pixelagents_group.command(name="richpresence")
    @commands.admin_or_permissions(administrator=True)
    @app_commands.describe(value="Whether rich presence (Spotify, games, etc.) is shown in the webview")
    async def cmd_richpresence(self, ctx: commands.Context, value: bool) -> None:
        """Set whether rich presence activity is broadcast to the webview (true/false)."""
        await self.config.broadcast_rich_presence.set(value)
        await self._reply(ctx, f"Rich presence broadcasting set to `{value}`.")

    @pixelagents_group.command(name="messages")
    @commands.admin_or_permissions(administrator=True)
    @app_commands.describe(value="Whether Discord messages are shown as tool bubbles in the webview")
    async def cmd_messages(self, ctx: commands.Context, value: bool) -> None:
        """Set whether Discord messages are broadcast as tool bubbles to the webview (true/false)."""
        await self.config.broadcast_messages.set(value)
        await self._reply(ctx, f"Message broadcasting set to `{value}`.")

    @pixelagents_group.command(name="editorrole")
    @commands.admin_or_permissions(administrator=True)
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
    @commands.admin_or_permissions(administrator=True)
    async def cmd_enable(self, ctx: commands.Context) -> None:
        """Enable Pixelpipes presence mirroring for this guild and run a full sync."""
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
        await self.config.guild(ctx.guild).enabled.set(True)
        await self._reply(ctx, "Enabled. Running full sync…")
        result = await self._full_sync(ctx.guild)
        await self._reply(ctx, result)

    @pixelagents_group.command(name="disable")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_disable(self, ctx: commands.Context) -> None:
        """Disable Pixelpipes presence mirroring for this guild and despawn all agents."""
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
        await self.config.guild(ctx.guild).enabled.set(False)
        await self._reply(ctx, "Disabled. Despawning all tracked agents…")
        await self._despawn_guild(ctx.guild)
        await self._reply(ctx, "Done.")

    @pixelagents_group.command(name="includebots")
    @commands.admin_or_permissions(administrator=True)
    @app_commands.describe(value="Whether bot users should be mirrored")
    async def cmd_includebots(self, ctx: commands.Context, value: bool) -> None:
        """Set whether bot users are mirrored (true/false)."""
        await self.config.guild(ctx.guild).include_bots.set(value)
        await self._reply(ctx, f"include_bots set to `{value}`. Running sync…")
        if await self.config.guild(ctx.guild).enabled():
            result = await self._full_sync(ctx.guild)
            await self._reply(ctx, result)

    @pixelagents_group.command(name="sync")
    @commands.admin_or_permissions(administrator=True)
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
    @commands.admin_or_permissions(administrator=True)
    async def cmd_despawnall(self, ctx: commands.Context) -> None:
        """Despawn all tracked agents for this guild without disabling the cog."""
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
        await self._reply(ctx, "Despawning all tracked agents for this guild…")
        await self._despawn_guild(ctx.guild)
        await self._reply(ctx, "Done.")

    @pixelagents_group.group(name="layout", invoke_without_command=True)
    async def pixelagents_layout_group(self, ctx: commands.Context) -> None:
        """Manage saved Pixelpipes layouts."""
        await ctx.send_help()

    async def _require_layout_editor(self, ctx: commands.Context) -> bool:
        if await self._can_edit_layout_ctx(ctx):
            return True
        await self._reply(ctx, "You are not authorized to manage Pixel Agents layouts.")
        return False

    async def _get_user_layouts(self, user) -> dict:
        layouts = await self.config.user(user).layouts()
        return dict(layouts or {})

    async def _set_user_layouts(self, user, layouts: dict) -> None:
        await self.config.user(user).layouts.set(layouts)

    @pixelagents_layout_group.command(name="save")
    @app_commands.describe(
        name="Saved layout name",
        overwrite="Overwrite an existing saved layout with this name",
    )
    async def cmd_layout_save(self, ctx: commands.Context, name: str, overwrite: bool = False) -> None:
        """Save the standalone host's current persisted layout."""
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
        if not await self._require_layout_editor(ctx):
            return

        key = self._normalize_layout_name(name)
        if key is None:
            await self._reply(ctx, "Layout names must be 1-64 characters and may only use letters, numbers, spaces, `_`, `-`, and `.`.")
            return

        layouts = await self._get_user_layouts(ctx.author)
        if key in layouts and not overwrite:
            await self._reply(ctx, "A layout with that name already exists. Re-run with `overwrite: true` to replace it.")
            return
        if key not in layouts and len(layouts) >= _MAX_LAYOUTS_PER_USER:
            await self._reply(ctx, f"You can save at most {_MAX_LAYOUTS_PER_USER} layouts. Delete one first.")
            return

        try:
            reply = await self._request_layout_snapshot()
        except RuntimeError as exc:
            await self._reply(ctx, str(exc))
            return

        if not reply.get("ok"):
            await self._reply(ctx, f"Could not read the current Pixelpipes layout: {reply.get('error', 'unknown error')}")
            return

        layout = reply.get("layout")
        if not self._validate_layout(layout):
            await self._reply(ctx, "Pixelpipes returned an invalid layout.")
            return

        size = self._layout_size(layout)
        if size > _MAX_LAYOUT_BYTES:
            await self._reply(ctx, f"Layout is too large to save ({size} bytes, limit {_MAX_LAYOUT_BYTES} bytes).")
            return

        now = int(time.time())
        existing = layouts.get(key, {})
        display_name = name.strip()
        layouts[key] = {
            "display_name": display_name,
            "created_at": existing.get("created_at", now),
            "updated_at": now,
            "size": size,
            "layout": layout,
        }
        await self._set_user_layouts(ctx.author, layouts)
        action = "Overwrote" if existing else "Saved"
        await self._reply(ctx, f"{action} layout `{display_name}` ({size} bytes).")

    @pixelagents_layout_group.command(name="load")
    @app_commands.describe(name="Saved layout name")
    async def cmd_layout_load(self, ctx: commands.Context, name: str) -> None:
        """Load one of your saved layouts into the shared frontend."""
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
        if not await self._require_layout_editor(ctx):
            return

        key = self._normalize_layout_name(name)
        layouts = await self._get_user_layouts(ctx.author)
        record = layouts.get(key or "")
        if record is None:
            await self._reply(ctx, "No saved layout found with that name.")
            return

        layout = record.get("layout")
        if not self._validate_layout(layout):
            await self._reply(ctx, "Saved layout is invalid and cannot be loaded.")
            return

        try:
            reply = await self._request_layout_load(layout)
        except RuntimeError as exc:
            await self._reply(ctx, str(exc))
            return

        if not reply.get("ok"):
            await self._reply(ctx, f"Could not load layout: {reply.get('error', 'unknown error')}")
            return
        await self._reply(ctx, f"Loaded layout `{record.get('display_name', name.strip())}`.")

    @pixelagents_layout_group.command(name="delete")
    @app_commands.describe(name="Saved layout name")
    async def cmd_layout_delete(self, ctx: commands.Context, name: str) -> None:
        """Delete one of your saved layouts."""
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
        if not await self._require_layout_editor(ctx):
            return

        key = self._normalize_layout_name(name)
        layouts = await self._get_user_layouts(ctx.author)
        record = layouts.pop(key or "", None)
        if record is None:
            await self._reply(ctx, "No saved layout found with that name.")
            return
        await self._set_user_layouts(ctx.author, layouts)
        await self._reply(ctx, f"Deleted layout `{record.get('display_name', name.strip())}`.")

    @pixelagents_layout_group.command(name="list")
    async def cmd_layout_list(self, ctx: commands.Context) -> None:
        """List your saved layouts."""
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
        if not await self._require_layout_editor(ctx):
            return

        layouts = await self._get_user_layouts(ctx.author)
        if not layouts:
            await self._reply(ctx, "You have no saved layouts.")
            return

        records = sorted(layouts.values(), key=lambda item: item.get("updated_at", 0), reverse=True)
        lines = []
        for record in records:
            updated_at = int(record.get("updated_at", 0))
            timestamp = f"<t:{updated_at}:R>" if updated_at else "unknown time"
            lines.append(f"- `{record.get('display_name', 'unnamed')}` ({record.get('size', 0)} bytes, updated {timestamp})")
        await self._reply(ctx, "\n".join(lines))

    @pixelagents_layout_group.command(name="share")
    @app_commands.describe(name="Saved layout name")
    async def cmd_layout_share(self, ctx: commands.Context, name: str) -> None:
        """Upload one of your saved layouts as layout.json."""
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=False)
        if not await self._require_layout_editor(ctx):
            return

        key = self._normalize_layout_name(name)
        layouts = await self._get_user_layouts(ctx.author)
        record = layouts.get(key or "")
        if record is None:
            await self._reply(ctx, "No saved layout found with that name.")
            return

        layout = record.get("layout")
        if not self._validate_layout(layout):
            await self._reply(ctx, "Saved layout is invalid and cannot be shared.")
            return

        payload = json.dumps(layout, indent=2, sort_keys=True).encode("utf-8")
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", record.get("display_name", name.strip())).strip("-") or "layout"
        file = discord.File(io.BytesIO(payload), filename=f"{safe_name}.layout.json")
        await self._send_public(ctx, f"Shared Pixel Agents layout `{record.get('display_name', name.strip())}`.", file=file)

    async def red_delete_data_for_user(self, *, requester, user_id: int) -> None:
        await self.config.user_from_id(user_id).layouts.set({})
