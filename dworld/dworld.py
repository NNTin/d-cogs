import asyncio
from typing import Literal

from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.config import Config

from .components import ConfigManager, WebSocketServerManager, ListenerManager
from .utils import DWorldDashboardIntegration

RequestType = Literal["discord_deleted_user", "owner", "user", "user_strict"]


class dworld(DWorldDashboardIntegration, commands.Cog):
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

        # Initialize dashboard integration
        super().__init__()

        # Initialize components in the correct order
        self.ws_manager = WebSocketServerManager(self.bot, self.config)
        self.config_manager = ConfigManager(self.bot, self.config, self.ws_manager.server)
        self.listener_manager = ListenerManager(self.ws_manager.server, self.ws_manager.get_member_role_color)
        
        # Initialize configuration
        self.config_manager.initialize_config()

    @property
    def server(self):
        """Expose the WebSocket server for backward compatibility."""
        return self.ws_manager.server

    async def cog_load(self) -> None:
        await super().cog_load()

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

    # Configuration commands
    @commands.group(name="dworldconfig", invoke_without_command=True)
    async def dworldconfig(self, ctx):
        """Configuration commands for d-world"""
        await ctx.send_help(ctx.command)

    @commands.is_owner()
    @dworldconfig.command(name="setclientid")
    async def setclientid(self, ctx, client_id: str):
        """Set the global OAuth2 client ID"""
        success, message, should_broadcast = await self.config_manager.set_client_id(client_id)
        await ctx.send(message)
        
        if should_broadcast:
            # Broadcast to all connected servers
            failed_broadcasts = []
            for guild in self.bot.guilds:
                try:
                    await self.server.broadcast_client_id_update(str(guild.id), client_id)
                except Exception as e:
                    print(f"[ERROR] Failed to broadcast client ID update to guild {guild.name} ({guild.id}): {e}")
                    failed_broadcasts.append(guild.name)
            
            if failed_broadcasts:
                await ctx.send(
                    f"✅ Client ID update has been sent to all connected clients across all servers, "
                    f"but failed for {len(failed_broadcasts)} guild(s): {', '.join(failed_broadcasts)}"
                )
            else:
                await ctx.send(
                    "✅ Client ID update has been sent to all connected clients across all servers."
                )

    @commands.is_owner()
    @dworldconfig.command(name="updateclientid")
    async def updateclientid(self, ctx, client_id: str):
        """Update the global OAuth2 client ID and notify all connected clients"""
        success, message = await self.config_manager.update_client_id(client_id)
        await ctx.send(message)

    @commands.is_owner()
    @dworldconfig.command(name="setclientsecret")
    async def setclientsecret(self, ctx, client_secret: str):
        """Set the global OAuth2 client secret"""
        success, message = await self.config_manager.set_client_secret(client_secret)
        await ctx.send(message)

    @commands.is_owner()
    @dworldconfig.command(name="status")
    async def status(self, ctx):
        """Show the current global OAuth2 configuration and server status"""
        status_msg = await self.config_manager.get_status()
        await ctx.send(status_msg)

    @commands.mod()
    @dworldconfig.command(name="toggleprotection")
    async def toggleprotection(self, ctx):
        """toggle the password protection for the d-back server"""
        if not ctx.guild:
            await ctx.send("This command can only be used in a server.")
            return

        success, message = await self.config_manager.toggle_protection(ctx.guild)
        await ctx.send(message)

    @commands.mod()
    @dworldconfig.command(name="toggleignoreoffline")
    async def toggleignoreoffline(self, ctx):
        """Toggle whether to ignore offline members in user data for this server"""
        if not ctx.guild:
            await ctx.send("This command can only be used in a server.")
            return

        new_state, message = await self.config_manager.toggle_ignore_offline(ctx.guild)
        await ctx.send(message)

    @commands.is_owner()
    @dworldconfig.command(name="setstaticpath")
    async def setstaticpath(self, ctx, path: str = None):
        """Set the static file path for custom d-zone version serving

        Args:
            path: Path to static files directory (None to disable)
        """
        success, message = await self.config_manager.set_static_path(path)
        await ctx.send(message)

    @commands.is_owner()
    @dworldconfig.command(name="getstaticpath")
    async def getstaticpath(self, ctx):
        """Get the current static file path configuration"""
        path = await self.config_manager.get_static_path()

        if path:
            await ctx.send(f"Current static file path: `{path}`")
        else:
            await ctx.send("Static file serving is currently disabled.")

    @commands.is_owner()
    @dworldconfig.command(name="setsocketurl")
    async def setsocketurl(self, ctx, url: str = None):
        """Set the socket URL for WebSocket connections

        Args:
            url: WebSocket server URL (None to clear)
        """
        success, message = await self.config_manager.set_socket_url(url)
        await ctx.send(message)

    @commands.is_owner()
    @dworldconfig.command(name="getsocketurl")
    async def getsocketurl(self, ctx):
        """Get the current socket URL configuration"""
        url = await self.config_manager.get_socket_url()

        if url:
            await ctx.send(f"Current socket URL: `{url}`")
        else:
            await ctx.send("Socket URL is not currently set.")

    # Event listeners
    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        """Handle member status/presence updates."""
        await self.listener_manager.handle_member_update(before, after)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Handle member joining a guild."""
        await self.listener_manager.handle_member_join(member)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        """Handle member leaving a guild."""
        await self.listener_manager.handle_member_remove(member)

    @commands.Cog.listener()
    async def on_message(self, message):
        """Handle new messages and broadcast them."""
        await self.listener_manager.handle_message(message)
