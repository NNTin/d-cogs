"""
Dashboard Integration for D-World Cog

This module serves as a coordination layer that delegates to individual page classes
in the pages/ folder structure. It provides dashboard integration for the dworld cog,
allowing web-based configuration management through the AAA3A-cogs Dashboard.

The dashboard includes:
- Configuration page: Guild-level settings (password protection, offline member filtering)
  and global settings (OAuth2 credentials, static file path)
- Customization page: Member role colors and custom messages (with different forms
  for privileged users vs regular users)

Requirements:
- AAA3A-cogs Dashboard must be loaded for this integration to work
- Users must have appropriate permissions (mod+ for guild settings, owner for global settings)
"""

import typing

import discord
from redbot.core import commands

from .pages.configuration import ConfigurationPage
from .pages.customization import CustomizationPage
from .pages.debugsimulation import DebugSimulationPage
from .pages.simulation import SimulationPage
from .utils import DashboardIntegration, dashboard_page


class DWorldDashboardIntegration(DashboardIntegration):
    """
    Dashboard integration for the dworld cog.

    This class acts as a coordination layer that delegates to individual page classes,
    providing web-based configuration management for the dworld cog through multiple
    dashboard pages.
    """

    # Type hint for the bot attribute (for type checking purposes)
    bot: commands.Bot

    @dashboard_page(
        name="configuration",
        description="D-World Configuration (manage server)",
        methods=("GET", "POST"),
    )
    async def dashboard_guild_settings(
        self, user: discord.User, guild: discord.Guild, **kwargs
    ) -> typing.Dict[str, typing.Any]:
        """
        Dashboard page wrapper for D-World configuration.

        Delegates to the ConfigurationPage instance.

        Args:
            user: The Discord user accessing the dashboard
            guild: The Discord guild being configured
            **kwargs: Additional arguments provided by the dashboard (includes Form, etc.)

        Returns:
            Dictionary with status and web_content for rendering
        """
        # Lazy initialization: create configuration page if it doesn't exist
        if not hasattr(self, "configuration_page"):
            self.configuration_page = ConfigurationPage()

        # Ensure the configuration page has access to current bot and config
        self.configuration_page.bot = self.bot
        self.configuration_page.config = self.config

        # Delegate to the configuration page
        return await self.configuration_page.dashboard_guild_settings(
            user, guild, **kwargs
        )

    @dashboard_page(
        name="customization",
        description="Member Customization (any guild member)",
        methods=("GET", "POST"),
    )
    async def dashboard_member_customization(
        self, user: discord.User, guild: discord.Guild, **kwargs
    ) -> typing.Dict[str, typing.Any]:
        """
        Dashboard page wrapper for member customization.

        Delegates to the CustomizationPage instance.

        Args:
            user: The Discord user accessing the dashboard
            guild: The Discord guild being configured
            **kwargs: Additional arguments provided by the dashboard (includes Form, etc.)

        Returns:
            Dictionary with status and web_content for rendering
        """
        # Lazy initialization: create customization page if it doesn't exist
        if not hasattr(self, "customization_page"):
            self.customization_page = CustomizationPage()

        # Ensure the customization page has access to current bot, config, and server
        self.customization_page.bot = self.bot
        self.customization_page.config = self.config
        self.customization_page.server = self.server

        # Delegate to the customization page
        return await self.customization_page.dashboard_member_customization(
            user, guild, **kwargs
        )

    @dashboard_page(
        name="simulation",
        description="d-zone: ambient life simulation",
        methods=("GET",),
    )
    async def dashboard_simulation(
        self, user: discord.User, guild: discord.Guild, **kwargs
    ) -> typing.Dict[str, typing.Any]:
        """
        Dashboard page wrapper for the lightweight simulation page.

        Any guild member may access this page. Delegates to the SimulationPage
        instance and ensures it has access to `bot` and `config` attributes.
        """
        # Lazy initialization: create simulation page if it doesn't exist
        if not hasattr(self, "simulation_page"):
            self.simulation_page = SimulationPage()

        # Ensure the simulation page has access to current bot and config
        self.simulation_page.bot = self.bot
        self.simulation_page.config = self.config

        # Delegate to the simulation page
        return await self.simulation_page.dashboard_simulation(user, guild, **kwargs)

    @dashboard_page(
        name="debugsimulation",
        description="For people with manage servers permission: choose which version you want to show to your server members",
        methods=("GET", "POST"),
    )
    async def dashboard_debugsimulation(
        self, user: discord.User, guild: discord.Guild, **kwargs
    ) -> typing.Dict[str, typing.Any]:
        """
        Dashboard page wrapper for debug simulation version selection.

        Delegates to the DebugSimulationPage instance.

        Args:
            user: The Discord user accessing the dashboard
            guild: The Discord guild being configured
            **kwargs: Additional arguments provided by the dashboard (includes Form, etc.)

        Returns:
            Dictionary with status and web_content for rendering
        """
        # Lazy initialization: create debugsimulation page if it doesn't exist
        if not hasattr(self, "debugsimulation_page"):
            self.debugsimulation_page = DebugSimulationPage()

        # Ensure the debugsimulation page has access to current bot and config
        self.debugsimulation_page.bot = self.bot
        self.debugsimulation_page.config = self.config

        # Delegate to the debugsimulation page
        return await self.debugsimulation_page.dashboard_debugsimulation(
            user, guild, **kwargs
        )
