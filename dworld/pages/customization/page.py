"""Customization dashboard page for D-World."""

import typing

import discord
import wtforms
from redbot.core import commands

from ...utils import DashboardIntegration, get_form_helpers


class CustomizationPage(DashboardIntegration):
    """Dashboard page for member customization settings."""

    # Type hints for attributes provided by parent cog
    bot: commands.Bot
    config: typing.Any
    server: typing.Any

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

            role_color = wtforms.fields.ColorField(
                "Your Role Color",
                validators=[
                    wtforms.validators.Regexp(
                        r"^#[0-9A-Fa-f]{6}$",
                        message="Color must be a valid hex color (e.g., #ffffff)",
                    )
                ],
            )
            custom_message = wtforms.StringField(
                "Your Custom Message",
                validators=[
                    wtforms.validators.Optional(),
                    wtforms.validators.Length(max=20),
                ],
            )
            submit = wtforms.SubmitField("Save My Settings")

        # Define PrivilegedUserForm class
        class PrivilegedUserForm(Form):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, prefix="privileged_user_", **kwargs)

            member_selector = wtforms.SelectField(
                "Select Member", choices=[], render_kw={"class": "form-select"}
            )
            role_color = wtforms.fields.ColorField(
                "Role Color",
                validators=[
                    wtforms.validators.Regexp(
                        r"^#[0-9A-Fa-f]{6}$",
                        message="Color must be a valid hex color (e.g., #ffffff)",
                    )
                ],
            )
            custom_message = wtforms.StringField(
                "Custom Message",
                validators=[
                    wtforms.validators.Optional(),
                    wtforms.validators.Length(max=20),
                ],
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
                        custom_msg = privileged_form.custom_message.data or ""
                        members_config[selected_member_id] = {
                            "role_color": privileged_form.role_color.data,
                            "custom_message": custom_msg.strip(),
                        }
                        await self.config.guild(guild).members.set(members_config)

                        # send updates to all connected d-zone clients
                        # this broadcast is only done for the privileged form (owner/mod)
                        await self.server.broadcast_presence(
                            server=str(guild.id),
                            uid=str(selected_member.id),
                            status=selected_member.status,
                            username=selected_member.display_name,
                            role_color=privileged_form.role_color.data,
                        )
                        await self.server.broadcast_message(
                            server=str(guild.id),
                            uid=str(selected_member.id),
                            message=custom_msg.strip(),
                            channel="123",
                        )

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
                },
            }

        else:
            # Regular user form
            regular_form = RegularUserForm()

            # Handle form submission
            if regular_form.validate_on_submit():
                try:
                    # Update user's config
                    custom_msg = regular_form.custom_message.data or ""
                    members_config[str(user.id)] = {
                        "role_color": regular_form.role_color.data,
                        "custom_message": custom_msg.strip(),
                    }
                    await self.config.guild(guild).members.set(members_config)

                    # Update display values
                    current_role_color = regular_form.role_color.data
                    current_custom_message = custom_msg.strip()

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
                },
            }
