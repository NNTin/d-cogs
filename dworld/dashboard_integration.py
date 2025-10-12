"""
Dashboard Integration for D-World Cog

This module provides dashboard integration for the dworld cog, allowing web-based
configuration management through the AAA3A-cogs Dashboard. It provides a single
dashboard page that handles both guild-level settings (password protection and
offline member filtering) and global settings (OAuth2 credentials and static file path).

The page includes:
- Current configuration display (read-only view)
- Guild settings form (accessible to moderators and bot owners)
- Global settings form (accessible to bot owners only)

Requirements:
- AAA3A-cogs Dashboard must be loaded for this integration to work
- Users must have appropriate permissions (mod+ for guild settings, owner for global settings)
"""

import typing
import discord
import wtforms
from redbot.core import commands
from ..dashboard_utils import dashboard_page, DashboardIntegration, get_form_helpers


class DWorldDashboardIntegration(DashboardIntegration):
    """
    Dashboard integration for the dworld cog.
    
    This class provides web-based configuration management for the dworld cog,
    exposing both guild-level and global settings through a unified dashboard page.
    """
    
    # Type hint for the bot attribute (for type checking purposes)
    bot: commands.Bot
    
    @dashboard_page(name="guild", description="D-World Configuration", methods=("GET", "POST"))
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
            is_mod = await self.bot.is_mod(member)
        
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
            
            client_id = wtforms.StringField("OAuth2 Client ID", validators=[wtforms.validators.Optional()])
            client_secret = wtforms.StringField("OAuth2 Client Secret", validators=[wtforms.validators.Optional()])
            static_file_path = wtforms.StringField("Static File Path", validators=[wtforms.validators.Optional()])
            submit = wtforms.SubmitField("Save Global Settings")
        
        # Instantiate forms
        guild_form = GuildSettingsForm()
        global_form = GlobalSettingsForm()
        result_html = ""
        
        # Handle guild form submission
        if guild_form.validate_on_submit():
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
                    await config.ignoreOfflineMembers.set(guild_form.ignoreOfflineMembers.data)
                    
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
        
        # Handle global form submission
        if global_form.validate_on_submit():
            # Check if user is owner
            if user.id not in self.bot.owner_ids:
                result_html = """
                <div style="background-color: #a02d2d; color: #ffffff; padding: 15px; border-radius: 5px; margin: 15px 0;">
                    <strong>✗ Permission Denied!</strong> Only bot owners can modify global settings.
                </div>
                """
            else:
                try:
                    # Update global config
                    await self.config.client_id.set(global_form.client_id.data or None)
                    await self.config.client_secret.set(global_form.client_secret.data or None)
                    await self.config.static_file_path.set(global_form.static_file_path.data or None)
                    
                    # Update local variables to reflect new values
                    client_id = global_form.client_id.data or None
                    client_secret = global_form.client_secret.data or None
                    static_file_path = global_form.static_file_path.data or None
                    
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
        
        # Build HTML response
        html_content = """
        <style>
            .dworld-config {
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background-color: #1e1f22;
                color: #e6e6e6;
                padding: 20px;
                border-radius: 8px;
            }
            .dworld-config h1 {
                color: #ffffff;
                border-bottom: 2px solid #5865f2;
                padding-bottom: 10px;
                margin-bottom: 20px;
            }
            .dworld-config h2 {
                color: #ffffff;
                margin-top: 30px;
                margin-bottom: 15px;
                font-size: 1.3em;
            }
            .dworld-config h3 {
                color: #b9bbbe;
                margin-top: 25px;
                margin-bottom: 10px;
                font-size: 1.1em;
            }
            .config-section {
                background-color: #2b2e34;
                padding: 20px;
                border-radius: 5px;
                margin-bottom: 20px;
            }
            .config-item {
                margin-bottom: 12px;
                padding: 8px;
                background-color: #1e1f22;
                border-radius: 3px;
            }
            .config-label {
                font-weight: bold;
                color: #b9bbbe;
                display: inline-block;
                width: 180px;
            }
            .config-value {
                color: #ffffff;
                font-family: 'Courier New', monospace;
            }
            .config-value.enabled {
                color: #3ba55d;
            }
            .config-value.disabled {
                color: #ed4245;
            }
            .config-value.set {
                color: #3ba55d;
            }
            .config-value.not-set {
                color: #ed4245;
            }
            .form-section {
                background-color: #2b2e34;
                padding: 20px;
                border-radius: 5px;
                margin-bottom: 20px;
            }
            .form-section input[type="text"],
            .form-section input[type="checkbox"] {
                margin: 8px 0;
            }
            .form-section input[type="text"] {
                background-color: #1e1f22;
                color: #ffffff;
                border: 1px solid #4f545c;
                padding: 8px;
                border-radius: 3px;
                width: 100%;
                max-width: 400px;
            }
            .form-section input[type="checkbox"] {
                width: 20px;
                height: 20px;
                cursor: pointer;
            }
            .form-section label {
                color: #b9bbbe;
                display: block;
                margin-top: 12px;
                margin-bottom: 5px;
                font-weight: 500;
            }
            .form-section input[type="submit"] {
                background-color: #5865f2;
                color: #ffffff;
                border: none;
                padding: 10px 20px;
                border-radius: 3px;
                cursor: pointer;
                font-size: 14px;
                font-weight: 600;
                margin-top: 15px;
                transition: background-color 0.2s;
            }
            .form-section input[type="submit"]:hover {
                background-color: #4752c4;
            }
            .owner-only-notice {
                background-color: #faa61a;
                color: #000000;
                padding: 10px;
                border-radius: 3px;
                margin-bottom: 15px;
                font-weight: 500;
            }
        </style>
        
        <div class="dworld-config">
            <h1>D-World Configuration</h1>
            <p style="color: #b9bbbe; margin-bottom: 30px;">
                Configure D-World settings for <strong style="color: #ffffff;">{{ guild_name }}</strong>
            </p>
            
            {{ result_html|safe }}
            
            <h2>Current Guild Settings</h2>
            <div class="config-section">
                <div class="config-item">
                    <span class="config-label">Password Protection:</span>
                    <span class="config-value {{ 'enabled' if passworded else 'disabled' }}">
                        {{ '✓ Enabled' if passworded else '✗ Disabled' }}
                    </span>
                </div>
                <div class="config-item">
                    <span class="config-label">Ignore Offline Members:</span>
                    <span class="config-value {{ 'enabled' if ignoreOfflineMembers else 'disabled' }}">
                        {{ '✓ Enabled' if ignoreOfflineMembers else '✗ Disabled' }}
                    </span>
                </div>
            </div>
            
            <h3>Update Guild Settings</h3>
            <div class="form-section">
                {{ guild_form|safe }}
            </div>
            
            <h2>Current Global Settings</h2>
            <div class="config-section">
                <div class="config-item">
                    <span class="config-label">OAuth2 Client ID:</span>
                    <span class="config-value {{ 'set' if client_id else 'not-set' }}">
                        {{ client_id if client_id else 'Not set' }}
                    </span>
                </div>
                <div class="config-item">
                    <span class="config-label">OAuth2 Client Secret:</span>
                    <span class="config-value {{ 'set' if client_secret else 'not-set' }}">
                        {{ '••••••••' if client_secret else 'Not set' }}
                    </span>
                </div>
                <div class="config-item">
                    <span class="config-label">Static File Path:</span>
                    <span class="config-value {{ 'set' if static_file_path else 'not-set' }}">
                        {{ static_file_path if static_file_path else 'Not set' }}
                    </span>
                </div>
            </div>
            
            <h3>Update Global Settings (Owner Only)</h3>
            <div class="form-section">
                {% if not is_owner %}
                <div class="owner-only-notice">
                    ⚠️ Only bot owners can modify global settings.
                </div>
                {% endif %}
                {{ global_form|safe }}
            </div>
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
                "is_owner": is_owner,
            },
        }
