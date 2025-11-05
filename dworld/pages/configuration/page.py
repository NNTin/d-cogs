"""Configuration dashboard page for D-World."""

import typing

import discord
import wtforms
from redbot.core import commands

from ...utils import get_form_helpers
from ..common_styles import get_common_styles


class ConfigurationPage:
    """Dashboard page for D-World configuration settings."""

    # Type hints for attributes provided by parent cog
    bot: commands.Bot
    config: typing.Any

    async def dashboard_guild_settings(
        self, user: discord.User, guild: discord.Guild, **kwargs
    ) -> typing.Dict[str, typing.Any]:
        """
        Dashboard page for D-World configuration.

        Handles both guild-level settings (password protection, offline member filtering)
        and global settings (OAuth2 credentials, static file path).

        Args:
            user: The Discord user accessing the dashboard
            guild: The Discord guild being configured
            **kwargs: Additional arguments provided by the dashboard (includes Form, etc.)

        Returns:
            Dictionary with status and web_content for rendering
        """
        # Permission checks
        is_owner = user.id in self.bot.owner_ids
        member = guild.get_member(user.id)
        is_mod = False

        if member:
            is_mod = member.guild_permissions.manage_guild

        # User must be either owner or mod to access this page
        if not is_owner and not is_mod:
            return {
                "status": 0,
                "error_code": 403,
                "message": "You must be a moderator or bot owner to access this page.",
            }

        # Load current configuration
        try:
            # Guild configuration
            config = self.config.guild(guild)
            passworded = await config.passworded()
            ignoreOfflineMembers = await config.ignoreOfflineMembers()

            # Global configuration
            client_id = await self.config.client_id()
            client_secret = await self.config.client_secret()
            static_file_path = await self.config.static_file_path()
            socketURL = await self.config.socketURL()
        except Exception as e:
            return {
                "status": 0,
                "error_code": 500,
                "message": f"Failed to load configuration: {str(e)}",
            }

        # Extract form utilities from kwargs
        Form, _, _ = get_form_helpers(kwargs)

        # Defensive check: ensure Form utilities are available
        if not Form:
            return {
                "status": 0,
                "error_code": 500,
                "message": "Form utilities are unavailable. Ensure the dashboard is properly configured.",
            }

        # Define GuildSettingsForm class
        class GuildSettingsForm(Form):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, prefix="guild_settings_", **kwargs)

            passworded = wtforms.BooleanField("Password Protection")
            ignoreOfflineMembers = wtforms.BooleanField("Ignore Offline Members")
            submit = wtforms.SubmitField("Save Guild Settings")

        # Define GlobalSettingsForm class
        class GlobalSettingsForm(Form):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, prefix="global_settings_", **kwargs)

            client_id = wtforms.StringField(
                "OAuth2 Client ID", validators=[wtforms.validators.Optional()]
            )
            client_secret = wtforms.StringField(
                "OAuth2 Client Secret", validators=[wtforms.validators.Optional()]
            )
            static_file_path = wtforms.StringField(
                "Static File Path", validators=[wtforms.validators.Optional()]
            )
            socketURL = wtforms.StringField(
                "Socket URL", validators=[wtforms.validators.Optional()]
            )
            submit = wtforms.SubmitField("Save Global Settings")

        # Instantiate forms
        guild_form = GuildSettingsForm()
        global_form = GlobalSettingsForm()
        result_html = ""

        # Handle guild form submission - only process if guild form's submit button was clicked
        if guild_form.submit.data and guild_form.validate():
            try:
                # Check if enabling password protection without OAuth2 credentials
                if guild_form.passworded.data and (not client_id or not client_secret):
                    result_html = """
                    <div style="background-color: #a02d2d; color: #ffffff; padding: 15px; border-radius: 5px; margin: 15px 0;">
                        <strong>✗ Error!</strong> Cannot enable password protection without global OAuth2 credentials. 
                        Please configure Client ID and Client Secret first (Owner only).
                    </div>
                    """
                else:
                    # Update guild config
                    await config.passworded.set(guild_form.passworded.data)
                    await config.ignoreOfflineMembers.set(
                        guild_form.ignoreOfflineMembers.data
                    )

                    # Update local variables to reflect new values
                    passworded = guild_form.passworded.data
                    ignoreOfflineMembers = guild_form.ignoreOfflineMembers.data

                    # Set success message
                    result_html = """
                    <div style="background-color: #2d7d46; color: #ffffff; padding: 15px; border-radius: 5px; margin: 15px 0;">
                        <strong>✓ Success!</strong> Guild settings have been updated.
                    </div>
                    """
            except Exception as e:
                # Set error message
                result_html = f"""
                <div style="background-color: #a02d2d; color: #ffffff; padding: 15px; border-radius: 5px; margin: 15px 0;">
                    <strong>✗ Error!</strong> Failed to update guild settings: {str(e)}
                </div>
                """

        # Handle global form submission - only process if global form's submit button was clicked
        elif global_form.submit.data and global_form.validate():
            # Check if user is owner
            if user.id not in self.bot.owner_ids:
                result_html = """
                <div style="background-color: #a02d2d; color: #ffffff; padding: 15px; border-radius: 5px; margin: 15px 0;">
                    <strong>✗ Permission Denied!</strong> Only bot owners can modify global settings.
                </div>
                """
            else:
                try:
                    # Normalize inputs by stripping whitespace (empty becomes None)
                    client_id_trimmed = (global_form.client_id.data or "").strip() or None
                    client_secret_trimmed = (global_form.client_secret.data or "").strip() or None
                    static_file_path_trimmed = (global_form.static_file_path.data or "").strip() or None
                    socketURL_trimmed = (global_form.socketURL.data or "").strip() or None
                    
                    # Update global config with trimmed values
                    await self.config.client_id.set(client_id_trimmed)
                    await self.config.client_secret.set(client_secret_trimmed)
                    await self.config.static_file_path.set(static_file_path_trimmed)
                    await self.config.socketURL.set(socketURL_trimmed)

                    # Update local variables to reflect new trimmed values
                    client_id = client_id_trimmed
                    client_secret = client_secret_trimmed
                    static_file_path = static_file_path_trimmed
                    socketURL = socketURL_trimmed

                    # Set success message
                    result_html = """
                    <div style="background-color: #2d7d46; color: #ffffff; padding: 15px; border-radius: 5px; margin: 15px 0;">
                        <strong>✓ Success!</strong> Global settings have been updated.
                    </div>
                    """
                except Exception as e:
                    # Set error message
                    result_html = f"""
                    <div style="background-color: #a02d2d; color: #ffffff; padding: 15px; border-radius: 5px; margin: 15px 0;">
                        <strong>✗ Error!</strong> Failed to update global settings: {str(e)}
                    </div>
                    """

        # Populate forms with current values
        guild_form.passworded.data = passworded
        guild_form.ignoreOfflineMembers.data = ignoreOfflineMembers

        global_form.client_id.data = client_id or ""
        global_form.client_secret.data = client_secret or ""
        global_form.static_file_path.data = static_file_path or ""
        global_form.socketURL.data = socketURL or ""

        # Build HTML response
        html_content = f"""
        <style>
            {get_common_styles()}
            .dworld-config h3 {{
                color: #b9bbbe;
                margin-top: 25px;
                margin-bottom: 10px;
                font-size: 1.1em;
            }}
            .owner-only-notice {{
                background-color: #faa61a;
                color: #000000;
                padding: 10px;
                border-radius: 3px;
                margin-bottom: 15px;
                font-weight: 500;
            }}
        </style>
        
        <div class="dworld-config">
            <h1>D-World Configuration</h1>
            <p style="color: #b9bbbe; margin-bottom: 30px;">
                Configure D-World settings for <strong style="color: #ffffff;">{{{{ guild_name }}}}</strong>
            </p>
            
            {{{{ result_html|safe }}}}
            
            <h2>Current Guild Settings</h2>
            <div class="config-section">
                <div class="config-item">
                    <span class="config-label">Password Protection:</span>
                    <span class="config-value {{{{ 'enabled' if passworded else 'disabled' }}}}">
                        {{{{ '✓ Enabled' if passworded else '✗ Disabled' }}}}
                    </span>
                </div>
                <div class="config-item">
                    <span class="config-label">Ignore Offline Members:</span>
                    <span class="config-value {{{{ 'enabled' if ignoreOfflineMembers else 'disabled' }}}}">
                        {{{{ '✓ Enabled' if ignoreOfflineMembers else '✗ Disabled' }}}}
                    </span>
                </div>
            </div>
            
            <h3>Update Guild Settings</h3>
            <div class="form-section">
                {{{{ guild_form|safe }}}}
            </div>
            
            <h2>Current Global Settings</h2>
            <div class="config-section">
                <div class="config-item">
                    <span class="config-label">OAuth2 Client ID:</span>
                    <span class="config-value {{{{ 'set' if client_id else 'not-set' }}}}">
                        {{{{ client_id if client_id else 'Not set' }}}}
                    </span>
                </div>
                <div class="config-item">
                    <span class="config-label">OAuth2 Client Secret:</span>
                    <span class="config-value {{{{ 'set' if client_secret else 'not-set' }}}}">
                        {{{{ '••••••••' if client_secret else 'Not set' }}}}
                    </span>
                </div>
                <div class="config-item">
                    <span class="config-label">Static File Path:</span>
                    <span class="config-value {{{{ 'set' if static_file_path else 'not-set' }}}}">
                        {{{{ '••••••••' if static_file_path else 'Not set' }}}}
                    </span>
                </div>
                <div class="config-item">
                    <span class="config-label">Socket URL:</span>
                    <span class="config-value {{{{ 'set' if socketURL else 'not-set' }}}}">
                        {{{{ socketURL if socketURL else 'Not set' }}}}
                    </span>
                </div>
            </div>
            
            {{% if is_owner %}}
                <h3>Update Global Settings (Owner Only)</h3>
                <div class="form-section">
                    {{{{ global_form|safe }}}}
                </div>
            {{% endif %}}
        </div>
        """

        # Return response dictionary
        return {
            "status": 0,
            "web_content": {
                "source": html_content,
                "guild_form": guild_form,
                "global_form": global_form,
                "result_html": result_html,
                "guild_name": guild.name,
                "passworded": passworded,
                "ignoreOfflineMembers": ignoreOfflineMembers,
                "client_id": client_id,
                "client_secret": client_secret,
                "static_file_path": static_file_path,
                "socketURL": socketURL,
                "is_owner": is_owner,
            },
        }
