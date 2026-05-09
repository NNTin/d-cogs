# Pixelagents Implementation Plan

## Goal

Create a Red DiscordBot cog named `pixelagents` that mirrors Discord guild
presence into the Pixelpipes webview through the existing pixel-agents-node-red
Agent Control API.

The cog does not modify Pixelpipes or the Node-RED API. It sends only accepted
Agent Control API fields:

- `agentKey`: unique key for each Discord guild member.
- `agentName`: the Discord member display name/nickname.
- `folderName`: the Discord presence label: `online`, `idle`, or `dnd`.

Offline and invisible members are despawned and therefore not shown in the
webview.

## Implementation Checklist

- [x] Create the Red cog package in `pixelagents/`.
- [x] Add `__init__.py`, `pixelagents.py`, and `info.json`.
- [x] Register global config:
  - [x] `base_url`, defaulting to `https://pp.lair.nntin.xyz/`.
  - [x] `api_key`, stored as a secret value and never echoed in full.
  - [x] HTTP timeout, defaulting to 10 seconds.
- [x] Register guild config:
  - [x] `enabled`, defaulting to `false`.
  - [x] `include_bots`, defaulting to `true`.
- [x] Add admin-only guild commands:
  - [x] `[p]pixelagents status`
  - [x] `[p]pixelagents baseurl <url>`
  - [x] `[p]pixelagents key <token>`
  - [x] `[p]pixelagents enable`
  - [x] `[p]pixelagents disable`
  - [x] `[p]pixelagents includebots <true|false>`
  - [x] `[p]pixelagents sync`
  - [x] `[p]pixelagents despawnall`
- [x] Implement an async HTTP client around the Agent Control API:
  - [x] `POST /api/agents`
  - [x] `PATCH /api/agents/{agentKey}/state`
  - [x] `DELETE /api/agents/{agentKey}`
  - [x] `GET /api/agents` for status/debug output.
- [x] Send `Authorization: Bearer <api_key>` and `Content-Type: application/json`.
- [x] Normalize `base_url` so trailing slashes do not produce malformed URLs.
- [x] Build stable agent keys as `discord:<guild_id>:<user_id>`.
- [x] Map Discord statuses:
  - [x] `online` -> spawn/update with `folderName: "online"`.
  - [x] `idle` -> spawn/update with `folderName: "idle"`.
  - [x] `dnd` -> spawn/update with `folderName: "dnd"`.
  - [x] `offline` and `invisible` -> despawn.
- [x] Use `member.display_name` for `agentName`.
- [x] Include bots by default, including the bot user, unless `include_bots` is
      disabled for the guild.
- [x] On cog load, do not auto-enable guilds; only reconcile guilds where
      `enabled` is already true.
- [x] On `[p]pixelagents enable`, mark the guild enabled and run a full sync.
- [x] On `[p]pixelagents disable`, mark the guild disabled and despawn tracked
      agents for that guild.
- [x] On `[p]pixelagents sync`, reconcile all eligible members in the current
      guild.
- [x] On `on_member_update`, react only when presence or display name changes
      for an enabled guild.
- [x] On `on_member_join`, spawn the member if the guild is enabled and the
      member is not offline.
- [x] On `on_member_remove`, despawn that member.
- [x] Treat duplicate spawn (`409`) as recoverable by patching mutable state.
- [x] Because the current PATCH endpoint does not accept `folderName`, refresh
      a member by delete-then-spawn when the status label changes among
      `online`, `idle`, and `dnd`.
- [x] Log HTTP failures with enough context to debug while never logging the
      bearer token.

## Payloads

Spawn or refresh a visible Discord member:

```json
{
  "agentKey": "discord:<guild_id>:<user_id>",
  "agentName": "<member display name>",
  "folderName": "online",
  "selected": false
}
```

Patch display-name-only changes:

```json
{
  "agentName": "<member display name>"
}
```

Despawn offline, invisible, removed, excluded, or disabled members:

```text
DELETE /api/agents/discord:<guild_id>:<user_id>
```

## Validation Checklist

- [x] `python -m compileall pixelagents` passes.
- [x] `info.json` validates against Red cog metadata expectations.
- [x] Unit tests cover agent key generation and Discord status mapping.
- [x] Unit tests cover bot inclusion and exclusion behavior.
- [x] Unit tests mock HTTP `201`, `200`, `204`, `401`, `404`, and `409`.
- [x] Unit tests verify `409` duplicate spawn falls back to patch.
- [x] Unit tests verify `folderName` status changes use delete-then-spawn.
- [ ] Manual command test: configure `base_url` and `api_key`.
- [ ] Manual command test: enable one guild and confirm online, idle, and dnd
      members appear in Pixelpipes.
- [ ] Manual command test: set one visible member offline and confirm the agent
      despawns.
- [ ] Manual command test: toggle `include_bots false` and confirm bot agents
      despawn.
- [ ] Manual command test: toggle `include_bots true` and confirm bot agents
      respawn if visible.
- [ ] Manual command test: rename a Discord member and confirm `agentName`
      updates.
- [ ] End-to-end verification: open
      `https://pixelpipes-webview-ui.vercel.app/?host=https://pa.lair.nntin.xyz`
      and confirm the webview shows agents labelled with Discord presence via
      `folderName`.

## Acceptance Criteria

- [ ] A guild admin can enable and disable Pixelpipes presence mirroring per
      guild.
- [ ] Visible members in enabled guilds are represented as Pixelpipes agents.
- [ ] Offline and invisible members are not represented as Pixelpipes agents.
- [ ] Discord display names are visible through `agentName`.
- [ ] Discord statuses `online`, `idle`, and `dnd` are visible through
      `folderName`.
- [ ] The cog remains compatible with the current Agent Control API.
- [ ] The API key is configurable and not leaked in command output or logs.
