"""Listener manager component for D-World cog."""


class ListenerManager:
    """Manages Discord event listener functionality for D-World."""

    # Status mapping dictionary to avoid duplication
    STATUS_MAPPING = {
        "online": "online",
        "idle": "idle",
        "dnd": "dnd",
        "offline": "offline",
        "invisible": "offline",
    }

    def __init__(self, server, get_member_role_color_func):
        """Initialize the listener manager.
        
        Args:
            server: WebSocket server instance
            get_member_role_color_func: Function to get member role color
        """
        self.server = server
        self.get_member_role_color_func = get_member_role_color_func

    async def handle_member_update(self, before, after):
        """Handle member status/presence updates."""
        if before.status != after.status:
            # Status changed, broadcast the update
            role_color = await self.get_member_role_color_func(after)

            status = self.STATUS_MAPPING.get(str(after.status), "offline")

            await self.server.broadcast_presence(
                server=str(after.guild.id),
                uid=str(after.id),
                status=status,
                username=after.display_name,
                role_color=role_color,
            )

    async def handle_member_join(self, member):
        """Handle member joining a guild."""
        role_color = await self.get_member_role_color_func(member)

        status = self.STATUS_MAPPING.get(str(member.status), "offline")

        await self.server.broadcast_presence(
            server=str(member.guild.id),
            uid=str(member.id),
            status=status,
            username=member.display_name,
            role_color=role_color,
        )

    async def handle_member_remove(self, member):
        """Handle member leaving a guild."""
        await self.server.broadcast_presence(
            server=str(member.guild.id),
            uid=str(member.id),
            status="offline",
            delete=True,
        )

    async def handle_message(self, message):
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