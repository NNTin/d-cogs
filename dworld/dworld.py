import asyncio
from typing import Literal

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
        self.config.register_guild(**default_guild)

        # Cache for password protection states
        self._password_cache = {}

        self.server = WebSocketServer(port=3000, host="localhost")

        # Register callbacks for server and user data
        self.server.on_get_server_data(self._get_server_data)
        self.server.on_get_user_data(self._get_user_data)
        self.server.on_validate_discord_user(self._validate_discord_user)

    @commands.command()
    async def toggleprotection(self, ctx):
        """toggle the password protection for the d-back server"""
        if not ctx.guild:
            await ctx.send("This command can only be used in a server.")
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

        # Load password protection states into cache
        for guild in self.bot.guilds:
            passworded = await self.config.guild(guild).passworded()
            self._password_cache[guild.id] = passworded

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
                return False

            user_id = int(user_info["id"])

            # Get the guild
            guild = self.bot.get_guild(int(discord_server_id))
            if not guild:
                print(f"[AUTH] Guild {discord_server_id} not found")
                return False

            # Check if the user is a member of the guild
            member = guild.get_member(user_id)
            if not member:
                print(
                    f"[AUTH] User {user_info.get('username')} ({user_id}) is not a member of guild {guild.name}"
                )
                return False

            print(
                f"[AUTH] User {member.display_name} ({user_id}) validated for guild {guild.name}"
            )
            return True

        except Exception as e:
            print(f"[ERROR] Discord user validation failed: {e}")
            return False
