"""
Dashboard Integration Utilities for d-cogs

This module provides reusable utilities for integrating cogs with the Red-DiscordBot
dashboard (AAA3A-cogs Dashboard). It eliminates code duplication by providing a
standard decorator and mixin pattern for dashboard integration.

Usage:
    1. Import the utilities in your cog:
       from ..dashboard_utils import dashboard_page, DashboardIntegration

    2. Make your cog inherit from DashboardIntegration:
       class MyCog(DashboardIntegration, commands.Cog):
           ...

    3. Use the @dashboard_page decorator on methods you want to expose:
       @dashboard_page(name="settings", description="Configure settings")
       async def settings_page(self, user, guild, **kwargs):
           ...

Example:
    from redbot.core import commands
    from ..dashboard_utils import dashboard_page, DashboardIntegration

    class MyCog(DashboardIntegration, commands.Cog):
        def __init__(self, bot):
            self.bot = bot
            super().__init__()

        @dashboard_page(name="config", description="Configuration page")
        async def config_page(self, user, guild, **kwargs):
            return {"status": "ok", "message": "Hello from dashboard!"}

Reference:
    This implementation follows the patterns established in:
    - ben-cogs/skysearch/dashboard/dashboard_integration.py
    - maxcogs/autopublisher/dashboard_integration.py
    - AAA3A-cogs/embedutils/dashboard_integration.py
    
    Works with the AAA3A-cogs Dashboard cog for Red-DiscordBot.
"""

from typing import Any, Callable, Tuple, Dict
from redbot.core import commands


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


class DashboardIntegration:
    """
    Mixin class for dashboard integration.
    
    Cogs that inherit from this class will automatically register themselves
    with the AAA3A-cogs Dashboard when it loads. The registration happens
    through the on_dashboard_cog_add listener, which is triggered when the
    dashboard dispatches its cog_add event.
    
    Usage:
        class MyCog(DashboardIntegration, commands.Cog):
            def __init__(self, bot):
                self.bot = bot
                super().__init__()
    
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
        # Register this cog as a third-party integration with the dashboard
        # The dashboard's RPC handler will handle the actual registration logic
        dashboard_cog.rpc.third_parties_handler.add_third_party(self)
