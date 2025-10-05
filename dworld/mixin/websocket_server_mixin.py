import mimetypes
import os

import aiohttp
from d_back.server import WebSocketServer


class WebsocketServerMixin:
    """Mixin to provide a websocket server."""

    def __init__(self):
        self.server = WebSocketServer(port=3000, host="localhost")

        # Register callbacks for d-back server
        self.server.on_get_server_data(self._get_server_data)
        self.server.on_get_user_data(self._get_user_data)
        self.server.on_validate_discord_user(self._validate_discord_user)
        self.server.on_get_client_id(self._get_client_id)
        self.server.on_static_request(self._handle_static_request)

    async def _validate_discord_user(
        self, token: str, user_info: dict, discord_server_id: str
    ) -> bool:
        """Validate Discord OAuth2 user and check if they have access to the server."""
        try:
            if not user_info or not user_info.get("id"):
                print("[AUTH] No user info provided")
                return False

            # Get the guild
            guild = self.bot.get_guild(int(discord_server_id))
            if not guild:
                print(f"[AUTH] Guild {discord_server_id} not found")
                return False

            # Get OAuth2 credentials from config (now global)
            client_id = await self.config.client_id()
            client_secret = await self.config.client_secret()

            if not client_id or not client_secret:
                print("[AUTH] Global OAuth2 credentials not configured")
                return False

            # Validate the token with Discord API
            async with aiohttp.ClientSession() as session:
                # First, validate the token by getting user info
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/x-www-form-urlencoded",
                }

                # Get user info from Discord API
                async with session.get(
                    "https://discord.com/api/users/@me", headers=headers
                ) as resp:
                    if resp.status != 200:
                        print(
                            f"[AUTH] Token validation failed with status {resp.status}"
                        )
                        return False

                    api_user_info = await resp.json()

                    # Verify the user ID matches
                    if api_user_info.get("id") != user_info.get("id"):
                        print(
                            "[AUTH] User ID mismatch between token and provided user info"
                        )
                        return False

            # Check membership using bot's perspective
            # -> no need to check the users guilds, because the bot shares servers with the user
            # -> scope "guilds" is not needed
            user_id = int(user_info["id"])
            member = guild.get_member(user_id)
            if not member:
                print(
                    f"[AUTH] User {user_info.get('username')} ({user_id}) not found in guild {guild.name}"
                )
                return False

            print(
                f"[AUTH] User {member.display_name} ({user_id}) successfully validated for guild {guild.name}"
            )
            return True

        except aiohttp.ClientError as e:
            print(f"[ERROR] HTTP error during Discord API validation: {e}")
            return False
        except Exception as e:
            print(f"[ERROR] Discord user validation failed: {e}")
            return False

    async def _get_server_data(self):
        """Get server data in the format expected by the WebSocket server."""
        server_data = {}

        # Iterate through all guilds the bot is connected to
        for guild in self.bot.guilds:
            # Get the password protection state (defaults to False if not set)
            passworded = await self.config.guild(guild).passworded()

            server_data[str(guild.id)] = {
                "id": guild.name[:1].upper(),  # Use first letter of guild name as ID
                "name": guild.name,
                "default": False,  # Could be made configurable
                "passworded": passworded,
            }

        # If we have guilds, make the first one default
        if server_data:
            first_guild_id = list(server_data.keys())[0]
            server_data[first_guild_id]["default"] = True

        return server_data

    async def _get_user_data(self, discord_server_id: str = None):
        """Get user data for a specific guild in the format expected by the WebSocket server."""
        if not discord_server_id:
            return {}

        # Find the guild by ID
        guild = self.bot.get_guild(int(discord_server_id))
        if not guild:
            return {}

        # Check if we should ignore offline members for this guild
        ignore_offline = await self.config.guild(guild).ignoreOfflineMembers()

        user_data = {}

        # Iterate through all members in the guild
        for member in guild.members:
            # Skip bots if desired (uncomment next line to skip bots)
            # if member.bot:
            #     continue

            # Map Discord status to the expected format
            status_mapping = {
                "online": "online",
                "idle": "idle",
                "dnd": "dnd",
                "offline": "offline",
                "invisible": "offline",
            }

            status = status_mapping.get(str(member.status), "offline")

            # Skip offline members if ignoreOfflineMembers is enabled
            if ignore_offline and status == "offline":
                continue

            # Get the member's top role color
            role_color = "#ffffff"  # default white
            if member.top_role and member.top_role.color.value != 0:
                role_color = f"#{member.top_role.color.value:06x}"

            user_data[str(member.id)] = {
                "uid": str(member.id),
                "username": member.display_name,
                "status": status,
                "roleColor": role_color,
            }

        return user_data

    async def _get_client_id(self, discord_server_id: str = None):
        """Get the global OAuth2 client ID."""
        # Since client_id is now global, we don't need the discord_server_id parameter
        # but we keep it for compatibility with the callback interface
        client_id = await self.config.client_id()
        return client_id

    async def _handle_static_request(self, path: str):
        """Handle static file requests with optional custom d-zone version serving.

        Args:
            path: The requested static file path

        Returns:
            None: Let default handler process the request
            (content_type, content): Return custom content
        """
        try:
            # Get the configured static file path
            static_file_path = await self.config.static_file_path()

            # If no static path is configured, let default handler take over
            if not static_file_path:
                return None

            # Normalize the path and ensure it's safe
            path = path.lstrip("/")
            if ".." in path or path.startswith("/"):
                # Security: reject paths with .. or absolute paths
                return None

            # Default file serving
            if path == "/" or path == "":
                path = "index.html"

            # Construct the full file path
            full_path = os.path.join(static_file_path, path)

            # Get the MIME type
            content_type, _ = mimetypes.guess_type(full_path)
            if content_type is None:
                content_type = "application/octet-stream"

            print(f"[STATIC] Serving custom file: {path} -> {full_path}")
            return (content_type, full_path)

        except Exception as e:
            print(f"[ERROR] Static file serving error for {path}: {e}")
            return None
