# ModReload Implementation Plan

ModReload keeps non-cog Python modules (e.g., `cogchain`) in sync with the running bot by reinstalling and reloading them when source files change.

## Decisions
- Default install mode: editable to match local dev.
- Pip flag: do **not** add `--no-build-isolation` by default.
- Dependent cogs: manual list per module (no auto scan for now).
- Tracked assets: `.py` files only for now; keep extension hookable later.

## Implementation outline
1) **Configuration schema**
   - Global list of tracked modules with fields: `name`, `path`, `editable`, `pip_args`, `reload_dependent_cogs`, `enabled`.
   - Global options: notify channel, debounce seconds, compile-before-reload toggle, allow/deny auto-pip execution (default off), max concurrent reloads (start with 1).

2) **Commands (owner)**
   - `modreload add/remove/list` to manage tracked modules.
   - Global toggles: `enable/disable`, `togglepip`, `notifychannel`, `compile`, `debounce`.
   - Per-module toggles: `enablemodule/disablemodule`, `dependents` to set cogs reloaded after module updates, `reload` to trigger manually.

3) **Watcher setup**
   - On cog load, start a watchdog observer; schedule per tracked module path (recursive) filtering `.py` files.
   - Skip missing paths; log and notify.
   - Debounce per module to avoid thrashing.

4) **Event handling flow**
   - Accept `created/modified/moved/deleted` for `.py`; ignore blacklisted directories (`.venv`, `venv`, `__pycache__`, `.git`, `.data`).
   - Optional compile check via `py_compile`; abort reload on failure.
   - Reinstall command: `python -m pip install [-e] <path> --no-deps [pip_args]`; no `--no-build-isolation` default.
   - Invalidate import caches, drop `sys.modules` entries for the package, re-import root module.
   - Reload dependent cogs via `CoreLogic._reload` after successful reinstall.

5) **Safety and concurrency**
   - One in-flight reload per module via debounce.
   - Pip execution gated by config flag; surface warning if disabled.
   - Add timeouts/logging for pip subprocess; report failures cleanly.

6) **Logging and notification**
   - Info-level for installs/reloads; warnings for skips; trace/debug for event filtering.
   - Optional notify channel for summary messages (event path, install result, cogs reloaded, compile errors).

7) **Persistence and recovery**
   - Store module configs in Red Config; rebuild watcher schedules on cog load.
   - On observer failure, log and allow manual restart via reload command.

8) **Testing plan**
   - Unit: config validation, path matching/blacklist logic, command behaviors, import reload helper.
   - Manual: edit tracked module -> pip runs -> module reload -> dependent cog reload; test compile failure and disabled auto-pip modes; moved/renamed paths handling.
