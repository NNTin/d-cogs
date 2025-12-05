import logging
from io import BytesIO
from pathlib import Path
from typing import Literal, Optional

import discord
from jinja2 import Environment, FileSystemLoader, TemplateError, select_autoescape
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.config import Config
from weasyprint import HTML

RequestType = Literal["discord_deleted_user", "owner", "user", "user_strict"]


class mermaid(commands.Cog):
    """
    Create mermaid images from text
    """

    logger = logging.getLogger("red.d_cogs.mermaid")

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(
            self,
            identifier=257263088,
            force_registration=True,
        )

    def _render_mermaid_html(self, diagram: str) -> str:
        """
        Render the Mermaid HTML page with the provided diagram content.

        Uses templates/index.html as the base template and returns the rendered HTML string.
        """
        template_dir = Path(__file__).parent / "templates"
        env = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape(["html", "xml"]),
        )
        template = env.get_template("index.html")
        return template.render(diagram=diagram)

    def _render_mermaid_png(self, html_content: str, *, resolution: int = 144) -> BytesIO:
        """
        Convert rendered HTML into a PNG image held in memory.

        Uses a slightly higher resolution by default for clearer Discord previews.
        """
        output = BytesIO()
        html = HTML(string=html_content, base_url=str(Path(__file__).parent))
        html.write_png(target=output, resolution=resolution)
        output.seek(0)
        return output

    async def red_delete_data_for_user(self, *, requester: RequestType, user_id: int) -> None:
        # TODO: Replace this with the proper end user data removal handling.
        super().red_delete_data_for_user(requester=requester, user_id=user_id)

    @commands.command()
    async def mermaid(self, ctx: commands.Context, *, content: Optional[str] = None) -> None:
        """Create a mermaid image from text."""
        if not content or not content.strip():
            await ctx.send("Please provide the mermaid diagram content. Usage: `[p]mermaid <content>`")
            return

        diagram = content.strip()
        status_message: Optional[discord.Message] = None

        try:
            await ctx.message.add_reaction("⏳")
        except discord.HTTPException:
            pass

        status_message = await ctx.send("Rendering Mermaid diagram...")

        try:
            rendered_html = self._render_mermaid_html(diagram)
        except TemplateError as exc:
            if status_message:
                await status_message.edit(content=f":warning: Unable to render the Mermaid template: {exc}")
            else:
                await ctx.send(f":warning: Unable to render the Mermaid template: {exc}")
            return
        except Exception:
            self.logger.exception("Unexpected error while rendering the Mermaid template")
            if status_message:
                await status_message.edit(content=":warning: Something went wrong while preparing the template.")
            else:
                await ctx.send(":warning: Something went wrong while preparing the template.")
            return

        try:
            png_bytes = self._render_mermaid_png(rendered_html)
        except Exception as exc:
            self.logger.exception("Failed converting Mermaid HTML to PNG")
            if status_message:
                await status_message.edit(
                    content="\U000026a0\ufe0f I couldn't convert that Mermaid diagram to an image. "
                    "Please check the diagram syntax and try again. "
                    f"Details: {exc}"
                )
            else:
                await ctx.send(
                    "\U000026a0\ufe0f I couldn't convert that Mermaid diagram to an image. "
                    "Please check the diagram syntax and try again. "
                    f"Details: {exc}"
                )
            return

        file = discord.File(fp=png_bytes, filename="mermaid.png")
        try:
            await ctx.send(file=file)
        except discord.HTTPException as exc:
            self.logger.exception("Discord rejected the Mermaid upload")
            if status_message:
                await status_message.edit(content=f":warning: Failed to upload the Mermaid image: {exc}")
            else:
                await ctx.send(f":warning: Failed to upload the Mermaid image: {exc}")
            return
        except discord.DiscordException:
            self.logger.exception("Unexpected Discord error while uploading Mermaid image")
            if status_message:
                await status_message.edit(content=":warning: An unexpected Discord error occurred while uploading the image.")
            else:
                await ctx.send(":warning: An unexpected Discord error occurred while uploading the image.")
            return

        if status_message:
            await status_message.edit(content="Mermaid diagram ready! ✅")

        try:
            await ctx.message.add_reaction("✅")
        except discord.HTTPException:
            pass
