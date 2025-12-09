# SPDX-FileCopyrightText: 2025 cswimr <copyright@csw.im>
# SPDX-License-Identifier: MPL-2.0

import concurrent.futures
import py_compile
from asyncio import run_coroutine_threadsafe
from functools import partial
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated, Any, ClassVar, Generator, List, Sequence

import discord
from discord.utils import MISSING
from red_commons.logging import RedTraceLogger, getLogger
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.core_commands import CoreLogic
from tidegear import Cog
from tidegear import chat_formatting as cf
from tidegear.config import BaseConfigSchema, ConfigMeta, GlobalConfigOption
from typing_extensions import override
from watchdog.events import FileSystemEvent, FileSystemMovedEvent, RegexMatchingEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver


class HotReloadSchema(BaseConfigSchema):
    version: ClassVar[int] = 1

    notify_channel: Annotated[GlobalConfigOption[int | None], ConfigMeta(default=None)]
    compile_before_reload: Annotated[GlobalConfigOption[bool], ConfigMeta(default=False)]


class HotReload(Cog):
    """Automatically reload cogs in local cog paths on file change."""

    def __init__(self, bot: Red) -> None:
        super().__init__(bot)
        self.config: HotReloadSchema = MISSING
        self.observers: List[BaseObserver] = []
        watchdog_loggers = [getLogger(name="watchdog.observers.inotify_buffer")]
        for watchdog_logger in watchdog_loggers:
            watchdog_logger.setLevel("INFO")  # SHUT UP!!!!

    @override
    async def cog_load(self) -> None:
        """Start the observer when the cog is loaded."""
        await super().cog_load()
        self.config = await HotReloadSchema.init(cog_name=self.qualified_name, identifier=294518358420750336, logger=self.logger)
        _ = self.bot.loop.create_task(self.start_observer())

    @override
    async def cog_unload(self) -> None:
        """Stop the observer when the cog is unloaded."""
        for observer in self.observers:
            observer.stop()
            observer.join()
            self.logger.info("Stopped observer. No longer watching for file changes.")
        await super().cog_unload()

    async def get_paths(self) -> Generator[Path, None, None]:
        """Retrieve user defined paths."""
        cog_manager = self.bot._cog_mgr  # noqa: SLF001 # We have to use this private method because there is no public API to get user defined paths
        cog_paths = await cog_manager.user_defined_paths()
        return (Path(path) for path in cog_paths)

    async def start_observer(self) -> None:
        """Start the observer to watch for file changes."""
        self.observers.append(Observer())
        paths = await self.get_paths()
        is_first = True
        for observer in self.observers:
            if not is_first:
                observer.stop()
                observer.join()
                self.logger.debug("Stopped hanging observer.")
                continue
            for path in paths:
                if not path.exists():
                    self.logger.warning("Path %s does not exist. Skipping.", path)
                    continue
                self.logger.debug("Adding observer schedule for path %s.", path)
                observer.schedule(event_handler=HotReloadHandler(cog=self, path=path), path=str(path), recursive=True)
            observer.start()
            self.logger.info("Started observer. Watching for file changes.")
            is_first = False

    @commands.group(name="hotreload")
    async def hotreload_group(self, ctx: commands.Context) -> None:
        """HotReload configuration commands."""
        _ = ctx

    @commands.is_owner()
    @hotreload_group.command(name="notifychannel")
    async def hotreload_notifychannel(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        """Set the channel to send notifications to."""
        await self.config.notify_channel.set(channel.id)
        await ctx.send(f"Notifications will be sent to {channel.mention}.")

    @commands.is_owner()
    @hotreload_group.command(name="compile")
    async def hotreload_compile(self, ctx: commands.Context, compile_before_reload: bool) -> None:
        """Set whether to compile modified files before reloading."""
        await self.config.compile_before_reload.set(compile_before_reload)
        await ctx.send(f"I {'will' if compile_before_reload else 'will not'} compile modified files before hotreloading cogs.")

    @commands.is_owner()
    @hotreload_group.command(name="list")
    async def hotreload_list(self, ctx: commands.Context) -> None:
        """List the currently active observers."""
        if not self.observers:
            await ctx.send("No observers are currently active.")
            return
        await ctx.send(
            (
                f"Currently active observers (If there are more than one of these, report an issue): "
                f"{cf.box(cf.humanize_list([str(o) for o in self.observers], style='unit'))}"
            )
        )


class HotReloadHandler(RegexMatchingEventHandler):
    """Handler for file changes."""

    def __init__(self, cog: HotReload, path: Path) -> None:
        super().__init__(regexes=[r".*\.py$"])
        self.cog: HotReload = cog
        self.path: Path = path
        self.logger: RedTraceLogger = getLogger(name="red.SeaCogs.HotReload.Observer")
        self.debounce: bool = False

    def _on_reload_done(self, event_type, fut: concurrent.futures.Future) -> None:
        _ = fut

        def _reset():
            self.debounce = False
            self.logger.verbose(
                "Debouncing disabled, event '%s' has been handled",
                event_type,
            )

        self.cog.bot.loop.call_soon_threadsafe(_reset)

    @override
    def on_any_event(self, event: FileSystemEvent) -> None:
        """Handle filesystem events."""
        if event.is_directory or self.debounce:
            return

        allowed_events = ("moved", "deleted", "created", "modified")
        if event.event_type not in allowed_events:
            return

        blacklisted_package_names: list[str] = [".venv", "venv", ".data"]
        required_files: list[str] = ["info.json"]

        relative_src_path = Path(str(event.src_path)).relative_to(self.path)

        src_package_name = relative_src_path.parts[0]
        if src_package_name in blacklisted_package_names:
            return

        if not any((self.path / src_package_name / name).exists() for name in required_files):
            return

        cogs_to_reload = [src_package_name]

        self.logger.verbose("Starting cog reload process, enabling debouncing during event '%s'", event.event_type)
        self.debounce = True

        if isinstance(event, FileSystemMovedEvent):
            dest = f" to {event.dest_path}"
            relative_dest_path = Path(str(event.dest_path)).relative_to(self.path)
            dest_package_name = relative_dest_path.parts[0]
            if dest_package_name != src_package_name:
                cogs_to_reload.append(dest_package_name)
        else:
            dest = ""

        self.logger.info("File %s has been %s%s.", event.src_path, event.event_type, dest)

        future = run_coroutine_threadsafe(
            coro=self.reload_cogs(
                cog_names=cogs_to_reload,
                paths=[Path(str(p)) for p in (event.src_path, getattr(event, "dest_path", None)) if p],
            ),
            loop=self.cog.bot.loop,
        )
        future.add_done_callback(partial(self._on_reload_done, event.event_type))

    async def reload_cogs(self, cog_names: Sequence[str], paths: Sequence[Path]) -> None:
        """Reload modified cogs."""
        if not await self.compile_modified_files(cog_names, paths):
            return

        core_logic = CoreLogic(bot=self.cog.bot)
        self.logger.info("Attempting to reload cogs: %s", cf.humanize_list(cog_names, style="unit"))
        # We have to use this private method because there is no public API to reload other cogs
        reloaded_cogs: Any = await core_logic._reload(pkg_names=cog_names)  # noqa: SLF001

        text: list[str] = []
        loaded: list[str] = [cf.inline(text=cog) for cog in reloaded_cogs.get("loaded_packages", [])]
        failed: list[str] = [cf.inline(text=cog) for cog in reloaded_cogs.get("failed_packages", [])]
        invalid_pkg_names: list[str] = [cf.inline(text=cog) for cog in reloaded_cogs.get("invalid_pkg_names", [])]
        not_found: list[str] = [cf.inline(text=cog) for cog in reloaded_cogs.get("notfound_packages", [])]
        failed_with_reason: dict[str, str] = reloaded_cogs.get("failed_with_reason_packages", {})

        if loaded:
            text.append(f"Reloaded cogs: {cf.humanize_list(items=loaded, style='unit')}")
        if failed:
            text.append(f"Failed to reload cogs: {cf.humanize_list(items=failed, style='unit')}")
        if invalid_pkg_names:
            text.append(f"Cogs skipped due to invalid package names: {cf.humanize_list(items=invalid_pkg_names, style='unit')}")
        if not_found:
            text.append(f"Cogs could not be found: {cf.humanize_list(items=not_found, style='unit')}")
        for k, v in failed_with_reason.items():
            text.append(f"Cog {k} could not be reloaded: {cf.inline(text=v)}")

        msg = "\n".join(text)
        self.logger.info(msg.replace("`", "'"))
        if channel_id := await self.cog.config.notify_channel.get():
            channel = self.cog.bot.get_channel(channel_id)
            if channel and isinstance(channel, discord.TextChannel):
                await channel.send(msg)

    async def compile_modified_files(self, cog_names: Sequence[str], paths: Sequence[Path]) -> bool:
        """Compile modified files to ensure they are valid Python files."""
        if not await self.cog.config.compile_before_reload.get():
            return True

        for path in paths:
            if not path.exists() or path.suffix != ".py":
                self.logger.debug("Path %s does not exist or does not point to a Python file. Skipping compilation step.", path)
                continue

            try:
                with NamedTemporaryFile() as temp_file:
                    self.logger.debug("Attempting to compile %s", path)
                    py_compile.compile(file=str(path), cfile=temp_file.name, doraise=True)
                    self.logger.debug("Successfully compiled %s", path)

            except py_compile.PyCompileError as e:
                e.__suppress_context__ = True
                self.logger.exception("%s failed to compile. Not reloading cogs %s.", path, cf.humanize_list(cog_names, style="unit"))
                return False
            except OSError:
                self.logger.exception("Failed to create tempfile for compilation step. Skipping.")
        return True
