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

from .utils import DashboardIntegration, dashboard_page, get_form_helpers


class DWorldDashboardIntegration(DashboardIntegration):
    """
    Dashboard integration for the dworld cog.

    This class provides web-based configuration management for the dworld cog,
    exposing both guild-level and global settings through a unified dashboard page.
    """

    # Type hint for the bot attribute (for type checking purposes)
    bot: commands.Bot

    async def _get_accessible_guilds(
        self, user: discord.User
    ) -> typing.List[typing.Tuple[str, str]]:
        """
        Get a list of guilds accessible to the user based on their permissions.

        Bot owners can access all guilds. Other users can only access guilds
        where they have moderator permissions or higher.

        Args:
            user: The Discord user to check permissions for

        Returns:
            List of tuples containing (guild_id, guild_name) sorted by guild name
        """
        accessible_guilds = []
        is_owner = user.id in self.bot.owner_ids

        for guild in self.bot.guilds:
            # Bot owners can access all guilds
            if is_owner:
                accessible_guilds.append((str(guild.id), guild.name))
            else:
                # Check if user is a member and has appropriate permissions
                member = guild.get_member(user.id)
                if member:
                    # Check owner, admin, manage_guild, or mod permissions
                    # Check less expensive conditions first to avoid unnecessary async calls
                    if member == guild.owner or member.guild_permissions.manage_guild:
                        accessible_guilds.append((str(guild.id), guild.name))
                    elif await self.bot.is_admin(member):
                        accessible_guilds.append((str(guild.id), guild.name))
                    elif await self.bot.is_mod(member):
                        accessible_guilds.append((str(guild.id), guild.name))

        # Sort by guild name (second element of tuple)
        accessible_guilds.sort(key=lambda x: x[1])

        return accessible_guilds

    @dashboard_page(
        name="guild", description="D-World Configuration", methods=("GET", "POST")
    )
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

        # Define GuildSelectorForm class
        class GuildSelectorForm(Form):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, prefix="guild_selector_", **kwargs)

            guild_selector = wtforms.SelectField(
                "Select Guild",
                choices=[],
                render_kw={"class": "form-select", "onchange": "this.form.submit()"},
            )
            submit_selector = wtforms.SubmitField("Switch Guild")

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
            submit = wtforms.SubmitField("Save Global Settings")

        # Instantiate forms
        guild_form = GuildSettingsForm()
        global_form = GlobalSettingsForm()
        result_html = ""

        # Get accessible guilds and instantiate guild selector form
        accessible_guilds = await self._get_accessible_guilds(user)

        # Ensure current guild is always present in the list (defensive check)
        accessible_guilds_dict = {gid: gname for gid, gname in accessible_guilds}
        accessible_guilds_dict[str(guild.id)] = guild.name

        # Convert back to list and sort by guild name
        accessible_guilds = list(accessible_guilds_dict.items())
        accessible_guilds.sort(key=lambda x: x[1])

        guild_selector_form = GuildSelectorForm()
        guild_selector_form.guild_selector.choices = accessible_guilds
        guild_selector_form.guild_selector.data = str(guild.id)
        accessible_guilds_count = len(accessible_guilds)

        # Handle guild selector form submission
        if guild_selector_form.validate_on_submit():
            try:
                selected_guild_id = int(guild_selector_form.guild_selector.data)
                selected_guild = self.bot.get_guild(selected_guild_id)

                if not selected_guild:
                    result_html = """
                    <div style="background-color: #a02d2d; color: #ffffff; padding: 15px; border-radius: 5px; margin: 15px 0;">
                        <strong>✗ Error!</strong> Selected guild not found.
                    </div>
                    """
                elif selected_guild.id != guild.id:
                    # Guild changed - use JavaScript redirect in the response
                    return {
                        "status": 0,
                        "web_content": {
                            "source": f"""
                            <script>
                                window.location.href = '/dashboard/third_parties/dworld/guild/{selected_guild_id}';
                            </script>
                            <div style="background-color: #2b2e34; color: #ffffff; padding: 20px; text-align: center;">
                                <p>Switching to {selected_guild.name}...</p>
                                <p>If you are not redirected, <a href="/dashboard/third_parties/dworld/guild/{selected_guild_id}" style="color: #5865f2;">click here</a>.</p>
                            </div>
                            """
                        },
                    }
            except (ValueError, TypeError) as e:
                result_html = f"""
                <div style="background-color: #a02d2d; color: #ffffff; padding: 15px; border-radius: 5px; margin: 15px 0;">
                    <strong>✗ Error!</strong> Invalid guild selection: {str(e)}
                </div>
                """

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
                    await self.config.client_secret.set(
                        global_form.client_secret.data or None
                    )
                    await self.config.static_file_path.set(
                        global_form.static_file_path.data or None
                    )

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
            .guild-selector-section {
                background-color: #2b2e34;
                padding: 15px;
                border-radius: 5px;
                margin-bottom: 20px;
                border: 2px solid #5865f2;
            }
            .guild-selector-section h3 {
                color: #ffffff;
                margin-top: 0;
                margin-bottom: 10px;
                font-size: 1.2em;
            }
            .guild-selector-section p {
                color: #b9bbbe;
                margin: 5px 0;
                font-size: 0.9em;
            }
            .form-select {
                background-color: #1e1f22;
                color: #ffffff;
                border: 1px solid #4f545c;
                padding: 8px;
                border-radius: 3px;
                width: 100%;
                max-width: 400px;
                cursor: pointer;
                font-size: 14px;
                margin: 10px 0;
            }
            .form-select:hover {
                border-color: #5865f2;
            }
            .form-select:focus {
                outline: none;
                border-color: #5865f2;
                box-shadow: 0 0 0 2px rgba(88, 101, 242, 0.2);
            }
            .guild-info-text {
                color: #72767d;
                font-size: 0.85em;
                font-style: italic;
                margin-top: 5px;
            }
            .single-guild-notice {
                background-color: #2d7d46;
                color: #ffffff;
                padding: 10px;
                border-radius: 3px;
                margin-top: 10px;
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
            <!-- Guild Selector Section -->
            <div class="guild-selector-section">
                <h3>🌍 Current Guild: {{ guild_name }}</h3>
                <p>You have access to {{ accessible_guilds_count }} guild(s)</p>
                {% if accessible_guilds_count > 1 %}
                    <p class="guild-info-text">Select a different guild to view and edit its settings</p>
                    {{ guild_selector_form|safe }}
                {% else %}
                    <div class="single-guild-notice">
                        ℹ️ You have access to only this guild
                    </div>
                {% endif %}
            </div>
            
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
                        {{ '••••••••' if static_file_path else 'Not set' }}
                    </span>
                </div>
            </div>
            
            {% if is_owner %}
                <h3>Update Global Settings (Owner Only)</h3>
                <div class="form-section">
                    {{ global_form|safe }}
                </div>
            {% endif %}
        </div>
        """

        # Return response dictionary
        return {
            "status": 0,
            "web_content": {
                "source": html_content,
                "guild_selector_form": guild_selector_form,
                "guild_form": guild_form,
                "global_form": global_form,
                "result_html": result_html,
                "guild_name": guild.name,
                "accessible_guilds_count": accessible_guilds_count,
                "passworded": passworded,
                "ignoreOfflineMembers": ignoreOfflineMembers,
                "client_id": client_id,
                "client_secret": client_secret,
                "static_file_path": static_file_path,
                "is_owner": is_owner,
            },
        }

    @dashboard_page(
        name="members", description="Member Customization", methods=("GET", "POST")
    )
    async def dashboard_member_customization(
        self, user: discord.User, guild: discord.Guild, **kwargs
    ) -> typing.Dict[str, typing.Any]:
        """
        Dashboard page for member customization (role colors and custom messages).

        Regular users can edit their own settings. Privileged users (owner or manage_guild)
        can select and edit any member's settings.

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
        is_privileged = member.guild_permissions.manage_guild if member else False
        has_privilege = is_owner or is_privileged

        # Load current members configuration
        try:
            members_config = await self.config.guild(guild).members()
        except Exception as e:
            return {
                "status": 0,
                "error_code": 500,
                "message": f"Failed to load member configuration: {str(e)}",
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

        # Define RegularUserForm class
        class RegularUserForm(Form):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, prefix="regular_user_", **kwargs)

            role_color = wtforms.StringField(
                "Your Role Color",
                validators=[
                    wtforms.validators.Regexp(
                        r"^#[0-9A-Fa-f]{6}$",
                        message="Color must be a valid hex color (e.g., #ffffff)",
                    )
                ],
            )
            custom_message = wtforms.StringField(
                "Your Custom Message", validators=[wtforms.validators.Optional()]
            )
            submit = wtforms.SubmitField("Save My Settings")

        # Define PrivilegedUserForm class
        class PrivilegedUserForm(Form):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, prefix="privileged_user_", **kwargs)

            member_selector = wtforms.SelectField(
                "Select Member", choices=[], render_kw={"class": "form-select"}
            )
            role_color = wtforms.StringField(
                "Role Color",
                validators=[
                    wtforms.validators.Regexp(
                        r"^#[0-9A-Fa-f]{6}$",
                        message="Color must be a valid hex color (e.g., #ffffff)",
                    )
                ],
            )
            custom_message = wtforms.StringField(
                "Custom Message", validators=[wtforms.validators.Optional()]
            )
            submit = wtforms.SubmitField("Save Member Settings")

        result_html = ""

        # Get current user's settings for display
        user_settings = members_config.get(str(user.id), {})
        current_role_color = user_settings.get("role_color", "#ffffff")
        current_custom_message = user_settings.get("custom_message", "")

        if has_privilege:
            # Populate member dropdown
            filtered_members = [m for m in guild.members if not m.bot]
            member_choices = [
                (str(m.id), f"{m.display_name} ({m.name})") for m in filtered_members
            ]
            member_choices.sort(key=lambda x: x[1])

            # Instantiate privileged form
            privileged_form = PrivilegedUserForm()
            privileged_form.member_selector.choices = member_choices

            # Handle form submission
            if privileged_form.validate_on_submit():
                try:
                    selected_member_id = privileged_form.member_selector.data
                    selected_member = guild.get_member(int(selected_member_id))

                    if not selected_member:
                        result_html = """
                        <div style="background-color: #a02d2d; color: #ffffff; padding: 15px; border-radius: 5px; margin: 15px 0;">
                            <strong>✗ Error!</strong> Selected member not found in guild.
                        </div>
                        """
                    else:
                        # Update member config
                        members_config[selected_member_id] = {
                            "role_color": privileged_form.role_color.data,
                            "custom_message": privileged_form.custom_message.data or "",
                        }
                        await self.config.guild(guild).members.set(members_config)

                        result_html = f"""
                        <div style="background-color: #2d7d46; color: #ffffff; padding: 15px; border-radius: 5px; margin: 15px 0;">
                            <strong>✓ Success!</strong> Settings for {selected_member.display_name} have been updated.
                        </div>
                        """
                except Exception as e:
                    result_html = f"""
                    <div style="background-color: #a02d2d; color: #ffffff; padding: 15px; border-radius: 5px; margin: 15px 0;">
                        <strong>✗ Error!</strong> Failed to update member settings: {str(e)}
                    </div>
                    """

            # Populate form with selected member's current settings (or user's settings as default)
            selected_member_id = privileged_form.member_selector.data or str(user.id)
            selected_settings = members_config.get(selected_member_id, {})
            privileged_form.member_selector.data = selected_member_id
            privileged_form.role_color.data = selected_settings.get(
                "role_color", "#ffffff"
            )
            privileged_form.custom_message.data = selected_settings.get(
                "custom_message", ""
            )

            # Build HTML for privileged users
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
                .color-preview {
                    display: inline-block;
                    width: 30px;
                    height: 30px;
                    border-radius: 3px;
                    vertical-align: middle;
                    margin-left: 10px;
                    border: 2px solid #4f545c;
                }
                .form-section {
                    background-color: #2b2e34;
                    padding: 20px;
                    border-radius: 5px;
                    margin-bottom: 20px;
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
                .form-select {
                    background-color: #1e1f22;
                    color: #ffffff;
                    border: 1px solid #4f545c;
                    padding: 8px;
                    border-radius: 3px;
                    width: 100%;
                    max-width: 400px;
                    cursor: pointer;
                    font-size: 14px;
                }
                .form-select:hover {
                    border-color: #5865f2;
                }
                .explanation-text {
                    color: #b9bbbe;
                    font-style: italic;
                    margin-bottom: 15px;
                }
            </style>
            
            <div class="dworld-config">
                <h1>Member Customization for {{ guild_name }}</h1>
                <p style="color: #b9bbbe; margin-bottom: 30px;">
                    Configure member role colors and custom messages
                </p>
                
                {{ result_html|safe }}
                
                <h2>Your Current Settings</h2>
                <div class="config-section">
                    <div class="config-item">
                        <span class="config-label">Your Role Color:</span>
                        <span class="config-value">{{ current_role_color }}</span>
                        <span class="color-preview" style="background-color: {{ current_role_color }};"></span>
                    </div>
                    <div class="config-item">
                        <span class="config-label">Your Custom Message:</span>
                        <span class="config-value">{{ current_custom_message if current_custom_message else 'Not set' }}</span>
                    </div>
                </div>
                
                <h2>Edit Member Settings</h2>
                <div class="form-section">
                    <p class="explanation-text">As a moderator/owner, you can customize any member's settings</p>
                    {{ privileged_form|safe }}
                </div>
            </div>
            """

            return {
                "status": 0,
                "web_content": {
                    "source": html_content,
                    "privileged_form": privileged_form,
                    "result_html": result_html,
                    "guild_name": guild.name,
                    "user_name": user.name,
                    "has_privilege": has_privilege,
                    "current_role_color": current_role_color,
                    "current_custom_message": current_custom_message,
                },
            }

        else:
            # Regular user form
            regular_form = RegularUserForm()

            # Handle form submission
            if regular_form.validate_on_submit():
                try:
                    # Update user's config
                    members_config[str(user.id)] = {
                        "role_color": regular_form.role_color.data,
                        "custom_message": regular_form.custom_message.data or "",
                    }
                    await self.config.guild(guild).members.set(members_config)

                    # Update display values
                    current_role_color = regular_form.role_color.data
                    current_custom_message = regular_form.custom_message.data or ""

                    result_html = """
                    <div style="background-color: #2d7d46; color: #ffffff; padding: 15px; border-radius: 5px; margin: 15px 0;">
                        <strong>✓ Success!</strong> Your settings have been updated.
                    </div>
                    """
                except Exception as e:
                    result_html = f"""
                    <div style="background-color: #a02d2d; color: #ffffff; padding: 15px; border-radius: 5px; margin: 15px 0;">
                        <strong>✗ Error!</strong> Failed to update your settings: {str(e)}
                    </div>
                    """

            # Populate form with user's current settings
            regular_form.role_color.data = current_role_color
            regular_form.custom_message.data = current_custom_message

            # Build HTML for regular users
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
                .color-preview {
                    display: inline-block;
                    width: 30px;
                    height: 30px;
                    border-radius: 3px;
                    vertical-align: middle;
                    margin-left: 10px;
                    border: 2px solid #4f545c;
                }
                .form-section {
                    background-color: #2b2e34;
                    padding: 20px;
                    border-radius: 5px;
                    margin-bottom: 20px;
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
                .explanation-text {
                    color: #b9bbbe;
                    font-style: italic;
                    margin-bottom: 15px;
                }
            </style>
            
            <div class="dworld-config">
                <h1>Member Customization for {{ guild_name }}</h1>
                <p style="color: #b9bbbe; margin-bottom: 30px;">
                    Configure your role color and custom message
                </p>
                
                {{ result_html|safe }}
                
                <h2>Your Current Settings</h2>
                <div class="config-section">
                    <div class="config-item">
                        <span class="config-label">Your Role Color:</span>
                        <span class="config-value">{{ current_role_color }}</span>
                        <span class="color-preview" style="background-color: {{ current_role_color }};"></span>
                    </div>
                    <div class="config-item">
                        <span class="config-label">Your Custom Message:</span>
                        <span class="config-value">{{ current_custom_message if current_custom_message else 'Not set' }}</span>
                    </div>
                </div>
                
                <h2>Edit Your Settings</h2>
                <div class="form-section">
                    <p class="explanation-text">You can customize your own role color and custom message</p>
                    {{ regular_form|safe }}
                </div>
            </div>
            """

            return {
                "status": 0,
                "web_content": {
                    "source": html_content,
                    "regular_form": regular_form,
                    "result_html": result_html,
                    "guild_name": guild.name,
                    "user_name": user.name,
                    "has_privilege": has_privilege,
                    "current_role_color": current_role_color,
                    "current_custom_message": current_custom_message,
                },
            }
