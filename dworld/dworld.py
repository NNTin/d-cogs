import asyncio
from typing import Literal

import aiohttp
from d_back.server import WebSocketServer
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.config import Config

RequestType = Literal["discord_deleted_user", "owner", "user", "user_strict"]


class dworld(commands.Cog):
    """
    d-world implements d-back
    """

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(
            self,
            identifier=257263088,
            force_registration=True,
        )

        # Set up default config structure
        default_guild = {
            "passworded": False,
        }
        default_global = {
            "client_id": None,
            "client_secret": None,
        }
        self.config.register_guild(**default_guild)
        self.config.register_global(**default_global)

        # Cache for password protection states
        # this dict is needed because _get_server_data is a sync method
        # method is a sync method because it is required by d-back
        self._password_cache = {}

        self.server = WebSocketServer(port=3000, host="localhost")

        # Register callbacks for server and user data
        self.server.on_get_server_data(self._get_server_data)
        self.server.on_get_user_data(self._get_user_data)
        self.server.on_validate_discord_user(self._validate_discord_user)
        self.server.on_get_client_id(self._get_client_id)

    @commands.group(name="dworldconfig", invoke_without_command=True)
    async def dworldconfig(self, ctx):
        """Configuration commands for d-world"""
        await ctx.send_help(ctx.command)

    @dworldconfig.command(name="setclientid")
    async def setclientid(self, ctx, client_id: str):
        """Set the global OAuth2 client ID"""
        # Get the old client ID for comparison
        old_client_id = await self.config.client_id()

        await self.config.client_id.set(client_id)
        await ctx.send("Global OAuth2 client ID has been set.")

        # If the client ID changed and there are connected clients, broadcast the update to all servers
        if old_client_id != client_id:
            # Broadcast to all connected servers
            for guild in self.bot.guilds:
                await self.server.broadcast_client_id_update(str(guild.id), client_id)
            await ctx.send(
                "✅ Client ID update has been sent to all connected clients across all servers."
            )

    @dworldconfig.command(name="updateclientid")
    async def updateclientid(self, ctx, client_id: str):
        """Update the global OAuth2 client ID and notify all connected clients"""
        # Set the new client ID
        await self.config.client_id.set(client_id)

        # Broadcast the update to all connected clients across all servers
        for guild in self.bot.guilds:
            await self.server.broadcast_client_id_update(str(guild.id), client_id)

        await ctx.send(
            f"✅ Global OAuth2 client ID has been updated to `{client_id}` and sent to all connected clients."
        )

    @dworldconfig.command(name="setclientsecret")
    async def setclientsecret(self, ctx, client_secret: str):
        """Set the global OAuth2 client secret"""
        await self.config.client_secret.set(client_secret)
        await ctx.send("Global OAuth2 client secret has been set.")

    @dworldconfig.command(name="status")
    async def status(self, ctx):
        """Show the current global OAuth2 configuration and server status"""
        client_id = await self.config.client_id()
        client_secret = await self.config.client_secret()

        # Check if credentials are set
        client_id_status = "✅ Set" if client_id else "❌ Not set"
        client_secret_status = "✅ Set" if client_secret else "❌ Not set"

        # Count passworded servers
        passworded_servers = []
        for guild in self.bot.guilds:
            if self._password_cache.get(guild.id, False):
                passworded_servers.append(guild.name)

        status_msg = f"""**D-World Configuration Status**

**Global OAuth2 Settings:**
• Client ID: {client_id_status}
• Client Secret: {client_secret_status}

**Server Protection:**
• Protected servers: {len(passworded_servers)}
• Server list: {", ".join(passworded_servers) if passworded_servers else "None"}

**WebSocket Server:**
• Status: Running on port 3000
• Connected clients: {len(self.server.connections)}"""

        if client_id:
            status_msg += f"\n• Current Client ID: `{client_id}`"

        await ctx.send(status_msg)

    @dworldconfig.command(name="toggleprotection")
    async def toggleprotection(self, ctx):
        """toggle the password protection for the d-back server"""
        if not ctx.guild:
            await ctx.send("This command can only be used in a server.")
            return

        # Check if OAuth2 credentials are set (global config)
        client_id = await self.config.client_id()
        client_secret = await self.config.client_secret()

        if not client_id or not client_secret:
            await ctx.send(
                "❌ Global OAuth2 client ID and client secret must be set before enabling password protection.\n"
                "Use `dworldconfig setclientid <id>` and `dworldconfig setclientsecret <secret>` first."
            )
            return

        # Get current password protection state
        current_state = await self.config.guild(ctx.guild).passworded()

        # Toggle the state
        new_state = not current_state
        await self.config.guild(ctx.guild).passworded.set(new_state)

        # Update the cache
        self._password_cache[ctx.guild.id] = new_state

        # Send confirmation message
        status = "enabled" if new_state else "disabled"
        await ctx.send(f"Password protection has been **{status}** for this server.")

    async def cog_load(self) -> None:
        await super().cog_load()

        # TODO: The fucking fuck, my default configuration is not loaded.

        # Load password protection states into cache
        print(f"Loading password cache for {len(self.bot.guilds)} guilds...")
        for guild in self.bot.guilds:
            passworded_state = await self.config.guild(guild).passworded()
            self._password_cache[guild.id] = passworded_state
            print(f"Guild {guild.name} ({guild.id}): passworded = {passworded_state}")

        print("Starting websockets server...")

        asyncio.create_task(self.server.start())

    async def cog_unload(self) -> None:
        await super().cog_unload()
        print("Stopping websockets server...")
        await self.server.stop()

    async def red_delete_data_for_user(
        self, *, requester: RequestType, user_id: int
    ) -> None:
        super().red_delete_data_for_user(requester=requester, user_id=user_id)

    def _get_server_data(self):
        """Get server data in the format expected by the WebSocket server."""
        server_data = {}

        # Iterate through all guilds the bot is connected to
        for guild in self.bot.guilds:
            # Get the password protection state from cache (defaults to False if not set)
            passworded = self._password_cache.get(guild.id, False)

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

    def _get_user_data(self, discord_server_id: str = None):
        """Get user data for a specific guild in the format expected by the WebSocket server."""
        if not discord_server_id:
            return {}

        # Find the guild by ID
        guild = self.bot.get_guild(int(discord_server_id))
        if not guild:
            return {}

        user_data = {}

        # Iterate through all members in the guild
        for member in guild.members:
            # Skip bots if desired (uncomment next line to skip bots)
            # if member.bot:
            #     continue

            # Get the member's top role color
            role_color = "#ffffff"  # default white
            if member.top_role and member.top_role.color.value != 0:
                role_color = f"#{member.top_role.color.value:06x}"

            # Map Discord status to the expected format
            status_mapping = {
                "online": "online",
                "idle": "idle",
                "dnd": "dnd",
                "offline": "offline",
                "invisible": "offline",
            }

            status = status_mapping.get(str(member.status), "offline")

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

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        """Handle member status/presence updates."""
        if before.status != after.status:
            # Status changed, broadcast the update
            role_color = "#ffffff"
            if after.top_role and after.top_role.color.value != 0:
                role_color = f"#{after.top_role.color.value:06x}"

            status_mapping = {
                "online": "online",
                "idle": "idle",
                "dnd": "dnd",
                "offline": "offline",
                "invisible": "offline",
            }

            status = status_mapping.get(str(after.status), "offline")

            await self.server.broadcast_presence(
                server=str(after.guild.id),
                uid=str(after.id),
                status=status,
                username=after.display_name,
                role_color=role_color,
            )

    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Handle member joining a guild."""
        role_color = "#ffffff"
        if member.top_role and member.top_role.color.value != 0:
            role_color = f"#{member.top_role.color.value:06x}"

        status_mapping = {
            "online": "online",
            "idle": "idle",
            "dnd": "dnd",
            "offline": "offline",
            "invisible": "offline",
        }

        status = status_mapping.get(str(member.status), "offline")

        await self.server.broadcast_presence(
            server=str(member.guild.id),
            uid=str(member.id),
            status=status,
            username=member.display_name,
            role_color=role_color,
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        """Handle member leaving a guild."""
        await self.server.broadcast_presence(
            server=str(member.guild.id),
            uid=str(member.id),
            status="offline",
            delete=True,
        )

    @commands.Cog.listener()
    async def on_message(self, message):
        """Handle new messages and broadcast them."""
        # Skip messages from bots (including this bot)
        if message.author.bot:
            return

        # Only handle messages from guilds (not DMs)
        if not message.guild:
            return

        await self.server.broadcast_message(
            server=str(message.guild.id),
            uid=str(message.author.id),
            message=message.content,
            channel=str(message.channel.id),
        )

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
