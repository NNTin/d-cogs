"""
Dashboard Integration Utilities for d-cogs

This module provides reusable utilities for integrating cogs with the Red-DiscordBot
dashboard (AAA3A-cogs Dashboard). It eliminates code duplication by providing a
standard decorator pattern for dashboard integration.

Usage:
    1. Import the utilities in your cog:
       from dashboard_utils import dashboard_page, DashboardIntegration

    2. Use DashboardManager component for dashboard integration:
       class MyCog(commands.Cog):
           def __init__(self, bot):
               self.dashboard_manager = DashboardManager(self)
           ...

    3. Use the @dashboard_page decorator on methods you want to expose:
       @dashboard_page(name="settings", description="Configure settings")
       async def settings_page(self, user, guild, **kwargs):
           ...

    4. Use helper functions to extract form utilities:
       @dashboard_page(name="config", description="Configuration page")
       async def config_page(self, user, guild, **kwargs):
           Form, DpyObjectConverter, Pagination = get_form_helpers(kwargs)
           # Now use Form to create forms, etc.

    5. Use configuration helpers to manage guild/global config:
       config = await get_guild_config(self, guild)
       await update_config_section(self, section, {"key": "value"})

Example:
    from redbot.core import commands
    from dashboard_utils import dashboard_page, DashboardIntegration, get_form_helpers

    class MyCog(commands.Cog):
        def __init__(self, bot):
            self.bot = bot
            self.dashboard_manager = DashboardManager(self)

        @dashboard_page(name="config", description="Configuration page")
        async def config_page(self, user, guild, **kwargs):
            Form, DpyObjectConverter, _ = get_form_helpers(kwargs)
            return {"status": "ok", "message": "Hello from dashboard!"}

Reference:
    This implementation follows the patterns established in:
    - ben-cogs/skysearch/dashboard/dashboard_integration.py
    - maxcogs/autopublisher/dashboard_integration.py
    - AAA3A-cogs/embedutils/dashboard_integration.py

    Works with the AAA3A-cogs Dashboard cog for Red-DiscordBot.
"""

from typing import Any, Callable, Dict, Tuple

from redbot.core import commands

# Public API surface
__all__ = [
    "dashboard_page",
    "DashboardIntegration",
    "get_form_helpers",
    "get_guild_config",
    "get_global_config",
    "update_config_section",
]


def dashboard_page(*args, **kwargs) -> Callable:
    """
    Decorator for marking methods as dashboard pages.

    This decorator stores its parameters in the function's __dashboard_decorator_params__
    attribute, which is later used by the dashboard's RPC handler to register the page.

    Args:
        *args: Positional arguments passed to the decorator (e.g., name, description)
        **kwargs: Keyword arguments passed to the decorator

    Returns:
        A decorator function that marks the method for dashboard integration

    Example:
        @dashboard_page(name="settings", description="Configure bot settings")
        async def settings_page(self, user, guild, **kwargs):
            return {"template": "settings.html", "data": {...}}
    """

    def decorator(func: Callable) -> Callable:
        # Store decorator parameters in the function's attributes
        # The dashboard RPC handler will read this during registration
        func.__dashboard_decorator_params__ = (args, kwargs)
        return func

    return decorator


def get_form_helpers(kwargs: Dict[str, Any]) -> Tuple[Any, Any, Any]:
    """
    Extract form generation utilities from dashboard page kwargs.

    The AAA3A-cogs Dashboard provides form utilities via the kwargs passed to
    dashboard page handlers. This helper extracts them for convenient use.

    Args:
        kwargs: The kwargs dict passed to a dashboard page handler

    Returns:
        A tuple of (Form, DpyObjectConverter, Pagination) where:
        - Form: FlaskForm class for creating web forms
        - DpyObjectConverter: Utility for converting Discord objects
        - Pagination: Utility for paginating large datasets

    Example:
        @dashboard_page(name="settings", description="Configure settings")
        async def settings_page(self, user, guild, **kwargs):
            Form, DpyObjectConverter, Pagination = get_form_helpers(kwargs)
            # Use Form to create a settings form
            class SettingsForm(Form):
                option = StringField("Option")
    """
    Form = kwargs.get("Form")
    DpyObjectConverter = kwargs.get("DpyObjectConverter")
    Pagination = kwargs.get("Pagination")

    return Form, DpyObjectConverter, Pagination


async def get_guild_config(cog: Any, guild: Any) -> Any:
    """
    Get the configuration section for a specific guild.

    This is a convenience wrapper around the standard Red config pattern
    for accessing guild-specific configuration.

    Args:
        cog: The cog instance (must have a `config` attribute)
        guild: The Discord guild object

    Returns:
        The guild configuration section

    Example:
        config = await get_guild_config(self, guild)
        passworded = await config.passworded()
    """
    if not hasattr(cog, "config"):
        raise AttributeError("Cog must have a 'config' attribute")

    return cog.config.guild(guild)


async def get_global_config(cog: Any) -> Any:
    """
    Get the global configuration section for the cog.

    This is a convenience wrapper around the standard Red config pattern
    for accessing global configuration.

    Args:
        cog: The cog instance (must have a `config` attribute)

    Returns:
        The global configuration section

    Example:
        config = await get_global_config(self)
        client_id = await config.client_id()
    """
    if not hasattr(cog, "config"):
        raise AttributeError("Cog must have a 'config' attribute")

    return cog.config


async def update_config_section(section: Any, updates: Dict[str, Any]) -> None:
    """
    Apply validated updates to a configuration section.

    This helper applies multiple configuration updates in a clean way,
    validating that each key exists in the config section before setting.

    Args:
        section: The config section (from get_guild_config or get_global_config)
        updates: Dictionary of config keys to update with their new values

    Raises:
        AttributeError: If a key in updates doesn't exist in the config section

    Example:
        guild_config = await get_guild_config(self, guild)
        await update_config_section(guild_config, {
            "passworded": True,
            "ignoreOfflineMembers": False
        })
    """
    for key, value in updates.items():
        if not hasattr(section, key):
            raise AttributeError(f"Config section does not have attribute '{key}'")

        # Get the config attribute and set the new value
        config_attr = getattr(section, key)
        await config_attr.set(value)


class DashboardIntegration:
    """
    Base class for dashboard integration (composition pattern preferred).

    Cogs that inherit from this class will automatically register themselves
    with the AAA3A-cogs Dashboard when it loads. The registration happens
    through the on_dashboard_cog_add listener, which is triggered when the
    dashboard dispatches its cog_add event.

    Usage:
        class MyCog(DashboardIntegration, commands.Cog):
            def __init__(self, bot):
                self.bot = bot
                super().__init__()

    Note: Using DashboardManager component directly is the recommended approach
    for new implementations.

    The dashboard will scan the cog for methods decorated with @dashboard_page
    and automatically register them as available pages in the web interface.
    """

    # Type hint for the bot attribute (for type checking purposes)
    bot: commands.Bot

    @commands.Cog.listener()
    async def on_dashboard_cog_add(self, dashboard_cog: commands.Cog) -> None:
        """
        Event listener that registers this cog with the dashboard.

        This method is automatically called when the AAA3A-cogs Dashboard
        dispatches the 'dashboard_cog_add' event during its cog_load().

        Args:
            dashboard_cog: The dashboard cog instance that dispatched the event

        Note:
            The dashboard's RPC third_parties_handler will scan this cog for
            methods decorated with @dashboard_page and register them.
        """
        # Defensive guard: check if dashboard_cog has the required attributes
        if not hasattr(dashboard_cog, "rpc"):
            return

        if not hasattr(dashboard_cog.rpc, "third_parties_handler"):
            return

        try:
            # Register this cog as a third-party integration with the dashboard
            # The dashboard's RPC handler will handle the actual registration logic
            dashboard_cog.rpc.third_parties_handler.add_third_party(self)
        except AttributeError:
            # Silently fail if the structure is not as expected
            pass
