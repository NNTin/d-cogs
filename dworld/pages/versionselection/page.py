"""Version Selection page for D-World dashboard integration."""

import typing
import aiohttp
import discord
import wtforms
from redbot.core import commands

from ...utils import get_form_helpers
from ..common_styles import get_common_styles


class VersionSelectionPage:
    """Version Selection page for D-World dashboard integration."""

    bot: commands.Bot
    config: typing.Any

    async def dashboard_versionselection(
        self, user: discord.User, guild: discord.Guild, **kwargs
    ) -> typing.Dict[str, typing.Any]:
        """Handle the version selection page for version selection.

        Args:
            user: The Discord user accessing the page
            guild: The guild being configured
            **kwargs: Additional arguments from the dashboard

        Returns:
            Dictionary containing page data and status
        """
        # Check permissions
        is_owner = user.id in self.bot.owner_ids

        if not (is_owner):
            return {
                "status": 0,
                "error_code": 403,
                "message": "You have to be owner to access this page.",
            }

        # Fetch available versions from GitHub Pages
        versions = []
        fetch_error = None
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://nntin.github.io/d-zone/versions.json",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        # Validate that the result is a list of strings
                        if isinstance(data, list) and all(
                            isinstance(v, str) for v in data
                        ):
                            versions = data
                        else:
                            fetch_error = "Invalid versions.json format: expected a list of strings"
                            versions = []
                    else:
                        fetch_error = (
                            f"Failed to fetch versions (HTTP {response.status})"
                        )
        except aiohttp.ClientError as e:
            fetch_error = f"Network error: {str(e)}"
        except Exception as e:
            fetch_error = f"Error fetching versions: {str(e)}"

        # Fallback if fetch failed
        if fetch_error:
            versions = []

        # Get form helpers
        Form, _, _ = get_form_helpers(kwargs)
        if Form is None:
            return {
                "status": 0,
                "error_code": 500,
                "message": "Form utilities not available",
            }

        # Define the form
        class VersionSelectionForm(Form):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, prefix="version_selection_", **kwargs)

            version_selector = wtforms.SelectField(
                "Select Version",
                choices=[],
                render_kw={"class": "form-select"},
            )
            submit = wtforms.SubmitField("Save Version")

        # Instantiate form
        version_form = VersionSelectionForm()

        # Populate choices
        choices = [("default", "Default (Latest)")]
        choices.extend([(version, version) for version in versions])
        version_form.version_selector.choices = choices

        # Compute valid option values for validation
        valid_choices = {choice[0] for choice in choices}

        # Handle form submission
        result_html = ""
        if version_form.validate_on_submit():
            try:
                selected = version_form.version_selector.data
                # Ensure selected value is in valid choices, otherwise use default
                if selected not in valid_choices:
                    selected = "default"
                selected_value = None if selected == "default" else selected
                await self.config.guild(guild).selectedVersion.set(selected_value)
                result_html = f"""
                <div style="background-color: #d4edda; color: #155724; padding: 15px; 
                     border: 1px solid #c3e6cb; border-radius: 5px; margin: 15px 0;">
                    <strong>Success!</strong> Version preference saved successfully.
                    {f"Selected version: {selected_value}" if selected_value else "Using default (latest) version"}
                </div>
                """
            except Exception as e:
                result_html = f"""
                <div style="background-color: #f8d7da; color: #721c24; padding: 15px; 
                     border: 1px solid #f5c6cb; border-radius: 5px; margin: 15px 0;">
                    <strong>Error!</strong> Failed to save version preference: {str(e)}
                </div>
                """

        # Load current configuration
        current_version = await self.config.guild(guild).selectedVersion()
        # Ensure the current version is in valid choices before setting form data
        if current_version and current_version in valid_choices:
            version_form.version_selector.data = current_version
        else:
            version_form.version_selector.data = "default"

        # Build HTML response
        common_styles = get_common_styles()

        # Show fetch error if any
        fetch_error_html = ""
        if fetch_error:
            fetch_error_html = f"""
            <div style="background-color: #fff3cd; color: #856404; padding: 15px; 
                 border: 1px solid #ffeeba; border-radius: 5px; margin: 15px 0;">
                <strong>Warning:</strong> {fetch_error}<br>
                Using cached/fallback version list.
            </div>
            """

        html_content = f"""
        <style>
            {common_styles}
        </style>

        <div class="dworld-config">
            <h1>Version Selection for {{{{ guild_name }}}}</h1>
            
            <p class="description">
                Choose which version you want to show to your server members in the simulation page.
            </p>

            {fetch_error_html}

            <div class="config-section">
                <h2>Current Configuration</h2>
                <div class="config-item">
                    <span class="config-label">Selected Version:</span>
                    <span class="config-value">
                        {{{{ current_version if current_version else "Default (Latest)" }}}}
                    </span>
                </div>
            </div>

            <div class="config-section">
                <h2>Version Selection</h2>
                {{{{ version_form|safe }}}}
            </div>

            {{{{ result_html|safe }}}}

            <div class="config-section">
                <h2>About This Setting</h2>
                <p class="description">
                    This setting controls which version of the D-Zone simulation is displayed 
                    to your server members. When set to "Default (Latest)", the simulation 
                    will use hash-based routing to load the latest available version. 
                    Selecting a specific version will pin the simulation to that version 
                    for your server.
                </p>
                <p class="description">
                    <strong>Note:</strong> This affects the iframe URL used in the simulation page.
                </p>
            </div>
        </div>
        """

        return {
            "status": 0,
            "web_content": {
                "source": html_content,
                "version_form": version_form,
                "result_html": result_html,
                "guild_name": guild.name,
                "current_version": current_version or "Default (Latest)",
            },
        }
