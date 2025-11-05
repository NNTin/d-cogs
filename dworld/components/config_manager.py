"""Config manager component for D-World cog."""


class ConfigManager:
    """Manages all configuration-related functionality for D-World."""

    def __init__(self, bot, config, server):
        """Initialize the config manager.
        
        Args:
            bot: Discord bot instance
            config: Configuration object
            server: WebSocket server instance
        """
        self.bot = bot
        self.config = config
        self.server = server

    def initialize_config(self):
        """Initialize configuration with default values."""
        # Set up default config structure
        default_guild = {
            "passworded": False,
            "ignoreOfflineMembers": False,
            "members": {"user_id": {"role_color": "#ffffff", "custom_message": "foo"}},
            "selectedVersion": None,
        }
        default_global = {
            "client_id": None,
            "client_secret": None,
            "static_file_path": None,
            "socketURL": None,
        }
        self.config.register_guild(**default_guild)
        self.config.register_global(**default_global)

    async def set_client_id(self, client_id: str):
        """Set the global OAuth2 client ID.
        
        Args:
            client_id: The OAuth2 client ID
            
        Returns:
            tuple: (bool, str, bool) - (success, message, should_broadcast)
        """
        # Get the old client ID for comparison
        old_client_id = await self.config.client_id()
        
        await self.config.client_id.set(client_id)
        
        # Check if the client ID changed and we should broadcast
        should_broadcast = old_client_id != client_id
        
        return True, "Global OAuth2 client ID has been set.", should_broadcast

    async def update_client_id(self, client_id: str):
        """Update the global OAuth2 client ID and notify all connected clients.
        
        Args:
            client_id: The OAuth2 client ID
            
        Returns:
            tuple: (bool, str) - (success, message)
        """
        # Set the new client ID
        await self.config.client_id.set(client_id)

        # Broadcast the update to all connected clients across all servers
        failed_broadcasts = []
        for guild in self.bot.guilds:
            try:
                await self.server.broadcast_client_id_update(str(guild.id), client_id)
            except Exception as e:
                print(f"[ERROR] Failed to broadcast client ID update to guild {guild.name} ({guild.id}): {e}")
                failed_broadcasts.append(guild.name)

        if failed_broadcasts:
            return True, (f"✅ Global OAuth2 client ID has been updated to `{client_id}` and sent to all connected clients, "
                         f"but failed for {len(failed_broadcasts)} guild(s): {', '.join(failed_broadcasts)}")
        else:
            return True, f"✅ Global OAuth2 client ID has been updated to `{client_id}` and sent to all connected clients."

    async def set_client_secret(self, client_secret: str):
        """Set the global OAuth2 client secret.
        
        Args:
            client_secret: The OAuth2 client secret
            
        Returns:
            tuple: (bool, str) - (success, message)
        """
        await self.config.client_secret.set(client_secret)
        return True, "Global OAuth2 client secret has been set."

    async def get_status(self):
        """Get the current global configuration and server status.
        
        Returns:
            str: Formatted status message
        """
        client_id = await self.config.client_id()
        client_secret = await self.config.client_secret()
        static_file_path = await self.config.static_file_path()
        socketURL = await self.config.socketURL()

        # Check if credentials are set
        client_id_status = "✅ Set" if client_id else "❌ Not set"
        client_secret_status = "✅ Set" if client_secret else "❌ Not set"
        static_path_status = (
            f"✅ Set to `{static_file_path}`" if static_file_path else "❌ Not set"
        )
        socket_url_status = (
            f"✅ Set to `{socketURL}`" if socketURL else "❌ Not set"
        )

        # Count passworded servers
        passworded_servers = []
        ignore_offline_servers = []
        for guild in self.bot.guilds:
            if await self.config.guild(guild).passworded():
                passworded_servers.append(guild.name)
            if await self.config.guild(guild).ignoreOfflineMembers():
                ignore_offline_servers.append(guild.name)

        status_msg = f"""**D-World Configuration Status**

**Global OAuth2 Settings:**
• Client ID: {client_id_status}
• Client Secret: {client_secret_status}

**Static File Serving:**
• Static Path: {static_path_status}

**WebSocket Configuration:**
• Socket URL: {socket_url_status}

**Server Protection:**
• Protected servers: {len(passworded_servers)}
• Server list: {", ".join(passworded_servers) if passworded_servers else "None"}

**Offline Member Filtering:**
• Servers ignoring offline members: {len(ignore_offline_servers)}
• Server list: {", ".join(ignore_offline_servers) if ignore_offline_servers else "None"}

**WebSocket Server:**
• Status: Running on port 3000
• Connected clients: {len(self.server.connections)}"""

        if client_id:
            status_msg += f"\n• Current Client ID: `{client_id}`"

        return status_msg

    async def toggle_protection(self, guild):
        """Toggle password protection for a guild.
        
        Args:
            guild: Discord guild object
            
        Returns:
            tuple: (bool, str) - (success, message)
        """
        # Check if OAuth2 credentials are set (global config)
        client_id = await self.config.client_id()
        client_secret = await self.config.client_secret()

        if not client_id or not client_secret:
            return False, ("❌ Global OAuth2 client ID and client secret must be set before enabling password protection.\n"
                          "Use `dworldconfig setclientid <id>` and `dworldconfig setclientsecret <secret>` first.")

        # Get current password protection state
        current_state = await self.config.guild(guild).passworded()

        # Toggle the state
        new_state = not current_state
        await self.config.guild(guild).passworded.set(new_state)

        # Return result
        status = "enabled" if new_state else "disabled"
        return True, f"Password protection has been **{status}** for this server."

    async def toggle_ignore_offline(self, guild):
        """Toggle whether to ignore offline members for a guild.
        
        Args:
            guild: Discord guild object
            
        Returns:
            tuple: (bool, str) - (new_state, message)
        """
        # Get current state
        current_state = await self.config.guild(guild).ignoreOfflineMembers()

        # Toggle the state
        new_state = not current_state
        await self.config.guild(guild).ignoreOfflineMembers.set(new_state)

        # Return result
        status = "enabled" if new_state else "disabled"
        return new_state, f"Ignoring offline members has been **{status}** for this server."

    async def set_static_path(self, path: str = None):
        """Set the static file path for custom d-zone version serving.
        
        Args:
            path: Path to static files directory (None to disable)
            
        Returns:
            tuple: (bool, str) - (success, message)
        """
        await self.config.static_file_path.set(path)

        if path:
            return True, f"Static file path has been set to: `{path}`"
        else:
            return True, "Static file serving has been disabled."

    async def get_static_path(self):
        """Get the current static file path configuration.
        
        Returns:
            str: Path if set, None otherwise
        """
        return await self.config.static_file_path()

    async def set_socket_url(self, url: str = None):
        """Set the socket URL for WebSocket connections.
        
        Args:
            url: WebSocket server URL (None to clear)
            
        Returns:
            tuple: (bool, str) - (success, message)
        """
        await self.config.socketURL.set(url)

        if url:
            return True, f"Socket URL has been set to: `{url}`"
        else:
            return True, "Socket URL has been cleared."

    async def get_socket_url(self):
        """Get the current socket URL configuration.
        
        Returns:
            str: URL if set, None otherwise
        """
        return await self.config.socketURL()