import asyncio
import logging
import json
import ast
import base64
from io import BytesIO
from datetime import datetime
from typing import Dict, List, Literal, Optional, Union

import discord
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.config import Config
from redbot.core.utils.chat_formatting import pagify, text_to_file

from .abc import ChainProvider, ChainStore, MessageHandler
from .classifier import ClassifierManager
from .conversation import ConversationManager
from .hub import ChainHub
from .models import GuildConfig

RequestType = Literal["discord_deleted_user", "owner", "user", "user_strict"]

log = logging.getLogger("red.tin.langcore")


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
            channel_id=default_guild["channel_id"],
            listen_channels=default_guild["listen_channels"],
            mention_respond=default_guild["mention_respond"],
            min_length=default_guild["min_length"],
            use_classifier=default_guild["use_classifier"],
            classifier_model=default_guild["classifier_model"],
        )

        self.conversation_manager = ConversationManager()
        self.classifier_manager = ClassifierManager()
        self.hub = ChainHub(self.bot)
        self.providers: Dict[str, ChainProvider] = {}
        self.message_handlers: Dict[str, MessageHandler] = {}
        self.chain_store: Optional[ChainStore] = None

    def register_provider(self, name: str, provider: ChainProvider) -> bool:
        """Register a ChainProvider implementation.

        Args:
            name: Unique identifier for the provider (typically cog name)
            provider: ChainProvider implementation instance

        Returns:
            bool: True if registration succeeded
        """
        if not isinstance(provider, ChainProvider):
            log.warning("Provider registration failed for %s: not a ChainProvider instance", name)
            return False

        existing = self.providers.get(name)
        if existing is provider:
            log.debug("Provider %s already registered with same instance; skipping", name)
            return True

        if existing is not None:
            log.warning("Provider %s already registered, overwriting", name)

        self.providers[name] = provider
        log.info("Registered provider: %s", name)
        return True

    def unregister_provider(self, name: str) -> None:
        """Unregister a ChainProvider implementation.

        Args:
            name: Provider identifier to remove
        """
        if name not in self.providers:
            log.debug("Provider %s not in registry", name)
            return

        del self.providers[name]
        log.info("Unregistered provider: %s", name)

    def get_provider(self, name: str) -> Optional[ChainProvider]:
        """Retrieve a registered provider by name.

        Args:
            name: Provider identifier

        Returns:
            ChainProvider instance or None if not found
        """
        return self.providers.get(name)

    def get_providers(self) -> Dict[str, ChainProvider]:
        """Get all registered providers.

        Returns:
            Dictionary mapping provider names to ChainProvider instances
        """
        return self.providers.copy()

    def register_message_handler(self, name: str, handler: MessageHandler) -> bool:
        """Register a MessageHandler implementation.

        Args:
            name: Unique identifier for the handler (typically cog name).
            handler: MessageHandler implementation instance.

        Returns:
            bool: True if registration succeeded.

        Example:
            # In an ExtensionCog's on_langcore_cog_add listener:
            handler = MyCustomHandler(self)
            langcore_cog.register_message_handler(self.qualified_name, handler)
        """
        if not isinstance(handler, MessageHandler):
            log.warning("Handler registration failed for %s: not a MessageHandler instance", name)
            return False

        existing = self.message_handlers.get(name)
        if existing is handler:
            log.debug("Handler %s already registered with same instance; skipping", name)
            return True

        if existing is not None:
            log.warning("Handler %s already registered, overwriting", name)

        self.message_handlers[name] = handler
        log.info("Registered message handler: %s", name)
        return True

    def unregister_message_handler(self, name: str) -> None:
        """Unregister a MessageHandler implementation.

        Args:
            name: Handler identifier to remove.
        """
        if name not in self.message_handlers:
            log.debug("Handler %s not in registry", name)
            return

        del self.message_handlers[name]
        log.info("Unregistered message handler: %s", name)

    def get_message_handler(self, name: str) -> Optional[MessageHandler]:
        """Retrieve a registered message handler by name.

        Args:
            name: Handler identifier.

        Returns:
            MessageHandler instance or None if not found.
        """
        return self.message_handlers.get(name)

    def get_message_handlers(self) -> Dict[str, MessageHandler]:
        """Get all registered message handlers.

        Returns:
            Dictionary mapping handler names to MessageHandler instances.
        """
        return self.message_handlers.copy()

    def register_chain_store(self, store: ChainStore) -> bool:
        """Register a ChainStore implementation."""
        if not isinstance(store, ChainStore):
            log.warning("Chain store registration failed: not a ChainStore instance")
            return False

        if self.chain_store is store:
            log.debug("Chain store already registered with same instance; skipping")
            return True

        if self.chain_store is not None:
            log.warning("Chain store already registered, overwriting existing store")

        self.chain_store = store
        log.info("Registered chain store: %s", type(store).__name__)
        return True

    def unregister_chain_store(self) -> None:
        """Unregister the current ChainStore implementation."""
        if self.chain_store is None:
            log.debug("No chain store registered; skipping unregister")
            return

        log.info("Unregistered chain store: %s", type(self.chain_store).__name__)
        self.chain_store = None

    def get_store(self) -> ChainStore:
        """Get the registered ChainStore or raise if missing."""
        if self.chain_store is None:
            raise RuntimeError(
                "No ChainStore registered. Load the qdrant cog or another store implementation."
            )
        return self.chain_store

    async def cog_load(self) -> None:
        """Initialize langcore and discover existing providers."""
        import asyncio

        asyncio.create_task(self._init_providers())

    async def _init_providers(self):
        try:
            await self.bot.wait_until_red_ready()
        except asyncio.CancelledError:
            log.info("LangCore init cancelled during shutdown")
            return
        # existing discovery loop
        for cog_name, cog in self.bot.cogs.items():
            if isinstance(cog, ChainProvider):
                self.register_provider(cog_name, cog)
                log.info("Discovered provider: %s", cog_name)
            if isinstance(cog, ChainStore):
                self.register_chain_store(cog)
                log.info("Discovered chain store: %s", cog_name)
        self.bot.dispatch("langcore_cog_add", self)
        log.info("LangCore initialized with %d providers", len(self.providers))

    async def cog_unload(self) -> None:
        """Clean up when langcore is unloaded."""
        self.bot.dispatch("langcore_cog_remove")
        log.info("LangCore unloaded")

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
        channel_mention = "None"
        if config.channel_id:
            channel = ctx.guild.get_channel(config.channel_id)
            channel_mention = channel.mention if channel else f"<#{config.channel_id}>"

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
        embed.add_field(
            name="Listener Settings",
            value=(
                f"Assistant Channel: {channel_mention if config.channel_id else 'None'}\n"
                f"Listen Channels: {len(config.listen_channels)}\n"
                f"Mention Respond: {config.mention_respond}\n"
                f"Min Length: {config.min_length}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Classifier",
            value=(
                f"Enabled: {config.use_classifier}\n"
                f"Model: `{config.classifier_model}`"
            ),
            inline=False,
        )

        await ctx.send(embed=embed)

    @langcore_config.command(name="providers")
    async def view_providers(self, ctx: commands.Context):
        """List all registered ChainProvider implementations."""
        if not self.providers:
            await ctx.send("No providers registered.")
            return

        embed = discord.Embed(
            title="Registered Providers",
            color=discord.Color.green(),
        )

        for name, provider in self.providers.items():
            provider_type = type(provider).__name__
            embed.add_field(
                name=name,
                value=f"Type: `{provider_type}`\nModule: `{provider.__module__}`",
                inline=False,
            )

        await ctx.send(embed=embed)

    @langcore_config.command(name="handlers")
    async def view_handlers(self, ctx: commands.Context):
        """List all registered MessageHandler implementations."""
        if not self.message_handlers:
            await ctx.send("No message handlers registered.")
            return

        embed = discord.Embed(
            title="Registered Message Handlers",
            color=discord.Color.purple(),
        )

        for name, handler in self.message_handlers.items():
            handler_type = type(handler).__name__
            embed.add_field(
                name=name,
                value=f"Type: `{handler_type}`\nModule: `{handler.__module__}`",
                inline=False,
            )

        await ctx.send(embed=embed)

    @langcore_config.command(name="store")
    async def view_store(self, ctx: commands.Context):
        """Show the registered ChainStore implementation."""
        if self.chain_store is None:
            await ctx.send("No chain store registered.")
            return

        store = self.chain_store
        embed = discord.Embed(
            title="Registered Chain Store",
            color=discord.Color.orange(),
        )
        embed.add_field(
            name=type(store).__name__,
            value=(
                f"Module: `{store.__module__}`\n"
                f"Cog: `{store.qualified_name if hasattr(store, 'qualified_name') else 'N/A'}`"
            ),
            inline=False,
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

    @langcore_config.group(name="channel", invoke_without_command=True)
    async def channel_group(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        """Set the dedicated assistant channel where the bot always responds."""
        if channel is None:
            await ctx.send("Please provide a channel, or use `langcore channel clear`.")
            return
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await ctx.send(f"Assistant channel set to {channel.mention}.")

    @channel_group.command(name="clear")
    async def channel_clear(self, ctx: commands.Context):
        """Clear the dedicated assistant channel."""
        await self.config.guild(ctx.guild).channel_id.set(None)
        await ctx.send("Assistant channel cleared.")

    @langcore_config.group(name="listenchannels")
    async def listenchannels_group(self, ctx: commands.Context):
        """Manage additional channels to listen in."""
        pass

    @listenchannels_group.command(name="add")
    async def listenchannels_add(self, ctx: commands.Context, channel: discord.TextChannel):
        """Add a channel to the listen list."""
        async with self.config.guild(ctx.guild).listen_channels() as listen_channels:
            if channel.id in listen_channels:
                await ctx.send(f"{channel.mention} is already in the listen list.")
                return
            listen_channels.append(channel.id)
        await ctx.send(f"Added {channel.mention} to the listen list.")

    @listenchannels_group.command(name="remove")
    async def listenchannels_remove(self, ctx: commands.Context, channel: discord.TextChannel):
        """Remove a channel from the listen list."""
        async with self.config.guild(ctx.guild).listen_channels() as listen_channels:
            if channel.id not in listen_channels:
                await ctx.send(f"{channel.mention} is not in the listen list.")
                return
            listen_channels.remove(channel.id)
        await ctx.send(f"Removed {channel.mention} from the listen list.")

    @listenchannels_group.command(name="list")
    async def listenchannels_list(self, ctx: commands.Context):
        """Show all listen channels."""
        listen_channels = await self.config.guild(ctx.guild).listen_channels()
        if not listen_channels:
            await ctx.send("No listen channels configured.")
            return

        lines: List[str] = []
        for channel_id in listen_channels:
            channel = ctx.guild.get_channel(channel_id)
            lines.append(channel.mention if channel else f"<#{channel_id}> ({channel_id})")

        message = "Listen Channels:\n" + "\n".join(f"- {line}" for line in lines)
        for page in pagify(message, delims=["\n"], page_length=1900):
            await ctx.send(page)

    @langcore_config.command(name="mentionrespond")
    async def toggle_mention_respond(self, ctx: commands.Context):
        """Toggle whether the bot responds when mentioned or replied to."""
        current = await self.config.guild(ctx.guild).mention_respond()
        await self.config.guild(ctx.guild).mention_respond.set(not current)
        await ctx.send(f"Mention respond set to {not current}.")

    @langcore_config.command(name="minlength")
    async def set_min_length(self, ctx: commands.Context, number: int):
        """Set the minimum message length to process (default: 3)."""
        if number < 1:
            await ctx.send("Minimum length must be 1 or greater.")
            return
        await self.config.guild(ctx.guild).min_length.set(number)
        await ctx.send(f"Minimum message length set to {number}.")

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

    @langcore_config.group(name="classifier")
    async def classifier_group(self, ctx: commands.Context):
        """Manage classifier settings for auto-reply gating."""
        pass

    @classifier_group.command(name="toggle")
    async def classifier_toggle(self, ctx: commands.Context):
        """Enable or disable the classifier for auto-reply gating."""
        current = await self.config.guild(ctx.guild).use_classifier()
        await self.config.guild(ctx.guild).use_classifier.set(not current)
        status = "enabled" if not current else "disabled"
        await ctx.send(f"Classifier has been {status} for this server.")

    @classifier_group.command(name="model")
    async def classifier_model_set(self, ctx: commands.Context, model_name: str):
        """Set the classifier model (e.g., llama3.2:1b, gemma3).

        Args:
            model_name: Name of the model to use for classification decisions.
        """
        await self.config.guild(ctx.guild).classifier_model.set(model_name)
        await ctx.send(f"Classifier model set to `{model_name}`.")

    @classifier_group.command(name="settings")
    async def classifier_settings(self, ctx: commands.Context):
        """View current classifier configuration."""
        config = await self.get_guild_config(ctx.guild.id)

        embed = discord.Embed(
            title="Classifier Configuration",
            color=discord.Color.purple(),
        )
        embed.add_field(
            name="Status",
            value=f"Enabled: {config.use_classifier}",
            inline=False,
        )
        embed.add_field(
            name="Model",
            value=f"`{config.classifier_model}`",
            inline=False,
        )
        embed.add_field(
            name="Description",
            value=(
                "The classifier gates auto-replies in the assistant channel. "
                "It decides whether to RESPOND, IGNORE, or END conversations."
            ),
            inline=False,
        )

        await ctx.send(embed=embed)

    @langcore_config.command(name="convostats")
    @commands.guild_only()
    async def convostats(self, ctx: commands.Context, user: Optional[discord.Member] = None):
        """View conversation statistics for this channel."""
        user = user or ctx.author
        conversation = self.conversation_manager.get_conversation(
            user.id,
            ctx.channel.id,
            ctx.guild.id,
        )
        config = await self.get_guild_config(ctx.guild.id)

        message_count = len(conversation.messages)
        estimated_tokens = (
            sum(len(str(msg.get("content", ""))) for msg in conversation.messages) // 4
            if conversation.messages
            else 0
        )
        tool_usage_count = sum(
            1 for msg in conversation.messages if isinstance(msg, dict) and msg.get("role") == "tool"
        )
        max_retention = config.get_user_max_retention(user)
        _max_time = config.get_user_max_time(user)
        is_expired = conversation.is_expired(config.max_retention_time)

        embed = discord.Embed(
            title="Conversation Stats",
            color=discord.Color.blue(),
        )
        embed.add_field(name="Channel", value=ctx.channel.mention, inline=False)
        embed.add_field(name="Messages", value=f"{message_count}/{max_retention}", inline=True)
        embed.add_field(name="Estimated Tokens", value=f"{estimated_tokens} (approx.)", inline=True)
        embed.add_field(name="Tool Calls", value=f"{tool_usage_count}", inline=True)
        embed.add_field(name="Expired", value=f"{is_expired}", inline=True)

        if conversation.last_updated:
            last_updated_dt = datetime.utcfromtimestamp(conversation.last_updated)
            embed.add_field(
                name="Last Updated",
                value=f"<t:{int(last_updated_dt.timestamp())}:F>",
                inline=False,
            )
        else:
            embed.add_field(name="Last Updated", value="Never", inline=False)

        await ctx.send(embed=embed)

        if conversation.system_prompt_override:
            await ctx.send(
                file=text_to_file(
                    conversation.system_prompt_override,
                    filename="system_prompt_override.txt",
                )
            )

    @langcore_config.command(name="clearconvo")
    @commands.guild_only()
    async def clearconvo(self, ctx: commands.Context):
        """Reset your conversation for this channel."""
        reset = self.conversation_manager.reset_conversation(
            ctx.author.id,
            ctx.channel.id,
            ctx.guild.id,
        )
        if reset:
            await ctx.send("Your conversation in this channel has been reset!")
        else:
            await ctx.send("No conversation found")

    @langcore_config.command(name="convoprompt")
    @commands.guild_only()
    async def convoprompt(self, ctx: commands.Context, *, prompt: Optional[str] = None):
        """Set or clear a system prompt override for this conversation."""
        if ctx.message.attachments:
            attachment = ctx.message.attachments[0]
            is_text_file = False
            if attachment.content_type and attachment.content_type.startswith("text/"):
                is_text_file = True
            elif attachment.filename.lower().endswith((".txt", ".md", ".prompt")):
                is_text_file = True

            if is_text_file:
                try:
                    prompt = (await attachment.read()).decode()
                except UnicodeDecodeError:
                    await ctx.send("Could not read the attached file as text (decode error).")
                    return

        if prompt is not None and len(prompt) > 10000:
            await ctx.send("Warning: prompt is longer than 10,000 characters.")

        conversation = self.conversation_manager.get_conversation(
            ctx.author.id,
            ctx.channel.id,
            ctx.guild.id,
        )
        conversation.system_prompt_override = prompt
        conversation.refresh()

        if prompt is not None:
            await ctx.send("System prompt has been set for this conversation!")
        else:
            await ctx.send("System prompt has been removed for this conversation!")

    @commands.command(name="chat", aliases=["ask"])
    @commands.guild_only()
    @commands.cooldown(1, 6, commands.BucketType.user)
    async def chat(self, ctx: commands.Context, *, question: str):
        """Chat with the AI assistant using the configured provider.

        Conversations are per-user per-channel, maintaining separate context
        for each channel you interact with the bot in.

        Args:
            question: Your message or question for the AI assistant.
        """
        config = await self.get_guild_config(ctx.guild.id)
        if not config.enabled:
            await ctx.send("LangCore is disabled for this server.")
            return

        conversation = self.conversation_manager.get_conversation(
            ctx.author.id,
            ctx.channel.id,
            ctx.guild.id,
        )

        max_retention = config.get_user_max_retention(ctx.author)
        max_retention_time = config.get_user_max_time(ctx.author)
        conversation.cleanup(max_retention, max_retention_time)
        conversation.update_messages(question, "user")
        conversation.cleanup(max_retention, max_retention_time)

        provider = self.get_provider("ollama")
        if not provider:
            await ctx.send("No AI provider is available. Please load the ollama cog.")
            return

        functions, callbacks = await self.hub.get_functions(
            guild_id=ctx.guild.id,
            guild_config=config,
            member=ctx.author,
            permission_filter=True,
        )

        async with ctx.typing():
            try:
                response = await self.conversation_manager.agent_chat(
                    key=(ctx.author.id, ctx.channel.id, ctx.guild.id),
                    provider=provider,
                    functions=functions,
                    callbacks=callbacks,
                    guild_id=ctx.guild.id,
                    member_id=ctx.author.id,
                    config=config,
                )
            except commands.UserFeedbackCheckFailure as e:
                # Provider raised user-facing error
                await ctx.send(str(e))
                return
            except Exception as e:
                log.exception("Unexpected error during chat for guild %s", ctx.guild.id)
                await ctx.send(f"An unexpected error occurred: {e}")
                return

        if len(response) <= 2000:
            await ctx.send(response)
        else:
            for page in pagify(response, delims=["\n", " "], page_length=1900):
                await ctx.send(page)

        # Handle rich media responses from tools
        conversation = self.conversation_manager.get_conversation(
            ctx.author.id,
            ctx.channel.id,
            ctx.guild.id,
        )

    @commands.Cog.listener()
    async def on_message_without_command(self, message: discord.Message):
        if not message or not message.guild or message.author.bot:
            return
        if not message.content:
            return

        guild = message.guild
        me = guild.me
        if not me:
            return
        if hasattr(message.channel, "permissions_for"):
            perms = message.channel.permissions_for(me)
            if not perms.send_messages:
                return

        config = await self.get_guild_config(guild.id)
        if not config.enabled:
            return

        bot_user = self.bot.user
        if not bot_user:
            return

        content = message.content.strip()
        ignored_prefixes = (",", ".", "+", "!", "-", "><", "?", "%", "^", "&", "*", "_")
        if any(content.startswith(prefix) for prefix in ignored_prefixes):
            return

        if self.is_blacklisted(message, config.blacklist):
            return

        if len(content) < config.min_length:
            return

        bot_mentioned = any(u.id == bot_user.id for u in message.mentions)

        if message.reference and message.reference.message_id:
            referenced: Optional[discord.Message] = None
            if isinstance(message.reference.resolved, discord.Message):
                referenced = message.reference.resolved
            else:
                try:
                    referenced = await message.channel.fetch_message(message.reference.message_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    referenced = None
            if referenced and referenced.author and referenced.author.id == bot_user.id:
                bot_mentioned = True

        should_respond = False
        if config.channel_id and message.channel.id == config.channel_id:
            should_respond = True
        elif message.channel.id in config.listen_channels:
            should_respond = True
        elif bot_mentioned and config.mention_respond:
            should_respond = True

        if not should_respond:
            return

        provider = self.get_provider("ollama")
        if not provider:
            await message.reply("No AI provider is available. Please load the ollama cog.")
            return

        # Classifier gatekeeper for assistant channel
        if config.use_classifier and message.channel.id == config.channel_id:
            log.debug(
                "Classifier enabled for channel %d, invoking decision logic",
                message.channel.id
            )

            decision = await self.classifier_manager.classify(
                channel_id=message.channel.id,
                message=message,
                provider=provider,
                config=config,
                guild_id=guild.id,
            )

            if decision == "IGNORE":
                log.debug(
                    "Classifier decision: IGNORE (channel %d, author %s)",
                    message.channel.id,
                    message.author.name
                )
                return

            elif decision == "END":
                reset_count = self.conversation_manager.reset_channel_conversations(
                    channel_id=message.channel.id,
                    guild_id=guild.id,
                )
                self.classifier_manager.clear_buffer(message.channel.id)
                log.info(
                    "Classifier decision: END (channel %d, reset %d conversations, cleared buffer)",
                    message.channel.id,
                    reset_count
                )
                return

            elif decision == "RESPOND":
                # Inject buffer context into message content
                buffer = self.classifier_manager.get_buffer(message.channel.id)
                if buffer and len(buffer) > 1:  # More than just current message
                    buffer_context = self.classifier_manager._format_buffer_context(buffer)
                    content = f"[Recent conversation context:\n{buffer_context}]\n\nCurrent message: {content}"
                    log.debug(
                        "Classifier decision: RESPOND (channel %d, injected %d buffered messages)",
                        message.channel.id,
                        len(buffer) - 1  # Exclude current message
                    )
                else:
                    log.debug(
                        "Classifier decision: RESPOND (channel %d, no buffer context to inject)",
                        message.channel.id
                    )

        conversation = self.conversation_manager.get_conversation(
            message.author.id,
            message.channel.id,
            guild.id,
        )

        max_retention = config.get_user_max_retention(message.author)
        max_retention_time = config.get_user_max_time(message.author)
        conversation.cleanup(max_retention, max_retention_time)
        conversation.update_messages(content, "user")
        conversation.cleanup(max_retention, max_retention_time)

        functions, callbacks = await self.hub.get_functions(
            guild_id=guild.id,
            guild_config=config,
            member=message.author,
            permission_filter=True,
        )

        async with message.channel.typing():
            try:
                response = await self.conversation_manager.agent_chat(
                    key=(message.author.id, message.channel.id, guild.id),
                    provider=provider,
                    functions=functions,
                    callbacks=callbacks,
                    guild_id=guild.id,
                    member_id=message.author.id,
                    config=config,
                )
            except commands.UserFeedbackCheckFailure as e:
                await message.reply(str(e))
                return
            except Exception as e:  # noqa: BLE001
                log.exception("Unexpected error during listener chat for guild %s", guild.id)
                await message.reply(f"An unexpected error occurred: {e}")
                return

        if len(response) <= 2000:
            await message.reply(response, mention_author=False)
        else:
            first = True
            for page in pagify(response, delims=["\n", " "], page_length=1900):
                if first:
                    await message.reply(page, mention_author=False)
                    first = False
                else:
                    await message.channel.send(page)

        # Handle rich media responses from tools
        conversation = self.conversation_manager.get_conversation(
            message.author.id,
            message.channel.id,
            guild.id,
        )

    @commands.Cog.listener()
    async def on_cog_add(self, cog: commands.Cog):
        """Notify provider cogs when langcore is available."""
        log.info("Cog added while langcore loaded: %s", cog.qualified_name)
        event = "on_langcore_cog_add"
        funcs = [func for event_name, func in cog.get_listeners() if event_name == event]
        for func in funcs:
            self.bot._schedule_event(func, event, self)

    @commands.Cog.listener()
    async def on_cog_remove(self, cog: commands.Cog):
        """Clean up when provider cogs are removed."""
        log.info("Cog removed while langcore loaded: %s", cog.qualified_name)
        cog_name = cog.qualified_name
        self.unregister_provider(cog_name)
        self.hub.unregister_cog(cog_name)
        self.unregister_message_handler(cog_name)
        if isinstance(cog, ChainStore):
            self.unregister_chain_store()

    async def red_delete_data_for_user(self, *, requester: RequestType, user_id: int) -> None:
        # TODO: Replace this with the proper end user data removal handling.
        super().red_delete_data_for_user(requester=requester, user_id=user_id)

    def is_blacklisted(self, message: discord.Message, blacklist: List[int]) -> bool:
        """Check if message author, roles, or channel are blacklisted."""
        if message.author.id in blacklist:
            return True
        if message.channel.id in blacklist:
            return True
        if hasattr(message.author, "roles"):
            for role in message.author.roles:
                if role.id in blacklist:
                    return True
        return False
