from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
from pathlib import Path
import random
import re
import secrets
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

import aiohttp
from aiohttp import WSMsgType, web
import discord
from discord import app_commands
from redbot.core import Config, commands
from redbot.core.bot import Red

log = logging.getLogger("red.d_cogs.pixelagents")

_VISIBLE_STATUSES = {"online", "idle", "dnd"}
_WEBVIEW_CACHE_CONTROL = "public, max-age=3600"
_DEFAULT_PIXEL_INDEX_API_URL = "https://pixel-index-api-staging.nntin.xyz"
_DEFAULT_PIXEL_INDEX_WEB_URL = "https://pixel-index.vercel.app"
_PIXEL_INDEX_HEALTH_TIMEOUT = 5.0
_PIXEL_INDEX_REQUEST_TIMEOUT = 10.0
_LAYOUT_SEARCH_PAGE_SIZE = 5
_LAYOUT_SORT_CHOICES = ("newest", "furniture", "largest", "title")

# JavaScript Number.MAX_SAFE_INTEGER = 2^53 - 1 = 9007199254740991
_JS_MAX_SAFE = (1 << 53) - 1

# How long an editor ticket minted by the dashboard page stays valid. Long
# enough to survive a reconnect or a page left open; short enough that a leaked
# URL fragment stops working the same day.
_TICKET_TTL = 8 * 60 * 60

# Bundled character palettes (char_0.png .. char_5.png).
_PALETTE_COUNT = 6

# Keys the AsyncAPI FurnitureAssetMessage allows. buildFurnitureCatalog emits
# `furniturePath` on top of these for its own PNG loading; the contract sets
# additionalProperties: false, so it is stripped before broadcast.
_FURNITURE_KEYS = frozenset({
    "id", "name", "label", "category", "file", "width", "height",
    "footprintW", "footprintH", "isDesk", "canPlaceOnWalls", "groupId",
    "canPlaceOnSurfaces", "backgroundTiles", "orientation", "state",
    "mirrorSide", "rotationScheme", "animationGroup", "frame",
})

# Mutating client messages. Everything else a viewer sends is harmless, but
# these change shared state and are dropped unless the socket is authorized.
_EDITOR_MESSAGES = frozenset({"saveLayout", "saveAgentSeats", "importLayout"})

# Upstream's Claude provider capabilities. The office uses these to pick the
# reading vs typing animation; Discord activity labels are rendered the same
# way, so we mirror the reference implementation's sets.
_READING_TOOLS = ["Read", "Grep", "Glob", "WebFetch", "WebSearch"]
_SUBAGENT_TOOL_NAMES = ["Task", "Agent"]

# Injected ahead of the bundle so the office's WebSocket can be upgraded to an
# editor session without the webview page itself requiring a dashboard login.
# The webview is public (anonymous visitors connect as read-only viewers); this
# shim opens the socket immediately and, in parallel, asks the `session` page
# (which *does* require login) for a ticket. Logged-in visitors get one and the
# shim sends it over the already-open socket; anonymous visitors get a failed
# fetch, swallowed silently, and stay viewers. Upstream builds its socket URL
# as `<origin>/ws` with no room for a credential, and Traefik routes /ws past
# the dashboard, so the session cookie never reaches the socket either way.
# Wrapping the constructor keeps the vendored bundle byte-identical to what
# upstream builds.
_TICKET_SHIM = """<script>
(function () {
  var Native = window.WebSocket;
  var ticketPromise = fetch(location.pathname + '/session', {
    credentials: 'same-origin',
    headers: { Accept: 'application/json' },
  })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (data) { return (data && data.ticket) || null; })
    .catch(function () { return null; });

  function authorize(socket) {
    ticketPromise.then(function (ticket) {
      if (!ticket) { return; }
      var payload = JSON.stringify({ type: 'authorize', ticket: ticket });
      if (socket.readyState === Native.OPEN) {
        socket.send(payload);
        return;
      }
      if (socket.readyState === Native.CONNECTING) {
        socket.addEventListener('open', function once() {
          socket.removeEventListener('open', once);
          socket.send(payload);
        });
      }
    });
  }

  function Patched(url, protocols) {
    var socket = protocols === undefined ? new Native(url) : new Native(url, protocols);
    if (typeof url === 'string' && url.indexOf('/ws') !== -1) {
      authorize(socket);
    }
    return socket;
  }
  Patched.prototype = Native.prototype;
  Patched.CONNECTING = Native.CONNECTING;
  Patched.OPEN = Native.OPEN;
  Patched.CLOSING = Native.CLOSING;
  Patched.CLOSED = Native.CLOSED;
  window.WebSocket = Patched;
})();
</script>"""


def dashboard_page(*args, **kwargs):
    def decorator(func: Callable):
        func.__dashboard_decorator_params__ = (args, kwargs)
        return func

    return decorator


def _discord_id_to_agent_id(user_id: int) -> int:
    """Map a Discord user ID to a stable negative JavaScript-safe integer.

    Discord snowflakes are up to 64 bits. We take user_id modulo JS_MAX_SAFE
    and negate. If the result is 0 (user_id is a multiple of JS_MAX_SAFE),
    we use -JS_MAX_SAFE to guarantee negativity.
    """
    mapped = user_id % _JS_MAX_SAFE
    return -(mapped if mapped != 0 else _JS_MAX_SAFE)


class pixelagents(commands.Cog):
    """Serve the Pixel Agents office and mirror Discord guild presence into it."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0x706978656C61, force_registration=True)
        self.config.register_global(
            ws_host="0.0.0.0",
            ws_port=3210,
            message_tool_clear_delay=2.0,
            editor_role_id=None,
            broadcast_rich_presence=True,
            broadcast_messages=True,
            # The office layout is owned by this cog now that there is no
            # standalone host to hold it. None falls back to the bundled default.
            layout=None,
            # agent_id (as str) -> {palette, hueShift, seatId}
            seats={},
            pixel_index_api_url=_DEFAULT_PIXEL_INDEX_API_URL,
            pixel_index_web_url=_DEFAULT_PIXEL_INDEX_WEB_URL,
        )
        self.config.register_guild(
            enabled=False,
            include_bots=True,
        )
        # Active agents: (guild_id, user_id) -> (folder_name, display_name)
        self._agents: Dict[Tuple[int, int], Tuple[str, str]] = {}
        self._sync_task: Optional[asyncio.Task] = None
        # Current rich presence label per agent, absent when no presence
        self._presence_cache: Dict[Tuple[int, int], str] = {}
        # Known collisions (agent_id) already logged
        self._logged_collisions: Set[int] = set()
        # Office WebSocket server state
        self._runner: Optional[web.AppRunner] = None
        self._clients: Dict[web.WebSocketResponse, bool] = {}  # socket -> authorized
        # Editor tickets minted by the dashboard page: ticket -> (user_id, expiry)
        self._tickets: Dict[str, Tuple[int, float]] = {}
        # Decoded sprite payloads, loaded once from webview_dist/assets/decoded/
        self._assets: Dict[str, Any] = {}
        self._closing = False

    # ------------------------------------------------------------------
    # Dashboard webview hosting
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_dashboard_cog_add(self, dashboard_cog: commands.Cog) -> None:
        if not hasattr(dashboard_cog, "rpc"):
            return
        third_parties = getattr(dashboard_cog.rpc, "third_parties_handler", None)
        if third_parties is None:
            return
        third_parties.add_third_party(self, overwrite=True)

    def _webview_dist_root(self) -> Path:
        return Path(__file__).with_name("webview_dist")

    def _resolve_webview_asset(self, asset_path: str) -> Optional[Path]:
        clean_path = asset_path.strip().lstrip("/")
        if not clean_path or "\x00" in clean_path:
            return None

        root = self._webview_dist_root().resolve()
        candidate = (root / clean_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    def _content_type_for_asset(self, asset_path: str) -> str:
        if asset_path.endswith(".js"):
            return "text/javascript; charset=utf-8"
        if asset_path.endswith(".css"):
            return "text/css; charset=utf-8"
        if asset_path.endswith(".json") or asset_path.endswith(".webmanifest"):
            return "application/json; charset=utf-8"
        if asset_path.endswith(".svg"):
            return "image/svg+xml"
        if asset_path.endswith(".ico"):
            return "image/x-icon"
        if asset_path.endswith(".ttf"):
            return "font/ttf"
        guessed, _ = mimetypes.guess_type(asset_path)
        return guessed or "application/octet-stream"

    def _mint_ticket(self, user_id: int) -> str:
        """Issue a short-lived editor ticket bound to a Discord user ID."""
        now = time.time()
        # Opportunistically drop expired tickets so the dict cannot grow without
        # bound on a long-lived bot process.
        for value, (_, expiry) in list(self._tickets.items()):
            if expiry <= now:
                del self._tickets[value]
        ticket = secrets.token_urlsafe(32)
        self._tickets[ticket] = (user_id, now + _TICKET_TTL)
        return ticket

    def _resolve_ticket(self, ticket: str) -> Optional[int]:
        entry = self._tickets.get(ticket)
        if entry is None:
            return None
        user_id, expiry = entry
        if expiry <= time.time():
            del self._tickets[ticket]
            return None
        return user_id

    # No `user_id` (or any other context-id-shaped name) in this signature —
    # that's what keeps this page public. `dashboard_page` infers context_ids
    # from parameters with no default, so a bare `user_id: int` here would
    # make the dashboard force a login before serving the page at all. Do NOT
    # add one back and do NOT pass `context_ids` explicitly either (that skips
    # the inference branch and files a same-named param under required_kwargs
    # instead, 404ing unless the caller appends `?user_id=`). Editor
    # authorization is handled out-of-band by `dashboard_session` below.
    @dashboard_page(name=None, description="Pixel Agents webview.", methods=("GET",))
    async def dashboard_webview(self, **kwargs) -> dict:
        index_path = self._resolve_webview_asset("index.html")
        if index_path is None:
            return {
                "status": 1,
                "error_code": 503,
                "error_message": "Pixel Agents webview assets are not installed.",
            }

        source = index_path.read_text(encoding="utf-8")
        # Immediately after <head>, i.e. ahead of the bundle's own <script>.
        # The bundle is a deferred module so a later inline script would still
        # win, but relying on that is a trap for whoever edits this next.
        match = re.search(r"<head[^>]*>", source, re.IGNORECASE)
        if match:
            source = source[: match.end()] + "\n" + _TICKET_SHIM + source[match.end():]
        else:
            source = _TICKET_SHIM + source

        return {
            "status": 0,
            "web_content": {
                "standalone": True,
                "source": source,
            },
        }

    # Unlike `dashboard_webview`, `user_id` here has no default, so the
    # dashboard *does* require login for this one page and hands us the
    # visitor's Discord ID. That's intentional: it's the only way a visitor
    # can mint an editor ticket, and it's fetched in the background by the
    # shim above rather than being a page a person navigates to.
    @dashboard_page(
        name="session",
        description="Pixel Agents editor session ticket.",
        methods=("GET",),
        hidden=True,
    )
    async def dashboard_session(self, user_id: int, **kwargs) -> dict:
        body = json.dumps({"ticket": self._mint_ticket(user_id)}).encode("utf-8")
        return {
            "status": 0,
            "raw_response": {
                "status": 200,
                "content_type": "application/json",
                "body_base64": base64.b64encode(body).decode("ascii"),
                "headers": {"Cache-Control": "no-store"},
            },
        }

    @dashboard_page(name="static", description="Pixel Agents static asset.", methods=("GET", "HEAD"))
    async def dashboard_static(self, asset_path: str, **kwargs) -> dict:
        resolved = self._resolve_webview_asset(asset_path)
        if resolved is None:
            return {
                "status": 1,
                "error_code": 404,
                "error_message": "Pixel Agents asset not found.",
            }

        body = b"" if kwargs.get("method") == "HEAD" else resolved.read_bytes()
        return {
            "status": 0,
            "raw_response": {
                "status": 200,
                "content_type": self._content_type_for_asset(asset_path),
                "body_base64": base64.b64encode(body).decode("ascii"),
                "headers": {
                    "Cache-Control": _WEBVIEW_CACHE_CONTROL,
                },
            },
        }

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
    # Broadcast helpers
    # ------------------------------------------------------------------

    async def _send_to(self, socket: web.WebSocketResponse, message: dict) -> None:
        try:
            await socket.send_str(json.dumps(message))
        except Exception as exc:
            log.debug("pixelagents: send error: %s", exc)

    async def _send(self, message: dict) -> None:
        """Broadcast a ServerMessage to every connected office client."""
        if not self._clients:
            return
        try:
            payload = json.dumps(message)
        except TypeError as exc:
            # A malformed message must not take down the presence update that
            # produced it; drop it loudly instead.
            log.error("pixelagents: refusing to broadcast unserializable %s: %s",
                      message.get("type"), exc)
            return
        for socket in list(self._clients):
            if socket.closed:
                self._clients.pop(socket, None)
                continue
            try:
                await socket.send_str(payload)
            except Exception as exc:
                log.debug("pixelagents: broadcast error: %s", exc)

    def _tracked_user_ids(self) -> List[int]:
        """Distinct tracked users, ordered stably so agent lists don't churn."""
        seen: List[int] = []
        for (_, uid) in sorted(self._agents):
            if uid not in seen:
                seen.append(uid)
        return seen

    def _existing_agents_message(self, seats: dict) -> dict:
        agent_ids: List[int] = []
        folder_names: Dict[str, str] = {}
        agent_meta: Dict[str, dict] = {}
        for uid in self._tracked_user_ids():
            aid = self._agent_id(uid)
            agent_ids.append(aid)
            folder = next(
                (f for (_, u), (f, _) in sorted(self._agents.items()) if u == uid),
                None,
            )
            if folder:
                folder_names[str(aid)] = folder
            agent_meta[str(aid)] = self._seat_meta(aid, seats)
        return {
            "type": "existingAgents",
            "agents": agent_ids,
            "agentMeta": agent_meta,
            "folderNames": folder_names,
            "externalAgents": {},
        }

    async def _send_existing_agents(self) -> None:
        await self._send(self._existing_agents_message(await self.config.seats() or {}))

    # ------------------------------------------------------------------
    # Seats and palettes
    # ------------------------------------------------------------------

    def _seat_meta(self, agent_id: int, seats: Optional[dict]) -> dict:
        record = (seats or {}).get(str(agent_id)) or {}
        meta: Dict[str, Any] = {}
        for key in ("palette", "hueShift", "seatId"):
            if record.get(key) is not None:
                meta[key] = record[key]
        return meta

    async def _assign_palette(self, agent_id: int) -> Tuple[int, int]:
        """Pick a palette for a new agent, mirroring upstream's diverse assignment.

        Counts palettes already in use and picks randomly among the least-used
        ones, so the first six characters each look different. Beyond that,
        palettes repeat with a random hue shift.
        """
        seats = await self.config.seats() or {}
        record = seats.get(str(agent_id))
        if record and record.get("palette") is not None:
            return int(record["palette"]), int(record.get("hueShift") or 0)

        counts = [0] * _PALETTE_COUNT
        live = {str(self._agent_id(uid)) for uid in self._tracked_user_ids()}
        for key, value in seats.items():
            if key in live and value.get("palette") is not None:
                index = int(value["palette"])
                if 0 <= index < _PALETTE_COUNT:
                    counts[index] += 1

        fewest = min(counts)
        palette = random.choice([i for i, c in enumerate(counts) if c == fewest])
        hue_shift = 0 if fewest == 0 else random.randint(45, 315)

        seats[str(agent_id)] = {
            **(record or {}),
            "palette": palette,
            "hueShift": hue_shift,
        }
        await self.config.seats.set(seats)
        return palette, hue_shift

    # ------------------------------------------------------------------
    # Layout ownership
    # ------------------------------------------------------------------

    def _default_layout(self) -> Optional[dict]:
        """The layout bundled with the webview build, used until one is saved."""
        index_path = self._resolve_webview_asset("assets/asset-index.json")
        if index_path is None:
            return None
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        name = index.get("defaultLayout")
        if not name:
            return None
        layout_path = self._resolve_webview_asset(f"assets/{name}")
        if layout_path is None:
            return None
        try:
            return json.loads(layout_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("pixelagents: could not read bundled default layout: %s", exc)
            return None

    async def _current_layout(self) -> Optional[dict]:
        return await self.config.layout() or self._default_layout()

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

    def _load_assets(self) -> None:
        """Read the decoded sprite payloads emitted by scripts/build-webview.

        Blocking file reads, but they happen once at cog load and total a few
        hundred kB of JSON. The webview cannot render without them: the
        production bundle decodes nothing itself.
        """
        assets: Dict[str, Any] = {}
        for name in ("characters", "floors", "walls", "carpets", "furniture"):
            path = self._resolve_webview_asset(f"assets/decoded/{name}.json")
            if path is None:
                log.warning(
                    "pixelagents: missing assets/decoded/%s.json — run scripts/build-webview", name
                )
                continue
            try:
                assets[name] = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                log.error("pixelagents: could not read decoded %s: %s", name, exc)

        catalog_path = self._resolve_webview_asset("assets/furniture-catalog.json")
        if catalog_path is not None:
            try:
                raw = json.loads(catalog_path.read_text(encoding="utf-8"))
                assets["catalog"] = [
                    {k: v for k, v in entry.items() if k in _FURNITURE_KEYS} for entry in raw
                ]
            except Exception as exc:
                log.error("pixelagents: could not read furniture catalog: %s", exc)

        self._assets = assets
        log.info(
            "pixelagents: loaded assets — %d palettes, %d floors, %d wall sets, %d furniture sprites",
            len(assets.get("characters", [])),
            len(assets.get("floors", [])),
            len(assets.get("walls", [])),
            len(assets.get("furniture", {})),
        )

    async def cog_load(self) -> None:
        self._closing = False
        await asyncio.get_event_loop().run_in_executor(None, self._load_assets)
        await self._start_server()
        # The producer client used to sync on connect. Nothing dials out now, so
        # seed the agent set once the gateway cache is populated instead.
        self._sync_task = asyncio.get_event_loop().create_task(self._initial_sync())

    async def _initial_sync(self) -> None:
        try:
            await self.bot.wait_until_red_ready()
            await self._sync_all_guilds()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("pixelagents: initial sync failed: %s", exc)

    async def cog_unload(self) -> None:
        self._closing = True
        task = getattr(self, "_sync_task", None)
        if task is not None:
            task.cancel()
        for socket in list(self._clients):
            try:
                await socket.close()
            except Exception:
                pass
        self._clients.clear()
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def _start_server(self) -> None:
        host = await self.config.ws_host()
        port = await self.config.ws_port()
        app = web.Application()
        app.router.add_get("/ws", self._handle_ws)
        app.router.add_get("/api/health", self._handle_health)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        try:
            await site.start()
        except OSError as exc:
            await runner.cleanup()
            log.error("pixelagents: could not bind office server to %s:%s — %s", host, port, exc)
            return
        self._runner = runner
        log.info("pixelagents: office server listening on %s:%s/ws", host, port)

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status": "ok",
            "clients": len(self._clients),
            "agents": len(self._tracked_user_ids()),
            "assets": sorted(self._assets),
        })

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        socket = web.WebSocketResponse(heartbeat=30.0, max_msg_size=0)
        await socket.prepare(request)

        ticket = request.query.get("ticket", "")
        user_id = self._resolve_ticket(ticket) if ticket else None
        authorized = bool(user_id) and await self._check_auth(user_id)
        self._clients[socket] = authorized
        log.info(
            "pixelagents: office client connected (%s, %d total)",
            "editor" if authorized else "viewer",
            len(self._clients),
        )

        try:
            async for msg in socket:
                if msg.type != WSMsgType.TEXT:
                    if msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                        break
                    continue
                try:
                    await self._handle_client_message(socket, json.loads(msg.data))
                except Exception as exc:
                    log.error("pixelagents: client message error: %s", exc, exc_info=True)
        finally:
            self._clients.pop(socket, None)
            log.info("pixelagents: office client disconnected (%d left)", len(self._clients))
        return socket

    async def _handle_client_message(self, socket: web.WebSocketResponse, data: dict) -> None:
        msg_type = data.get("type")

        if msg_type in _EDITOR_MESSAGES and not self._clients.get(socket, False):
            log.info("pixelagents: dropped %s from an unauthorized office client", msg_type)
            return

        if msg_type == "authorize":
            # Sent out-of-band by the injected shim once its background
            # fetch of the `session` page resolves. The webview page itself
            # is public, so a socket starts as an unauthorized viewer and is
            # only upgraded here, after the ticket is independently validated
            # (mint + authz check), never trusted from the client alone.
            ticket = data.get("ticket")
            user_id = self._resolve_ticket(ticket) if ticket else None
            if user_id and await self._check_auth(user_id):
                self._clients[socket] = True
                log.info("pixelagents: office client upgraded to editor")
        elif msg_type == "webviewReady":
            await self._send_bootstrap(socket)
        elif msg_type == "saveLayout":
            layout = data.get("layout")
            if self._validate_layout(layout):
                await self.config.layout.set(layout)
                # Mirror the new layout to every other open tab. The saving
                # client already has it applied locally.
                for other in list(self._clients):
                    if other is not socket and not other.closed:
                        await self._send_to(other, {"type": "layoutLoaded", "layout": layout})
            else:
                log.warning("pixelagents: rejected an invalid layout from an office client")
        elif msg_type == "saveAgentSeats":
            await self._save_seats(data.get("seats") or {})
        elif msg_type == "requestDiagnostics":
            await self._send_to(socket, {"type": "agentDiagnostics", "agents": []})

    async def _save_seats(self, incoming: dict) -> None:
        seats = await self.config.seats() or {}
        for agent_id, value in incoming.items():
            if not isinstance(value, dict):
                continue
            record = dict(seats.get(str(agent_id)) or {})
            palette = value.get("palette")
            hue_shift = value.get("hueShift")
            seat_id = value.get("seatId")
            # Validate before storing: a hand-edited payload should not be able
            # to persist a palette index that renders as a missing sprite.
            if isinstance(palette, int) and 0 <= palette < max(
                len(self._assets.get("characters", [])), _PALETTE_COUNT
            ):
                record["palette"] = palette
            if isinstance(hue_shift, int) and 0 <= hue_shift <= 360:
                record["hueShift"] = hue_shift
            if isinstance(seat_id, str):
                record["seatId"] = seat_id
            seats[str(agent_id)] = record
        await self.config.seats.set(seats)

    async def _send_bootstrap(self, socket: web.WebSocketResponse) -> None:
        """Push the whole world to a freshly connected office client.

        Order matters and mirrors upstream's handleWebviewReady: capabilities
        first, then assets, then settings, and `layoutLoaded` LAST — the webview
        buffers `existingAgents` and only materializes characters when the
        layout arrives, so a layout-first bootstrap leaves an empty office.
        """
        await self._send_to(socket, {
            "type": "providerCapabilities",
            "readingTools": _READING_TOOLS,
            "subagentToolNames": _SUBAGENT_TOOL_NAMES,
        })

        if "characters" in self._assets:
            await self._send_to(socket, {
                "type": "characterSpritesLoaded",
                "characters": self._assets["characters"],
            })
        if "floors" in self._assets:
            await self._send_to(socket, {
                "type": "floorTilesLoaded",
                "sprites": self._assets["floors"],
            })
        if "walls" in self._assets:
            await self._send_to(socket, {"type": "wallTilesLoaded", "sets": self._assets["walls"]})
        if "carpets" in self._assets:
            await self._send_to(socket, {
                "type": "carpetTilesLoaded",
                "sets": self._assets["carpets"],
            })
        if "catalog" in self._assets and "furniture" in self._assets:
            await self._send_to(socket, {
                "type": "furnitureAssetsLoaded",
                "catalog": self._assets["catalog"],
                "sprites": self._assets["furniture"],
            })

        await self._send_to(socket, {
            "type": "settingsLoaded",
            "soundEnabled": False,
            "lastSeenVersion": "",
            "extensionVersion": "",
            "watchAllSessions": False,
            "alwaysShowLabels": False,
            "ghostHeadlessAgents": False,
            "hooksEnabled": False,
            "hooksInfoShown": True,
            "externalAssetDirectories": [],
            "showAreas": False,
        })
        await self._send_to(socket, {"type": "areaMappingsLoaded", "mappings": {}})

        seats = await self.config.seats() or {}
        await self._send_to(socket, self._existing_agents_message(seats))
        for uid in self._tracked_user_ids():
            aid = self._agent_id(uid)
            name = next(
                (n for (_, u), (_, n) in sorted(self._agents.items()) if u == uid),
                None,
            )
            if name:
                await self._send_to(socket, {
                    "type": "agentTeamInfo", "id": aid, "agentName": name,
                })

        await self._send_to(socket, {"type": "layoutLoaded", "layout": await self._current_layout()})

        # Activity bubbles reference characters that only exist after the
        # layout flush above, so replay them last.
        for (guild_id, user_id), label in self._presence_cache.items():
            if (guild_id, user_id) in self._agents:
                await self._send_to(socket, {
                    "type": "agentToolStart",
                    "id": self._agent_id(user_id),
                    "toolId": f"rp-{self._agent_id(user_id)}",
                    "toolName": "Activity",
                    "status": label,
                })

    # ------------------------------------------------------------------
    # Editor authorization
    # ------------------------------------------------------------------

    async def _check_auth(self, user_id: int) -> bool:
        return await self._can_edit_layout_user(user_id)

    async def _get_auth_member(
        self,
        guild: discord.Guild,
        user_id: int,
    ) -> Optional[discord.Member]:
        member = guild.get_member(user_id)
        if member is not None:
            return member

        fetch_member = getattr(guild, "fetch_member", None)
        if fetch_member is None:
            return None

        try:
            return await fetch_member(user_id)
        except Exception as exc:
            log.debug(
                "pixelagents: failed to fetch member %d in guild %s for auth check: %s",
                user_id,
                getattr(guild, "id", "unknown"),
                exc,
            )
            return None

    async def _can_edit_layout_user(self, user_id: int) -> bool:
        if user_id == 0:
            return False
        if await self.bot.is_owner(discord.Object(id=user_id)):
            return True
        role_id = await self.config.editor_role_id()
        for guild in self.bot.guilds:
            if not await self.config.guild(guild).enabled():
                continue
            member = await self._get_auth_member(guild, user_id)
            if member is None:
                continue
            permissions = getattr(member, "guild_permissions", None)
            if getattr(permissions, "administrator", False) is True:
                return True
            if role_id is not None and any(r.id == role_id for r in getattr(member, "roles", [])):
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
        # Discord never sends PRESENCE_UPDATE for the bot's own user, so member.status
        # stays at its default ("offline"). Treat the bot itself as always "online".
        if self.bot.user is not None and member.id == self.bot.user.id:
            return "online"
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
            palette, hue_shift = await self._assign_palette(agent_id)
            await self._send({"type": "agentClosed", "id": agent_id})
            await self._send({
                "type": "agentCreated",
                "id": agent_id,
                "folderName": folder,
                "palette": palette,
                "hueShift": hue_shift,
            })
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
            palette, hue_shift = await self._assign_palette(agent_id)
            await self._send({
                "type": "agentCreated",
                "id": agent_id,
                "folderName": folder,
                "palette": palette,
                "hueShift": hue_shift,
            })
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
            if cached is not None:
                # Same stable toolId — webview deduplicates on toolId so clear first
                await self._send({"type": "agentToolsClear", "id": agent_id})
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
        ws_host = await self.config.ws_host()
        ws_port = await self.config.ws_port()
        clear_delay = await self.config.message_tool_clear_delay()
        editor_role_id = await self.config.editor_role_id()
        broadcast_rp = await self.config.broadcast_rich_presence()
        broadcast_msg = await self.config.broadcast_messages()
        pixel_index_api_url = await self.config.pixel_index_api_url()
        pixel_index_web_url = await self.config.pixel_index_web_url()
        enabled = await self.config.guild(ctx.guild).enabled()
        include_bots = await self.config.guild(ctx.guild).include_bots()
        tracked = sum(1 for (gid, _) in self._agents if gid == ctx.guild.id)
        serving = self._runner is not None
        editors = sum(1 for authorized in self._clients.values() if authorized)

        def yn(value: bool) -> str:
            return "✅" if value else "🛑"

        embed = discord.Embed(title="Pixelagents Status", color=discord.Color.blurple())
        embed.add_field(name="Office Server", value=f"{ws_host}:{ws_port}/ws", inline=False)
        embed.add_field(name="Serving", value=yn(serving), inline=True)
        embed.add_field(
            name="Office Clients",
            value=f"{len(self._clients)} ({editors} editor)",
            inline=True,
        )
        embed.add_field(
            name="Assets",
            value="✅ loaded" if self._assets.get("characters") else "⚠️ missing",
            inline=True,
        )
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
        embed.add_field(name="Pixel Index API", value=pixel_index_api_url, inline=False)
        embed.add_field(name="Pixel Index Web", value=pixel_index_web_url, inline=False)

        await self._reply(ctx, embed=embed)

    @pixelagents_group.command(name="wsport")
    @commands.admin_or_permissions(administrator=True)
    @app_commands.describe(port="Port the office WebSocket server binds (default: 3210)")
    async def cmd_wsport(self, ctx: commands.Context, port: int) -> None:
        """Set the port the office WebSocket server listens on.

        Traefik routes `/ws` on the dashboard host to this port, so changing it
        means updating the Traefik label in redstack too.
        """
        if not 1 <= port <= 65535:
            await self._reply(ctx, "Port must be between 1 and 65535.")
            return
        await self.config.ws_port.set(port)
        await self._reply(
            ctx,
            f"Office server port set to `{port}`. Reload the cog to rebind, and update the "
            "Traefik `/ws` route to match.",
        )

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

    @pixelagents_group.group(name="index", invoke_without_command=True)
    @commands.admin_or_permissions(administrator=True)
    async def pixelagents_pixelindex_group(self, ctx: commands.Context) -> None:
        """Show the configured Pixel Index API endpoint and check its health."""
        if ctx.invoked_subcommand is not None:
            return
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
        api_url = await self.config.pixel_index_api_url()
        web_url = await self.config.pixel_index_web_url()
        ok, detail = await self._check_pixel_index_health(api_url)
        await self._reply(
            ctx,
            f"Pixel Index API: `{api_url}`\n"
            f"Pixel Index Web: `{web_url}`\n"
            f"Health check: {'✅ ' + detail if ok else '🛑 ' + detail}",
        )

    async def _check_pixel_index_health(self, url: str) -> Tuple[bool, str]:
        health_url = url.rstrip("/") + "/health"
        timeout = aiohttp.ClientTimeout(total=_PIXEL_INDEX_HEALTH_TIMEOUT)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(health_url) as resp:
                    if resp.status == 200:
                        return True, f"ok ({health_url})"
                    return False, f"HTTP {resp.status} ({health_url})"
        except Exception as exc:
            return False, f"unreachable ({exc})"

    @staticmethod
    def _clean_url(url: str) -> Optional[str]:
        clean = url.strip().rstrip("/")
        parsed = urlparse(clean)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return None
        return clean

    @pixelagents_pixelindex_group.command(name="set")
    @commands.admin_or_permissions(administrator=True)
    @app_commands.describe(url="Pixel Index API base URL, e.g. https://pixel-index-api.nntin.xyz")
    async def cmd_pixelindex_set(self, ctx: commands.Context, url: str) -> None:
        """Set the Pixel Index API endpoint (e.g. to switch between prod and staging)."""
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
        clean = self._clean_url(url)
        if clean is None:
            await self._reply(ctx, "Please provide a valid URL, e.g. `https://pixel-index-api.nntin.xyz`.")
            return
        await self.config.pixel_index_api_url.set(clean)
        ok, detail = await self._check_pixel_index_health(clean)
        await self._reply(
            ctx,
            f"Pixel Index API endpoint set to `{clean}`.\nHealth check: {'✅ ' + detail if ok else '🛑 ' + detail}",
        )

    @pixelagents_pixelindex_group.command(name="setweb")
    @commands.admin_or_permissions(administrator=True)
    @app_commands.describe(url="Pixel Index web frontend base URL, e.g. https://pixel-index.vercel.app")
    async def cmd_pixelindex_setweb(self, ctx: commands.Context, url: str) -> None:
        """Set the Pixel Index web frontend base URL, used for "View on site" links."""
        clean = self._clean_url(url)
        if clean is None:
            await self._reply(ctx, "Please provide a valid URL, e.g. `https://pixel-index.vercel.app`.")
            return
        await self.config.pixel_index_web_url.set(clean)
        await self._reply(ctx, f"Pixel Index web frontend set to `{clean}`.")

    # ------------------------------------------------------------------
    # Pixel Index HTTP client
    # ------------------------------------------------------------------

    async def _pixel_index_get(self, path: str, params: Optional[dict] = None) -> Tuple[bool, Any]:
        base = await self.config.pixel_index_api_url()
        url = f"{base.rstrip('/')}{path}"
        timeout = aiohttp.ClientTimeout(total=_PIXEL_INDEX_REQUEST_TIMEOUT)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        return False, f"Pixel Index API returned HTTP {resp.status}."
                    return True, await resp.json()
        except Exception as exc:
            return False, f"Could not reach the Pixel Index API: {exc}"

    async def _pixel_index_search(
        self,
        *,
        query: Optional[str],
        tag: Optional[str],
        sort: str,
        cursor: Optional[str] = None,
    ) -> Tuple[bool, Any]:
        params: Dict[str, Any] = {"sort": sort, "limit": _LAYOUT_SEARCH_PAGE_SIZE}
        if query:
            params["q"] = query
        if tag:
            params["tags"] = tag
        if cursor:
            params["cursor"] = cursor
        return await self._pixel_index_get("/api/v1/layouts", params)

    async def _pixel_index_layout(self, slug: str) -> Tuple[bool, Any]:
        return await self._pixel_index_get(f"/api/v1/layouts/{slug}")

    async def _load_pixel_index_layout(self, user_id: int, slug: str) -> Tuple[bool, str]:
        """Fetch a layout from Pixel Index and push it into the shared office."""
        if not await self._can_edit_layout_user(user_id):
            return False, "You are not authorized to manage Pixel Agents layouts."
        ok, data = await self._pixel_index_layout(slug)
        if not ok:
            return False, str(data)
        layout = data.get("layout")
        if not self._validate_layout(layout):
            return False, "That layout is invalid and cannot be loaded."
        await self.config.layout.set(layout)
        await self._send({"type": "layoutLoaded", "layout": layout})
        return True, f"Loaded `{data.get('title', slug)}` into the office."

    # ------------------------------------------------------------------
    # Pixel Index layout browsing
    # ------------------------------------------------------------------

    @pixelagents_group.group(name="layout", invoke_without_command=True)
    async def pixelagents_layout_group(self, ctx: commands.Context) -> None:
        """Browse shared office layouts from Pixel Index."""
        await ctx.send_help()

    @pixelagents_layout_group.command(name="search")
    @app_commands.describe(
        query="Text to search for in the title/description",
        tag="Only show layouts with this tag",
        sort="Sort order",
    )
    @app_commands.choices(
        sort=[app_commands.Choice(name=choice, value=choice) for choice in _LAYOUT_SORT_CHOICES]
    )
    async def cmd_layout_search(
        self,
        ctx: commands.Context,
        query: Optional[str] = None,
        tag: Optional[str] = None,
        sort: str = "newest",
    ) -> None:
        """Search Pixel Index for shared office layouts."""
        if ctx.interaction:
            await ctx.interaction.response.defer()
        if sort not in _LAYOUT_SORT_CHOICES:
            sort = "newest"
        ok, page = await self._pixel_index_search(query=query, tag=tag, sort=sort)
        if not ok:
            await self._send_public(ctx, str(page))
            return
        if not page.get("layouts"):
            await self._send_public(ctx, "No layouts found on Pixel Index.")
            return
        api_base = await self.config.pixel_index_api_url()
        web_base = await self.config.pixel_index_web_url()
        view = _LayoutBrowseView(
            self,
            ctx.author.id,
            query=query,
            tag=tag,
            sort=sort,
            pages=[page],
            page_index=0,
            api_base=api_base,
            web_base=web_base,
        )
        await self._send_public(ctx, view=view)

    @pixelagents_layout_group.command(name="view")
    @app_commands.describe(slug="Pixel Index layout slug")
    async def cmd_layout_view(self, ctx: commands.Context, slug: str) -> None:
        """Show a single Pixel Index layout by its slug."""
        if ctx.interaction:
            await ctx.interaction.response.defer()
        ok, detail = await self._pixel_index_layout(slug.strip().lower())
        if not ok:
            await self._send_public(ctx, str(detail))
            return
        api_base = await self.config.pixel_index_api_url()
        web_base = await self.config.pixel_index_web_url()
        view = _LayoutDetailView(self, ctx.author.id, detail, api_base=api_base, web_base=web_base)
        await self._send_public(ctx, view=view)


def _abs_url(base: str, path: str) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return base.rstrip("/") + "/" + path.lstrip("/")


class _LayoutBrowseView(discord.ui.LayoutView):
    """Paginated Pixel Index search results, rendered with Components V2."""

    def __init__(
        self,
        cog: "pixelagents",
        owner_id: int,
        *,
        query: Optional[str],
        tag: Optional[str],
        sort: str,
        pages: List[dict],
        page_index: int,
        api_base: str,
        web_base: str,
    ) -> None:
        super().__init__(timeout=180)
        self.cog = cog
        self.owner_id = owner_id
        self.query = query
        self.tag = tag
        self.sort = sort
        self.pages = pages
        self.page_index = page_index
        self.api_base = api_base
        self.web_base = web_base
        self._build()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the person who ran this search can use these controls.", ephemeral=True
            )
            return False
        return True

    def _build(self) -> None:
        page = self.pages[self.page_index]
        layouts = page.get("layouts") or []
        total = page.get("total", len(layouts))

        header_bits = [f"**Pixel Index layouts** — {total} match(es)"]
        if self.query:
            header_bits.append(f"matching `{self.query}`")
        if self.tag:
            header_bits.append(f"tagged `{self.tag}`")
        container = discord.ui.Container(discord.ui.TextDisplay(" ".join(header_bits)))

        for entry in layouts:
            author = entry.get("author") or {}
            stats = (
                f"{entry.get('visibleCols', '?')}×{entry.get('visibleRows', '?')} · "
                f"{entry.get('furniture', 0)} furniture · by {author.get('displayName') or 'unknown'}"
            )
            tags = entry.get("tags") or []
            lines = [f"**{entry.get('title') or entry['slug']}**", stats]
            if tags:
                lines.append("_" + ", ".join(tags) + "_")
            thumbnail_path = (entry.get("files") or {}).get("thumbnail")
            accessory = (
                discord.ui.Thumbnail(_abs_url(self.api_base, thumbnail_path))
                if thumbnail_path
                else discord.ui.Button(label="?", disabled=True)
            )
            container.add_item(
                discord.ui.Section(discord.ui.TextDisplay("\n".join(lines)), accessory=accessory)
            )

        select_row = discord.ui.ActionRow()
        select = discord.ui.Select(
            placeholder="View a layout…",
            options=[
                discord.SelectOption(
                    label=(entry.get("title") or entry["slug"])[:100],
                    value=entry["slug"],
                    description=(entry.get("description") or "")[:100] or None,
                )
                for entry in layouts
            ],
        )
        select.callback = self._make_select_callback(select)
        select_row.add_item(select)
        container.add_item(select_row)

        nav_row = discord.ui.ActionRow()
        prev_button = discord.ui.Button(
            label="◀ Prev", style=discord.ButtonStyle.secondary, disabled=self.page_index == 0
        )
        prev_button.callback = self._on_prev
        at_last_known_page = self.page_index >= len(self.pages) - 1
        next_button = discord.ui.Button(
            label="Next ▶",
            style=discord.ButtonStyle.secondary,
            disabled=at_last_known_page and page.get("nextCursor") is None,
        )
        next_button.callback = self._on_next
        nav_row.add_item(prev_button)
        nav_row.add_item(next_button)
        container.add_item(nav_row)

        self.add_item(container)

    def _make_select_callback(self, select: discord.ui.Select) -> Callable:
        async def on_select(interaction: discord.Interaction) -> None:
            slug = select.values[0]
            ok, detail = await self.cog._pixel_index_layout(slug)
            if not ok:
                await interaction.response.send_message(str(detail), ephemeral=True)
                return
            detail_view = _LayoutDetailView(
                self.cog,
                self.owner_id,
                detail,
                api_base=self.api_base,
                web_base=self.web_base,
                back=self,
            )
            await interaction.response.edit_message(view=detail_view)

        return on_select

    async def _on_prev(self, interaction: discord.Interaction) -> None:
        if self.page_index == 0:
            await interaction.response.defer()
            return
        new_view = _LayoutBrowseView(
            self.cog,
            self.owner_id,
            query=self.query,
            tag=self.tag,
            sort=self.sort,
            pages=self.pages,
            page_index=self.page_index - 1,
            api_base=self.api_base,
            web_base=self.web_base,
        )
        await interaction.response.edit_message(view=new_view)

    async def _on_next(self, interaction: discord.Interaction) -> None:
        if self.page_index + 1 < len(self.pages):
            new_pages = self.pages
            new_index = self.page_index + 1
        else:
            cursor = self.pages[self.page_index].get("nextCursor")
            if not cursor:
                await interaction.response.defer()
                return
            ok, page = await self.cog._pixel_index_search(
                query=self.query, tag=self.tag, sort=self.sort, cursor=cursor
            )
            if not ok:
                await interaction.response.send_message(str(page), ephemeral=True)
                return
            new_pages = self.pages + [page]
            new_index = len(new_pages) - 1
        new_view = _LayoutBrowseView(
            self.cog,
            self.owner_id,
            query=self.query,
            tag=self.tag,
            sort=self.sort,
            pages=new_pages,
            page_index=new_index,
            api_base=self.api_base,
            web_base=self.web_base,
        )
        await interaction.response.edit_message(view=new_view)


class _LayoutDetailView(discord.ui.LayoutView):
    """A single Pixel Index layout, rendered with Components V2."""

    def __init__(
        self,
        cog: "pixelagents",
        owner_id: int,
        detail: dict,
        *,
        api_base: str,
        web_base: str,
        back: Optional["_LayoutBrowseView"] = None,
    ) -> None:
        super().__init__(timeout=180)
        self.cog = cog
        self.owner_id = owner_id
        self.detail = detail
        self.api_base = api_base
        self.web_base = web_base
        self.back = back
        self._build()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the person who ran this command can use these controls.", ephemeral=True
            )
            return False
        return True

    def _build(self) -> None:
        d = self.detail
        author = d.get("author") or {}
        lines = [f"**{d.get('title') or d.get('slug')}**"]
        if d.get("description"):
            lines.append(d["description"])
        lines.append(
            f"{d.get('visibleCols', '?')}×{d.get('visibleRows', '?')} · "
            f"{d.get('furniture', 0)} furniture · {d.get('areas', 0)} areas · "
            f"{d.get('pets', 0)} pets · {d.get('seats', 0)} seats"
        )
        lines.append(f"By {author.get('displayName') or 'unknown'}")
        tags = d.get("tags") or []
        if tags:
            lines.append("Tags: " + ", ".join(tags))

        container = discord.ui.Container(discord.ui.TextDisplay("\n".join(lines)))

        preview_path = (d.get("files") or {}).get("preview")
        if preview_path:
            container.add_item(
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(_abs_url(self.api_base, preview_path))
                )
            )

        actions = discord.ui.ActionRow()
        download_path = (d.get("files") or {}).get("layout")
        if download_path:
            actions.add_item(
                discord.ui.Button(label="Download JSON", url=_abs_url(self.api_base, download_path))
            )
        slug = d.get("slug")
        if slug:
            actions.add_item(
                discord.ui.Button(
                    label="View on site", url=_abs_url(self.web_base, f"/layouts/{slug}")
                )
            )
        load_button = discord.ui.Button(label="Load into office", style=discord.ButtonStyle.primary)
        load_button.callback = self._on_load
        actions.add_item(load_button)
        if self.back is not None:
            back_button = discord.ui.Button(label="◀ Back", style=discord.ButtonStyle.secondary)
            back_button.callback = self._on_back
            actions.add_item(back_button)
        container.add_item(actions)

        self.add_item(container)

    async def _on_load(self, interaction: discord.Interaction) -> None:
        slug = self.detail.get("slug")
        ok, message = await self.cog._load_pixel_index_layout(interaction.user.id, slug)
        await interaction.response.send_message(message, ephemeral=True)

    async def _on_back(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(view=self.back)

