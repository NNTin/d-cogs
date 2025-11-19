"""Dashboard manager component for D-World cog."""


class DashboardManager:
    """Manages dashboard registration functionality for D-World."""

    def __init__(self, cog):
        """Initialize the dashboard manager.

        Args:
            cog: The cog instance to register with the dashboard
        """
        self.cog = cog

    async def register_with_dashboard(self, dashboard_cog):
        """Register the cog with the dashboard.

        Args:
            dashboard_cog: The dashboard cog instance that dispatched the event
        """
        # Defensive guard: check if dashboard_cog has the required attributes
        if not hasattr(dashboard_cog, "rpc"):
            return

        if not hasattr(dashboard_cog.rpc, "third_parties_handler"):
            return

        try:
            # Register this cog as a third-party integration with the dashboard
            # The dashboard's RPC handler will handle the actual registration logic
            dashboard_cog.rpc.third_parties_handler.add_third_party(self.cog)
        except AttributeError:
            # Silently fail if the structure is not as expected
            pass
