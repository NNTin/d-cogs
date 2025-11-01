from typing import Any, Dict

import discord
from redbot.core import commands

from ...utils.dashboard import DashboardIntegration
from ..common_styles import get_common_styles


class SimulationPage(DashboardIntegration):
    """Simple simulation page that embeds the external d-zone iframe.

    This page returns a small HTML payload with an iframe and JS that appends
    a timestamp to the iframe src to avoid caching.
    """

    bot: commands.Bot
    config: Any

    async def dashboard_simulation(
        self, user: discord.User, guild: discord.Guild, **kwargs
    ) -> Dict[str, Any]:
        """Return the simulation page content.

        Any guild member may access this page. It embeds an iframe pointing to
        the ambient simulation and appends a timestamp query param to the URL
        on DOMContentLoaded.
        """
        # Load socketURL from guild config, fallback to global config, then to default
        try:
            config = self.config.guild(guild)
            socket_url = await config.socketURL()
            if not socket_url:
                # Try global config
                socket_url = await self.config.socketURL()
            if not socket_url:
                # Final fallback to default
                socket_url = "wss://localhost:3000"
        except Exception:
            # If config access fails, use default
            socket_url = "wss://localhost:3000"

        html_content = f"""
        <style>
            {get_common_styles()}
            .simulation-iframe {{
                width: 100%;
                height: 800px;
                border: none;
                border-radius: 5px;
                overflow: hidden;
            }}
        </style>

        <div class="dworld-config">
            <h1>Simulation for {{{{ guild_name }}}}</h1>
            <p class="explanation-text">This is the d-zone ambient life simulation. The iframe below loads the live simulation and appends a timestamp to force fresh connections.</p>

            <iframe id="main-iframe" class="simulation-iframe" src="about:blank" title="D-Zone Simulation"></iframe>

            <script>
                (function() {{
                    var iframe = document.getElementById('main-iframe');
                    if (!iframe) return;
                    var t = new Date().getTime();
                    var src = 'https://nntin.xyz/d-zone?s=repos&socketURL={socket_url}&t=' + t;
                    iframe.src = src;
                }})();
            </script>
        </div>
        """

        return {
            "status": 0,
            "web_content": {
                "source": html_content,
                "guild_name": guild.name,
                "user_name": user.name,
            },
        }
