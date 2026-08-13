from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import mimetypes
from pathlib import Path
import random
import re
import secrets
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from aiohttp import WSMsgType, web
import discord
from discord import app_commands
from redbot.core import Config, commands
from redbot.core.bot import Red

log = logging.getLogger("red.d_cogs.pixelagents")

_VISIBLE_STATUSES = {"online", "idle", "dnd"}
_MAX_LAYOUTS_PER_USER = 20
_MAX_LAYOUT_BYTES = 1024 * 1024
_LAYOUT_NAME_RE = re.compile(r"^[A-Za-z0-9 _.-]{1,64}$")
_WEBVIEW_CACHE_CONTROL = "public, max-age=3600"

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

        layout = await self._current_layout()
        if not self._validate_layout(layout):
            await self._reply(ctx, "There is no valid office layout to save yet.")
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

        await self.config.layout.set(layout)
        # Push it to every open office tab so the change is live immediately.
        await self._send({"type": "layoutLoaded", "layout": layout})
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
