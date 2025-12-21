from __future__ import annotations

import asyncio
import importlib
import logging
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

import discord
from discord.utils import escape_markdown
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.config import Config
from redbot.core.core_commands import CoreLogic
from watchdog.events import FileSystemEvent, FileSystemMovedEvent, RegexMatchingEventHandler
from watchdog.observers import Observer


log = logging.getLogger("red.d_cogs.modreload")


BLACKLISTED_PARTS = {".venv", "venv", "__pycache__", ".git", ".data"}
ALLOWED_EVENTS = {"moved", "deleted", "created", "modified"}


@dataclass(slots=True)
class ModuleSettings:
    name: str
    path: Path
    editable: bool
    pip_args: str
    reload_dependent_cogs: list[str]
    enabled: bool


class ModuleReloadHandler(RegexMatchingEventHandler):
    """Watchdog handler for a tracked module."""

    def __init__(self, cog: "ModReload", settings: ModuleSettings, debounce_seconds: float) -> None:
        super().__init__(regexes=[r".*\.py$"])
        self.cog = cog
        self.settings = settings
        self.debounce_seconds = debounce_seconds
        self._debouncing: bool = False

    def _reset_debounce(self) -> None:
        self._debouncing = False
        log.debug("Debounce lifted for module '%s'", self.settings.name)

    def on_any_event(self, event: FileSystemEvent) -> None:  # noqa: D401 - watchdog signature
        if event.is_directory or self._debouncing:
            return

        if event.event_type not in ALLOWED_EVENTS:
            return

        paths: list[Path] = []
        try:
            src_path = Path(event.src_path)
            paths.append(src_path)
            relative = src_path.relative_to(self.settings.path)
        except ValueError:
            return

        if any(part in BLACKLISTED_PARTS for part in relative.parts):
            return

        if isinstance(event, FileSystemMovedEvent) and event.dest_path:
            paths.append(Path(event.dest_path))

        self._debouncing = True
        log.info(
            "Detected %s in %s (%s). Debouncing for %.1fs.",
            event.event_type,
            self.settings.name,
            relative,
            self.debounce_seconds,
        )

        fut = asyncio.run_coroutine_threadsafe(
            self.cog.handle_module_change(settings=self.settings, event_paths=paths),
            self.cog.bot.loop,
        )
        fut.add_done_callback(lambda _: self.cog.bot.loop.call_later(self.debounce_seconds, self._reset_debounce))


class ModReload(commands.Cog):
    """
    Automatically reinstall and reload tracked Python modules on file changes.
    """

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=257263092, force_registration=True)
        self.config.register_global(
            modules={},
            notify_channel=None,
            debounce_seconds=1.0,
            compile_before_reload=False,
            auto_pip_enabled=False,
            enabled=True,
        )

        self.observer: Optional[Observer] = None
        self.handlers: list[ModuleReloadHandler] = []

    async def cog_load(self) -> None:
        await super().cog_load()
        await self._restart_observer()

    async def cog_unload(self) -> None:
        await self._stop_observer()
        await super().cog_unload()

    #
    # Commands
    #
    @commands.group(name="modreload", invoke_without_command=True)
    async def modreload_group(self, ctx: commands.Context) -> None:
        """Manage automatic reloading of non-cog modules."""
        await ctx.send_help(ctx.command)

    @commands.is_owner()
    @modreload_group.command(name="add")
    async def add_module(
        self,
        ctx: commands.Context,
        name: str,
        path: str,
        editable: bool = True,
        *,
        pip_args: str = "",
    ) -> None:
        """Track a module for automatic reloads."""
        root = Path(path).expanduser().resolve()
        if not root.exists():
            await ctx.send(f"Path `{escape_markdown(str(root))}` does not exist.")
            return

        modules = await self.config.modules()
        modules[name] = {
            "path": str(root),
            "editable": bool(editable),
            "pip_args": pip_args,
            "reload_dependent_cogs": [],
            "enabled": True,
        }
        await self.config.modules.set(modules)
        await ctx.send(f"Registered module `{escape_markdown(name)}` at `{escape_markdown(str(root))}`.")
        await self._restart_observer()

    @commands.is_owner()
    @modreload_group.command(name="remove")
    async def remove_module(self, ctx: commands.Context, name: str) -> None:
        """Stop tracking a module."""
        modules = await self.config.modules()
        if name not in modules:
            await ctx.send(f"Module `{escape_markdown(name)}` is not tracked.")
            return

        modules.pop(name)
        await self.config.modules.set(modules)
        await ctx.send(f"Removed module `{escape_markdown(name)}`.")
        await self._restart_observer()

    @commands.is_owner()
    @modreload_group.command(name="list")
    async def list_modules(self, ctx: commands.Context) -> None:
        """List tracked modules and their status."""
        modules = await self.config.modules()
        if not modules:
            await ctx.send("No modules are currently tracked.")
            return

        lines = []
        for name, data in modules.items():
            root = data.get("path", "?")
            enabled = data.get("enabled", True)
            editable = data.get("editable", True)
            deps = data.get("reload_dependent_cogs", [])
            parts = [
                f"path={escape_markdown(root)}",
                "enabled" if enabled else "disabled",
                "editable" if editable else "non-editable",
            ]
            if deps:
                parts.append(f"dependents={', '.join(map(escape_markdown, deps))}")
            lines.append(f"- `{escape_markdown(name)}`: " + "; ".join(parts))
        await ctx.send("\n".join(lines))

    @commands.is_owner()
    @modreload_group.command(name="enable")
    async def enable_global(self, ctx: commands.Context) -> None:
        """Enable modreload globally."""
        await self.config.enabled.set(True)
        await ctx.send("ModReload enabled.")
        await self._restart_observer()

    @commands.is_owner()
    @modreload_group.command(name="disable")
    async def disable_global(self, ctx: commands.Context) -> None:
        """Disable modreload globally."""
        await self.config.enabled.set(False)
        await ctx.send("ModReload disabled.")
        await self._stop_observer()

    @commands.is_owner()
    @modreload_group.command(name="togglepip")
    async def toggle_pip(self, ctx: commands.Context, allow: bool) -> None:
        """Allow or block automatic pip installs."""
        await self.config.auto_pip_enabled.set(bool(allow))
        await ctx.send(f"Auto pip installs {'enabled' if allow else 'disabled'}.")

    @commands.is_owner()
    @modreload_group.command(name="notifychannel")
    async def set_notify_channel(self, ctx: commands.Context, channel: discord.TextChannel | None) -> None:
        """Set or clear the channel to send reload notifications."""
        await self.config.notify_channel.set(channel.id if channel else None)
        if channel:
            await ctx.send(f"Notifications will be sent to {channel.mention}.")
        else:
            await ctx.send("Notification channel cleared.")

    @commands.is_owner()
    @modreload_group.command(name="compile")
    async def set_compile(self, ctx: commands.Context, compile_before_reload: bool) -> None:
        """Toggle compiling modified files before reinstalling."""
        await self.config.compile_before_reload.set(bool(compile_before_reload))
        await ctx.send(
            f"I {'will' if compile_before_reload else 'will not'} compile modified files before reinstalling modules."
        )

    @commands.is_owner()
    @modreload_group.command(name="debounce")
    async def set_debounce(self, ctx: commands.Context, seconds: float) -> None:
        """Set debounce duration between reload attempts per module."""
        if seconds < 0:
            await ctx.send("Debounce must be zero or positive.")
            return
        await self.config.debounce_seconds.set(float(seconds))
        await ctx.send(f"Debounce set to {seconds:.2f}s.")
        await self._restart_observer()

    @commands.is_owner()
    @modreload_group.command(name="enablemodule")
    async def enable_module(self, ctx: commands.Context, name: str) -> None:
        """Enable reloads for a single module."""
        if not await self._set_module_flag(name, True):
            await ctx.send(f"Module `{escape_markdown(name)}` is not tracked.")
            return
        await ctx.send(f"Module `{escape_markdown(name)}` enabled.")
        await self._restart_observer()

    @commands.is_owner()
    @modreload_group.command(name="disablemodule")
    async def disable_module(self, ctx: commands.Context, name: str) -> None:
        """Disable reloads for a single module."""
        if not await self._set_module_flag(name, False):
            await ctx.send(f"Module `{escape_markdown(name)}` is not tracked.")
            return
        await ctx.send(f"Module `{escape_markdown(name)}` disabled.")
        await self._restart_observer()

    @commands.is_owner()
    @modreload_group.command(name="dependents")
    async def set_dependents(self, ctx: commands.Context, name: str, *cog_names: str) -> None:
        """Define cogs to reload after a module update."""
        modules = await self.config.modules()
        if name not in modules:
            await ctx.send(f"Module `{escape_markdown(name)}` is not tracked.")
            return
        modules[name]["reload_dependent_cogs"] = list(cog_names)
        await self.config.modules.set(modules)
        await ctx.send(
            f"Dependents for `{escape_markdown(name)}` set to: {', '.join(map(escape_markdown, cog_names)) or 'none'}."
        )

    @commands.is_owner()
    @modreload_group.command(name="reload")
    async def reload_module_command(self, ctx: commands.Context, name: str) -> None:
        """Manually trigger a module reinstall/reload."""
        settings = await self._get_module_settings(name)
        if not settings:
            await ctx.send(f"Module `{escape_markdown(name)}` is not tracked or disabled.")
            return
        await ctx.send(f"Triggering reload for `{escape_markdown(name)}`.")
        await self.handle_module_change(settings=settings, event_paths=[settings.path])

    #
    # Internal helpers
    #
    async def _set_module_flag(self, name: str, enabled: bool) -> bool:
        modules = await self.config.modules()
        if name not in modules:
            return False
        modules[name]["enabled"] = enabled
        await self.config.modules.set(modules)
        return True

    async def _get_module_settings(self, name: str) -> Optional[ModuleSettings]:
        modules = await self.config.modules()
        data = modules.get(name)
        if not data or not data.get("enabled", True):
            return None
        root = Path(data["path"])
        return ModuleSettings(
            name=name,
            path=root,
            editable=bool(data.get("editable", True)),
            pip_args=data.get("pip_args", ""),
            reload_dependent_cogs=list(data.get("reload_dependent_cogs", [])),
            enabled=bool(data.get("enabled", True)),
        )

    async def _iter_settings(self) -> Iterable[ModuleSettings]:
        modules = await self.config.modules()
        for name, data in modules.items():
            if not data.get("enabled", True):
                continue
            path = Path(data.get("path", ""))
            if not path.exists():
                log.warning("Module %s path %s does not exist; skipping schedule.", name, path)
                continue
            yield ModuleSettings(
                name=name,
                path=path,
                editable=bool(data.get("editable", True)),
                pip_args=data.get("pip_args", ""),
                reload_dependent_cogs=list(data.get("reload_dependent_cogs", [])),
                enabled=bool(data.get("enabled", True)),
            )

    async def _restart_observer(self) -> None:
        await self._stop_observer()
        if not await self.config.enabled():
            return

        debounce_seconds = await self.config.debounce_seconds()
        observer = Observer()
        handlers: list[ModuleReloadHandler] = []
        async for settings in self._iter_settings():
            handler = ModuleReloadHandler(self, settings, debounce_seconds=debounce_seconds)
            handlers.append(handler)
            observer.schedule(handler, str(settings.path), recursive=True)
            log.info("Watching %s for module %s", settings.path, settings.name)

        if handlers:
            observer.start()
            self.observer = observer
            self.handlers = handlers
            log.info("ModReload observer started with %s handler(s).", len(handlers))

    async def _stop_observer(self) -> None:
        if self.observer:
            self.observer.stop()
            self.observer.join()
            log.info("ModReload observer stopped.")
        self.observer = None
        self.handlers = []

    async def handle_module_change(self, settings: ModuleSettings, event_paths: Sequence[Path]) -> None:
        if not await self.config.enabled():
            return
        if not settings.enabled:
            return

        if not await self._compile_if_requested(event_paths):
            await self._notify(f"Compilation failed for `{settings.name}`; reload aborted.")
            return

        if not await self.config.auto_pip_enabled():
            msg = f"Auto pip is disabled; skipped reinstall for `{settings.name}`."
            log.warning(msg)
            await self._notify(msg)
            return

        pip_result = await self._run_pip_install(settings)
        await self._notify(pip_result)

        if "failed" in pip_result.lower():
            return

        reload_message = await self._reload_imports(settings)
        if reload_message:
            await self._notify(reload_message)

        if settings.reload_dependent_cogs:
            await self._reload_cogs(settings.reload_dependent_cogs)

    async def _compile_if_requested(self, paths: Sequence[Path]) -> bool:
        if not await self.config.compile_before_reload():
            return True

        for path in paths:
            if not path.exists() or path.suffix != ".py":
                continue
            try:
                await asyncio.to_thread(self._compile_path, path)
            except Exception:
                log.exception("Failed to compile %s", path)
                return False
        return True

    @staticmethod
    def _compile_path(path: Path) -> None:
        import py_compile
        import tempfile

        with tempfile.NamedTemporaryFile() as temp_file:
            py_compile.compile(file=str(path), cfile=temp_file.name, doraise=True)

    async def _run_pip_install(self, settings: ModuleSettings) -> str:
        python = sys.executable
        args: List[str] = [python, "-m", "pip", "install"]
        if settings.editable:
            args.append("-e")
        args.append(str(settings.path))
        args.append("--no-deps")

        extra = shlex.split(settings.pip_args) if settings.pip_args else []
        args.extend(extra)

        log.info("Installing %s with: %s", settings.name, " ".join(args))

        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
        except FileNotFoundError:
            log.exception("Python executable not found while installing %s", settings.name)
            return f"Failed to reinstall `{settings.name}`: could not start pip."

        out = stdout.decode().strip()
        err = stderr.decode().strip()
        if process.returncode != 0:
            log.error("Pip install failed for %s: %s", settings.name, err or out)
            return f"Failed to reinstall `{settings.name}` (exit {process.returncode})."

        if out:
            log.debug("Pip output for %s: %s", settings.name, out)
        return f"Reinstalled `{settings.name}` successfully."

    async def _reload_imports(self, settings: ModuleSettings) -> str:
        name = settings.name
        importlib.invalidate_caches()
        dropped = [mod for mod in list(sys.modules) if mod == name or mod.startswith(f"{name}.")]
        for mod in dropped:
            sys.modules.pop(mod, None)
        try:
            importlib.import_module(name)
        except Exception:
            log.exception("Failed to reload module %s", name)
            return f"Failed to reload imports for `{name}`."
        return f"Reloaded imports for `{name}` (dropped {len(dropped)} module(s))."

    async def _reload_cogs(self, cog_names: Sequence[str]) -> None:
        logic = CoreLogic(bot=self.bot)
        log.info("Reloading dependent cogs: %s", ", ".join(cog_names))
        try:
            result = await logic._reload(pkg_names=list(cog_names))  # noqa: SLF001
        except Exception:
            log.exception("Failed to reload dependent cogs: %s", cog_names)
            await self._notify("Failed to reload dependent cogs.")
            return

        loaded = result.get("loaded_packages", [])
        failed = result.get("failed_packages", [])

        parts = []
        if loaded:
            parts.append(f"Reloaded dependent cogs: {', '.join(loaded)}.")
        if failed:
            parts.append(f"Failed to reload: {', '.join(failed)}.")
        if parts:
            await self._notify(" ".join(parts))

    async def _notify(self, message: str) -> None:
        log.info(message.replace("`", "'"))
        channel_id = await self.config.notify_channel()
        if not channel_id:
            return
        channel = self.bot.get_channel(channel_id)
        if channel and isinstance(channel, discord.TextChannel):
            try:
                await channel.send(message)
            except Exception:
                log.exception("Failed to send notification to channel %s", channel_id)
