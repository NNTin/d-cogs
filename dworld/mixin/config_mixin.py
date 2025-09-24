from redbot.core import commands


class ConfigMixin:
    """Mixin class for d-world configuration commands"""

    def __init__(self):
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

    @commands.group(name="dworldconfig", invoke_without_command=True)
    async def dworldconfig(self, ctx):
        """Configuration commands for d-world"""
        await ctx.send_help(ctx.command)

    @commands.is_owner()
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

    @commands.is_owner()
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

    @commands.is_owner()
    @dworldconfig.command(name="setclientsecret")
    async def setclientsecret(self, ctx, client_secret: str):
        """Set the global OAuth2 client secret"""
        await self.config.client_secret.set(client_secret)
        await ctx.send("Global OAuth2 client secret has been set.")

    @commands.is_owner()
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

    @commands.mod()
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

    async def load_cache(self):
        # guilds is available when red is ready
        await self.bot.wait_until_red_ready()

        # Load password protection states into cache
        print(f"Loading password cache for {len(self.bot.guilds)} guilds...")
        for guild in self.bot.guilds:
            passworded_state = await self.config.guild(guild).passworded()
            self._password_cache[guild.id] = passworded_state
            print(f"Guild {guild.name} ({guild.id}): passworded = {passworded_state}")
