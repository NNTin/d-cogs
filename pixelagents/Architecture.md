# Pixelagents Architecture

## Overview

`pixelagents` is a Red DiscordBot cog that projects Discord presence into the
Pixelpipes webview. It observes enabled guilds, maps each visible Discord member
to one Node-RED overlay-managed agent, and drives lifecycle changes through the
pixel-agents-node-red Agent Control API.

The cog is intentionally an integration layer. It does not host a webview, does
not speak WebSocket directly to Pixelpipes, and does not require changes to
Pixelpipes or Node-RED.

```text
Discord gateway events
  -> Red cog: pixelagents
  -> HTTPS Agent Control API
  -> pixel-agents-node-red
  -> Pixelpipes standalone host events
  -> Pixelpipes webview
```

## Configuration Model

Global configuration:

- `base_url`: Node-RED base URL. Default: `https://pp.lair.nntin.xyz/`.
- `api_key`: bearer token for the Agent Control API.
- `timeout_seconds`: HTTP request timeout. Default: `10`.

Guild configuration:

- `enabled`: whether the guild is mirrored into Pixelpipes. Default: `false`.
- `include_bots`: whether bot users are mirrored. Default: `true`.

Configuration is managed by admin-only commands. The API key command deletes the
invoking Discord message when possible and status output only reports whether a
key is set.

## Agent Identity And Payload Mapping

Each Discord member gets a stable `agentKey`:

```text
discord:<guild_id>:<user_id>
```

The value is stable across nickname changes and avoids collisions when the same
Discord user appears in multiple guilds.

The cog sends only fields accepted by the current Agent Control API:

| Discord concept | Agent Control API field | Example |
|---|---|---|
| Guild member identity | `agentKey` | `discord:123:456` |
| Guild display name | `agentName` | `Tin` |
| Presence label | `folderName` | `online`, `idle`, `dnd` |

Spawn payload:

```json
{
  "agentKey": "discord:123:456",
  "agentName": "Tin",
  "folderName": "online",
  "selected": false
}
```

Display name update payload:

```json
{
  "agentName": "New Nickname"
}
```

The current PATCH endpoint supports `agentName` but not `folderName`. To change
the visible presence label for an already spawned member, the cog despawns and
respawns that member.

## Presence Lifecycle

Presence mapping:

| Discord status | Cog behavior |
|---|---|
| `online` | Ensure agent is spawned with `folderName: "online"` |
| `idle` | Ensure agent is spawned with `folderName: "idle"` |
| `dnd` | Ensure agent is spawned with `folderName: "dnd"` |
| `offline` | Despawn agent |
| `invisible` | Despawn agent |

Main flows:

- Cog load: reconcile only guilds that are already enabled.
- Guild enable: persist `enabled=true`, then full sync all eligible members.
- Guild disable: persist `enabled=false`, then despawn all tracked members for
  that guild.
- Manual sync: list guild members and reconcile each member against current
  Discord status and bot inclusion settings.
- Member update: if status or display name changed, reconcile only that member.
- Member join: reconcile the joined member.
- Member remove: despawn that member.

Bot handling:

- Bots are mirrored by default.
- If `include_bots=false`, bot users are treated as excluded and despawned.
- The bot account itself follows the same rule.

## HTTP Client Behavior

The cog uses asynchronous HTTP requests with:

```text
Authorization: Bearer <api_key>
Content-Type: application/json
```

Expected operations:

- `POST {base_url}/api/agents` for spawn.
- `PATCH {base_url}/api/agents/{agentKey}/state` for mutable metadata such as
  `agentName`.
- `DELETE {base_url}/api/agents/{agentKey}` for despawn.
- `GET {base_url}/api/agents` for diagnostics and future reconciliation checks.

Response handling:

- `201` spawn success.
- `200` patch/list success.
- `204` despawn success.
- `404` on delete is treated as already despawned.
- `409` on spawn is treated as already spawned; the cog patches mutable state.
- `401` is reported as configuration/auth failure.
- Network errors are logged and surfaced in command responses for manual
  commands.

The client must normalize URL joining so `https://pp.lair.nntin.xyz/` and
`https://pp.lair.nntin.xyz` both resolve to the same endpoint paths.

## Reliability Notes

Presence events can arrive quickly and repeatedly. The implementation should
make reconcile operations idempotent:

- Despawning an already missing agent is success.
- Spawning an already existing agent is success after patching mutable fields.
- Status label changes use delete-then-spawn because `folderName` is immutable
  through the current PATCH API.

The cog should keep a small in-memory cache per guild/member with the last
successfully applied `folderName` and `agentName`. The cache is an optimization,
not the source of truth; manual sync can rebuild state from Discord presence.

## Security And Privacy

The cog sends Discord user IDs, guild IDs, display names, bot status by
inclusion behavior, and presence labels to the configured Node-RED endpoint.
It does not persist message content.

The API key is sensitive:

- Never log it.
- Never render it in Discord.
- Delete the setup command message when possible.
- Keep command status limited to "set" or "not set".

## End-to-End Verification

After implementation and configuration:

1. Set `base_url` to `https://pp.lair.nntin.xyz/`.
2. Set the Agent Control API bearer key.
3. Enable the target guild.
4. Open `https://pixelpipes-webview-ui.vercel.app/?host=https://pa.lair.nntin.xyz`.
5. Confirm online, idle, and dnd users appear as agents.
6. Confirm each agent label shows the Discord display name and presence label.
7. Set a user offline and confirm their agent despawns.
