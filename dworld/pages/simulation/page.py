from typing import Any, Dict

import discord
from redbot.core import commands

from ..common_styles import get_common_styles


class SimulationPage:
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
        # Get guild config once for reuse
        guild_config = self.config.guild(guild)

        # Load socketURL from guild config, fallback to global config, then to default
        try:
            socket_url = await guild_config.socketURL()
            if not socket_url:
                # Try global config
                socket_url = await self.config.socketURL()
            if not socket_url:
                # Final fallback to default
                socket_url = "wss://localhost:3000"
        except Exception:
            # If config access fails, use default
            socket_url = "wss://localhost:3000"

        # Load selectedVersion from guild config for hash-based routing
        try:
            selected_version = await guild_config.selectedVersion()
        except Exception:
            # If config access fails, use None (no version hash)
            selected_version = None

        # Sanitize selected_version to avoid double-hash and whitespace issues
        if selected_version:
            # Convert to string, strip whitespace, and remove any leading #
            selected_version = str(selected_version).strip().lstrip("#")

        # Construct the version hash fragment
        if selected_version:
            version_hash = f"#{selected_version}"
        else:
            version_hash = ""

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
                    var socketURL = encodeURIComponent('{socket_url}');
                    var src = 'https://nntin.github.io/d-zone?socketURL=' + socketURL + '&t=' + t + '{version_hash}';
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
