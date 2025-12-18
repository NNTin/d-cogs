import asyncio
import logging
from io import BytesIO
from pathlib import Path
from typing import Literal, Optional

import discord
from jinja2 import Environment, FileSystemLoader, TemplateError, select_autoescape
from playwright.async_api import TimeoutError as PlaywrightTimeout
from playwright.async_api import async_playwright
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.config import Config

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

    async def cog_load(self) -> None:
        """Ensure Playwright is ready before the cog is used."""
        await super().cog_load()
        await self._ensure_playwright_installed()

    async def cog_unload(self) -> None:
        """Clean up when mermaid cog is unloaded."""
        await super().cog_unload()

        # ChainHub will automatically unregister via langcore's on_cog_remove listener
        self.logger.info("Mermaid cog unloaded")

    @commands.Cog.listener()
    async def on_langcore_cog_add(self, langcore_cog):
        """Register mermaid tool with ChainHub when langcore becomes available."""
        schema = {
            "name": "generate_mermaid",
            "description": (
                "Generate Mermaid diagram syntax from a natural language description. "
                "Returns the Mermaid syntax as a string that can be rendered into a diagram. "
                "Supports flowcharts, sequence diagrams, class diagrams, state diagrams, and more. "
                "Use this when the user asks to create, visualize, or diagram something."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": (
                            "Natural language description of what to diagram. "
                            "Be specific about relationships, flow, and structure."
                        ),
                    },
                    "diagram_type": {
                        "type": "string",
                        "enum": ["flowchart", "sequence", "class", "state", "graph"],
                        "description": (
                            "Type of diagram to generate. Choose based on what best represents the concept: "
                            "flowchart for processes, sequence for interactions, class for structures, "
                            "state for state machines, graph for relationships."
                        ),
                    },
                },
                "required": ["description", "diagram_type"],
            },
        }

        success = langcore_cog.hub.register_function(
            cog_name=self.qualified_name,
            schema=schema,
            permission_level="user",
        )

        if success:
            self.logger.info("Registered generate_mermaid tool with ChainHub")
        else:
            self.logger.warning("Failed to register generate_mermaid tool with ChainHub")

    async def _ensure_playwright_installed(self) -> None:
        """Install Playwright browsers so rendering works out of the box."""
        try:
            process = await asyncio.create_subprocess_exec(
                "playwright",
                "install",
                "chromium",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            self.logger.warning(
                "Playwright CLI not found. Mermaid rendering will fail until Playwright is installed."
            )
            return

        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            self.logger.warning(
                "Playwright install failed with code %s: %s",
                process.returncode,
                (stderr or b"").decode(errors="ignore").strip(),
            )
            return

        if stdout:
            self.logger.debug((stdout or b"").decode(errors="ignore").strip())
        self.logger.info("Playwright Chromium installation completed or already present.")

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

    async def _render_mermaid_png(self, html_content: str, *, viewport=(1280, 720), timeout: int = 15) -> BytesIO:
        """
        Render Mermaid HTML into a PNG via headless Chromium.

        Uses playwright to execute Mermaid's JS and screenshot the diagram element.
        """
        png_bytes: Optional[bytes] = None

        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})
            try:
                await page.set_content(html_content, wait_until="networkidle", timeout=timeout * 1000)
                await page.wait_for_selector(".mermaid", timeout=timeout * 1000)
                await page.wait_for_function("document.querySelector('.mermaid svg') !== null", timeout=timeout * 1000)
                element = await page.query_selector(".mermaid")
                if not element:
                    raise RuntimeError("Mermaid container not found after rendering.")
                png_bytes = await element.screenshot(type="png")
            except PlaywrightTimeout as exc:
                raise RuntimeError("Timed out while rendering the Mermaid diagram.") from exc
            finally:
                await browser.close()

        if png_bytes is None:
            raise RuntimeError("Failed to capture the Mermaid diagram.")

        return BytesIO(png_bytes)

    async def generate_mermaid(self, description: str, diagram_type: str = "flowchart") -> str:
        """Generate Mermaid diagram syntax from a natural language description.

        This function is called by the AI agent when it needs to create a diagram.
        It returns the Mermaid syntax as a string, which is stored in the conversation.
        Users can then render it using [p]mermaid command.

        Args:
            description: Natural language description of what to diagram
            diagram_type: Type of diagram (flowchart, sequence, class, state, graph)

        Returns:
            str: Mermaid diagram syntax
        """
        # Map diagram types to Mermaid syntax prefixes
        type_mapping = {
            "flowchart": "flowchart TD",
            "sequence": "sequenceDiagram",
            "class": "classDiagram",
            "state": "stateDiagram-v2",
            "graph": "graph TD",
        }

        diagram_prefix = type_mapping.get(diagram_type, "flowchart TD")

        # For now, create a simple template-based diagram
        # In the future, this could use an LLM to generate more sophisticated diagrams
        if diagram_type == "sequence":
            syntax = f"""{diagram_prefix}
participant User
participant System
User->>System: {description}
System-->>User: Response"""
        elif diagram_type == "class":
            syntax = f"""{diagram_prefix}
class Entity {{
    +description: {description}
}}"""
        elif diagram_type == "state":
            syntax = f"""{diagram_prefix}
[*] --> State1
State1 --> [*]: {description}"""
        else:  # flowchart or graph
            # Create a simple flowchart
            lines = description.split(".")
            syntax = f"{diagram_prefix}\n"
            for i, line in enumerate(lines[:5]):  # Limit to 5 steps
                if line.strip():
                    node_id = f"Step{i+1}"
                    syntax += f"    {node_id}[{line.strip()}]\n"
                    if i > 0:
                        syntax += f"    Step{i} --> {node_id}\n"

        self.logger.debug("Generated mermaid syntax for type %s: %s", diagram_type, syntax[:100])
        return syntax

    async def red_delete_data_for_user(self, *, requester: RequestType, user_id: int) -> None:
        # TODO: Replace this with the proper end user data removal handling.
        super().red_delete_data_for_user(requester=requester, user_id=user_id)

    @commands.command()
    async def mermaid(self, ctx: commands.Context, *, content: Optional[str] = None) -> None:
        """Create a mermaid image from text.

        You can provide Mermaid diagram syntax directly, or ask the AI assistant
        to generate diagrams for you using natural language (via [p]chat or mentions).

        The AI can create flowcharts, sequence diagrams, class diagrams, and more.
        Once generated, use this command to render the syntax into an image.

        Example:
            [p]mermaid graph TD; A-->B; B-->C;
        """
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
            png_bytes = await self._render_mermaid_png(rendered_html)
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
