# Pixelagents Architecture

## Overview

`pixelagents` is a Red DiscordBot cog that projects Discord presence into the
Pixelpipes webview. It observes enabled guilds, maps each visible Discord member
to a Pixelpipes agent, and drives lifecycle changes through a persistent
WebSocket connection directly to the Pixelpipes standalone host.

```text
Discord gateway events
  -> Red cog: pixelagents
  -> ws://standalone:3210/ws/producer  (internal Docker network)
  -> Pixelpipes standalone host
  -> Pixelpipes webview
```

The cog speaks WebSocket directly to Pixelpipes. Node-RED is not in this path.

## Configuration Model

Global configuration:

- `producer_url`: Pixelpipes producer WebSocket URL.
  Default: `ws://standalone:3210/ws/producer`.
- `message_tool_clear_delay`: seconds to keep the Discord message indicator
  visible. Default: `2.0`.
- `editor_role_id`: Discord role ID whose members may edit webview
  furniture/layout via Discord OAuth. Default: `None` (no role configured).

Guild configuration:

- `enabled`: whether the guild is mirrored into Pixelpipes. Default: `false`.
- `include_bots`: whether bot users are mirrored. Default: `true`.

Configuration is managed by admin-only commands.

## Agent Identity

Each Discord user maps to a deterministic Pixelpipes agent ID:

```python
_JS_MAX_SAFE = (1 << 53) - 1  # 9007199254740991

def _discord_id_to_agent_id(user_id: int) -> int:
    mapped = user_id % _JS_MAX_SAFE
    return -(mapped if mapped != 0 else _JS_MAX_SAFE)
```

Properties:
- Stable across cog restarts (derived from Discord user ID only).
- Always negative — does not collide with Node-RED's positive overlay IDs or
  Pixelpipes' own positive agent IDs.
- JavaScript-safe: magnitude ≤ 2^53 - 1.
- Collisions are logged at WARNING level if they occur (unlikely in practice).

## Producer Protocol

On connection the cog sends:

```json
{ "type": "producerHello", "capabilities": ["auth-check"] }
```

This registers the cog as a Discord producer. The standalone host routes
`producerAuthCheckRequest` messages only to producers that advertise the
`auth-check` capability.

### Bootstrap

When the standalone host sends `producerBootstrapRequest`, the cog replies
with `existingAgents` listing all currently tracked agents.

### Presence Lifecycle

| Discord status | Cog behavior |
|---|---|
| `online` | `agentCreated` with `folderName: "online"` + `agentTeamInfo` + `agentStatus` |
| `idle` | `agentCreated` with `folderName: "idle"` + `agentTeamInfo` + `agentStatus` |
| `dnd` | `agentCreated` with `folderName: "dnd"` + `agentTeamInfo` + `agentStatus` |
| `offline` | `agentClosed` |
| `invisible` | `agentClosed` |

A presence label change (e.g. `online` → `dnd`) sends `agentClosed` followed
by a new `agentCreated` because `folderName` is immutable after creation.

A display name change sends `agentTeamInfo` with the new `agentName`.

An `agentStatus` of `"active"` is sent when the member has non-custom Discord
rich presence; `"waiting"` otherwise.

After each state change, the cog sends `existingAgents` to let the standalone
reconcile its overlay.

### Discord Message Activity

When a tracked member sends a Discord message:

```json
{ "type": "agentToolStart", "id": <agentId>, "toolId": "msg-<messageId>",
  "toolName": "Message", "status": "<truncated content>" }
```

After `message_tool_clear_delay` seconds:

```json
{ "type": "agentToolsClear", "id": <agentId> }
```

## Editor Authorization

The standalone host sends auth-check requests when a browser completes Discord
OAuth login:

```json
{ "type": "producerAuthCheckRequest", "requestId": "<uuid>",
  "discordUserId": "<id>" }
```

The cog checks:
1. Is the Discord user a bot owner (`bot.is_owner`)? → allow
2. Is the `editor_role_id` configured and does the user have that role in any
   enabled guild? → allow
3. Otherwise → deny

```json
{ "type": "producerAuthCheckReply", "requestId": "<uuid>", "allowed": true }
```

The auth policy:
- **Allow** bot owners.
- **Allow** members with the configured editor role.
- **Deny** everyone else, including if the role is not configured, the user is
  not in any enabled guild, or Discord lookup fails.

## Connection and Reconnect

The cog maintains a persistent WebSocket connection:
- On cog load, opens connection and runs a full guild sync.
- On disconnect, retries with exponential backoff (1s → 2s → 4s … max 60s).
- On reconnect, re-sends `producerHello` and re-runs full guild sync.
- Deterministic agent IDs prevent duplicate agents after reconnect.

## Security And Privacy

- Discord user IDs, guild IDs, display names, presence labels, and message
  content (truncated to 40 chars) are sent to the producer endpoint.
- The producer endpoint is on the internal Docker network only — not exposed
  through Traefik.
- No bearer tokens or API keys are needed (Docker network is the trust boundary).

## Network

The Red bot container must be attached to the `pixel-agents` Docker network so
that `standalone:3210` resolves from inside the container. The `redstack`
Docker Compose template includes both `redstack` and `pixel-agents` networks.

## Commands

| Command | Description |
|---|---|
| `[p]pixelagents status` | Show configuration and connection status |
| `[p]pixelagents enable` | Enable guild mirroring and run a full sync |
| `[p]pixelagents disable` | Disable guild mirroring and despawn all agents |
| `[p]pixelagents sync` | Manually reconcile guild members |
| `[p]pixelagents despawnall` | Despawn all tracked agents without disabling |
| `[p]pixelagents includebots <true/false>` | Toggle bot user mirroring |
| `[p]pixelagents producerurl <url>` | Override the producer WebSocket URL |
| `[p]pixelagents toolcleardelay <seconds>` | Set message indicator duration |
| `[p]pixelagents editorrole [role]` | Set or clear the editor role |

## End-to-End Verification

1. Ensure the `pixel-agents` Docker network exists and both `standalone` and
   `red-pico` containers are attached to it.
2. Load the cog in Red. `[p]pixelagents status` should show "Connected: Yes".
3. Enable the target guild: `[p]pixelagents enable`.
4. Open `https://pa.lair.nntin.xyz` in a browser.
5. Confirm online, idle, and dnd users appear as agents in the webview.
6. Set a user offline — confirm their agent despawns.
7. Send a Discord message from a tracked user — confirm the tool bubble appears.
8. For editor auth: complete Discord OAuth at `/api/discord/auth/start`. Bot
   owners and configured role members should be able to save layouts; others
   should be denied.
