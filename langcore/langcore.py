import logging
from typing import Literal, Union

import discord
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.config import Config

from .conversation import ConversationManager
from .hub import ChainHub
from .models import GuildConfig

RequestType = Literal["discord_deleted_user", "owner", "user", "user_strict"]

log = logging.getLogger("red.langcore")


class langcore(commands.Cog):
    """
    core framework cog build on top of LangChain framework
    """

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(
            self,
            identifier=257263088,
            force_registration=True,
        )
        default_guild = GuildConfig().model_dump(exclude_defaults=False)
        self.config.register_guild(
            enabled=default_guild["enabled"],
            max_retention=default_guild["max_retention"],
            max_retention_time=default_guild["max_retention_time"],
            blacklist=default_guild["blacklist"],
            role_overrides=default_guild["role_overrides"],
            function_statuses=default_guild["function_statuses"],
        )

        self.conversation_manager = ConversationManager()
        self.hub = ChainHub(self.bot)

    async def get_guild_config(self, guild_id: int) -> GuildConfig:
        """Retrieve guild configuration with validation and fallback to defaults."""
        data = await self.config.guild_from_id(guild_id).all()
        try:
            return GuildConfig.model_validate(data)
        except Exception as exc:  # noqa: BLE001
            log.warning("Invalid langcore config for guild %s, using defaults: %s", guild_id, exc)
            default = GuildConfig()
            guild_conf = self.config.guild_from_id(guild_id)
            default_data = default.model_dump(exclude_defaults=False)
            for key in default_data.keys():
                await getattr(guild_conf, key).set(default_data[key])
            return default

    async def save_guild_config(self, guild_id: int, config: GuildConfig) -> bool:
        """Save guild configuration with validation."""
        try:
            guild_conf = self.config.guild_from_id(guild_id)
            config_data = config.model_dump(exclude_defaults=False)
            for key, value in config_data.items():
                await getattr(guild_conf, key).set(value)
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to save guild config for %s: %s", guild_id, exc)
            return False

    @commands.group(name="langcore")
    @commands.admin_or_permissions(administrator=True)
    @commands.guild_only()
    async def langcore_config(self, ctx: commands.Context):
        """Manage langcore framework configuration."""
        pass

    @langcore_config.command(name="settings")
    async def view_settings(self, ctx: commands.Context):
        """View current langcore configuration for this server."""
        config = await self.get_guild_config(ctx.guild.id)

        embed = discord.Embed(
            title="LangCore Configuration",
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="Status",
            value=f"Enabled: {config.enabled}",
            inline=False,
        )
        embed.add_field(
            name="Conversation Retention",
            value=f"Max Messages: {config.max_retention}\nMax Time: {config.max_retention_time}s",
            inline=False,
        )
        embed.add_field(
            name="Blacklist",
            value=f"{len(config.blacklist)} entries" if config.blacklist else "None",
            inline=True,
        )
        embed.add_field(
            name="Function Statuses",
            value=f"{len(config.function_statuses)} configured" if config.function_statuses else "All enabled",
            inline=True,
        )
        embed.add_field(
            name="Role Overrides",
            value=f"{len(config.role_overrides)} configured" if config.role_overrides else "None",
            inline=True,
        )

        await ctx.send(embed=embed)

    @langcore_config.command(name="toggle")
    async def toggle_enabled(self, ctx: commands.Context):
        """Enable or disable langcore for this server."""
        current = await self.config.guild(ctx.guild).enabled()
        await self.config.guild(ctx.guild).enabled.set(not current)
        status = "enabled" if not current else "disabled"
        await ctx.send(f"LangCore has been {status} for this server.")

    @langcore_config.command(name="maxretention")
    async def set_max_retention(self, ctx: commands.Context, messages: int):
        """Set maximum conversation message retention (0 for unlimited)."""
        if messages < 0:
            await ctx.send("Message count must be 0 or greater.")
            return
        await self.config.guild(ctx.guild).max_retention.set(messages)
        await ctx.send(f"Max retention set to {messages} messages.")

    @langcore_config.command(name="maxtime")
    async def set_max_time(self, ctx: commands.Context, seconds: int):
        """Set maximum conversation time retention in seconds (0 for unlimited)."""
        if seconds < 0:
            await ctx.send("Time must be 0 or greater.")
            return
        await self.config.guild(ctx.guild).max_retention_time.set(seconds)
        await ctx.send(f"Max retention time set to {seconds} seconds.")

    @langcore_config.group(name="blacklist")
    async def blacklist_group(self, ctx: commands.Context):
        """Manage blacklisted users/roles/channels."""
        pass

    @blacklist_group.command(name="add")
    async def blacklist_add(
        self,
        ctx: commands.Context,
        target: Union[discord.Member, discord.Role, discord.TextChannel],
    ):
        """Add a user, role, or channel to the blacklist."""
        async with self.config.guild(ctx.guild).blacklist() as blacklist:
            if target.id in blacklist:
                await ctx.send(f"{target.mention} is already blacklisted.")
                return
            blacklist.append(target.id)
        await ctx.send(f"Added {target.mention} to blacklist.")

    @blacklist_group.command(name="remove")
    async def blacklist_remove(
        self,
        ctx: commands.Context,
        target: Union[discord.Member, discord.Role, discord.TextChannel],
    ):
        """Remove a user, role, or channel from the blacklist."""
        async with self.config.guild(ctx.guild).blacklist() as blacklist:
            if target.id not in blacklist:
                await ctx.send(f"{target.mention} is not blacklisted.")
                return
            blacklist.remove(target.id)
        await ctx.send(f"Removed {target.mention} from blacklist.")

    @blacklist_group.command(name="list")
    async def blacklist_list(self, ctx: commands.Context):
        """List all blacklisted entries."""
        blacklist = await self.config.guild(ctx.guild).blacklist()
        if not blacklist:
            await ctx.send("No blacklisted entries.")
            return

        entries = []
        for entry_id in blacklist:
            obj = (
                ctx.guild.get_member(entry_id)
                or ctx.guild.get_role(entry_id)
                or ctx.guild.get_channel(entry_id)
            )
            if obj:
                entries.append(f"- {obj.mention} ({obj.id})")
            else:
                entries.append(f"- Unknown ID: {entry_id}")

        embed = discord.Embed(
            title="Blacklist",
            description="\n".join(entries),
            color=discord.Color.red(),
        )
        await ctx.send(embed=embed)

    @langcore_config.group(name="functions")
    async def functions_group(self, ctx: commands.Context):
        """Manage function enable/disable status."""
        pass

    @functions_group.command(name="list")
    async def functions_list(self, ctx: commands.Context):
        """List all registered functions and their status."""
        config = await self.get_guild_config(ctx.guild.id)
        registered_cogs = self.hub.get_registered_cogs()

        if not registered_cogs:
            await ctx.send("No functions registered.")
            return

        embed = discord.Embed(
            title="Registered Functions",
            color=discord.Color.green(),
        )

        for cog_name in registered_cogs:
            functions = self.hub.get_cog_functions(cog_name)
            status_list = []
            for func_name in functions:
                enabled = config.function_statuses.get(func_name, True)
                status = "✅" if enabled else "❌"
                status_list.append(f"{status} {func_name}")

            embed.add_field(
                name=cog_name,
                value="\n".join(status_list) if status_list else "No functions",
                inline=False,
            )

        await ctx.send(embed=embed)

    @functions_group.command(name="toggle")
    async def functions_toggle(self, ctx: commands.Context, function_name: str):
        """Toggle a function's enabled status."""
        async with self.config.guild(ctx.guild).function_statuses() as statuses:
            current = statuses.get(function_name, True)
            statuses[function_name] = not current
            status = "enabled" if not current else "disabled"
        await ctx.send(f"Function `{function_name}` has been {status}.")

    async def red_delete_data_for_user(self, *, requester: RequestType, user_id: int) -> None:
        # TODO: Replace this with the proper end user data removal handling.
        super().red_delete_data_for_user(requester=requester, user_id=user_id)
