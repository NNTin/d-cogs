from redbot.core import commands


class ListenerMixin:
    """A mixin providing listener functionality."""

    def __init__(self):
        pass

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        """Handle member status/presence updates."""
        if before.status != after.status:
            # Status changed, broadcast the update
            role_color = await self._get_member_role_color(after)

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
        role_color = await self._get_member_role_color(member)

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
