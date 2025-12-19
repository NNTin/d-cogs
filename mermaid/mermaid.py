import asyncio
import base64
import logging
from io import BytesIO
from pathlib import Path
from typing import Any, Literal, Optional, Tuple

import discord
from jinja2 import Environment, FileSystemLoader, TemplateError, select_autoescape
from langchain_core.messages import convert_to_messages
from playwright.async_api import TimeoutError as PlaywrightTimeout
from playwright.async_api import async_playwright
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.config import Config
from langcore.abc import MessageHandler

RequestType = Literal["discord_deleted_user", "owner", "user", "user_strict"]


class MermaidManager:
    """Sub-agent responsible for generating and repairing Mermaid syntax."""

    SYNTAX_GENERATION_PROMPT = (
        "You are a Mermaid diagram syntax expert. Generate ONLY valid Mermaid syntax based on the user's description.\n\n"
        "Rules:\n"
        "- Return ONLY the mermaid syntax, no explanations or markdown code blocks\n"
        "- Use the specified diagram type: {diagram_type}\n"
        "- Follow Mermaid.js syntax strictly\n"
        "- For sequence diagrams: use participant declarations and proper arrow syntax\n"
        "- For flowcharts: use proper node shapes and connection syntax\n"
        "- For class diagrams: use proper class declaration and relationship syntax\n"
        "- For state diagrams: use stateDiagram-v2 syntax\n"
        "- Ensure all node IDs are valid (no spaces, special characters)\n\n"
        "User description: {description}\n"
        "Diagram type: {diagram_type}\n\n"
        "Generate the mermaid syntax now:"
    )

    ERROR_FIXING_PROMPT = (
        "You are a Mermaid syntax debugger. The following Mermaid syntax produced a rendering error.\n\n"
        "Original syntax:\n"
        "{syntax}\n\n"
        "Error context:\n"
        "{error_message}\n\n"
        "Fix the syntax and return ONLY the corrected Mermaid code. No explanations, no markdown blocks.\n"
        "Common issues to check:\n"
        "- Invalid node IDs (spaces, special characters)\n"
        "- Missing semicolons or proper line breaks\n"
        "- Incorrect arrow syntax\n"
        "- Malformed participant/class/state declarations\n"
        "- Unclosed quotes or brackets\n\n"
        "Return the fixed syntax now:"
    )

    def __init__(self, mermaid_cog, langcore_cog) -> None:
        self.mermaid_cog = mermaid_cog
        self.langcore_cog = langcore_cog
        self.logger = logging.getLogger("red.d_cogs.mermaid.manager")
        self.max_retries = 3

    async def generate_syntax(self, description: str, diagram_type: str, guild_id: int) -> str:
        """Generate Mermaid syntax using the LLM provider."""
        try:
            provider = self.langcore_cog.get_provider("ollama")
            if not provider:
                raise RuntimeError("MermaidManager could not find the ollama provider")

            prompt = self.SYNTAX_GENERATION_PROMPT.format(description=description, diagram_type=diagram_type)
            llm = await provider.get_chat_llm(guild_id=guild_id)
            messages = convert_to_messages([{"role": "user", "content": prompt}])
            response = await llm.ainvoke(messages)
            syntax = str(response.content).strip()

            if syntax.startswith("```"):
                syntax = syntax.strip("`")
                if syntax.lower().startswith("mermaid"):
                    syntax = syntax[len("mermaid") :].strip()

            self.logger.debug("Generated Mermaid syntax: %s", syntax[:100])
            return syntax
        except asyncio.TimeoutError as exc:
            raise RuntimeError("LLM timeout during syntax generation") from exc
        except Exception as exc:
            raise RuntimeError(f"Failed to generate syntax: {exc}") from exc

    async def fix_syntax_error(self, syntax: str, error_message: str, guild_id: int) -> str:
        """Attempt to fix Mermaid syntax using the LLM based on an error message."""
        try:
            provider = self.langcore_cog.get_provider("ollama")
            if not provider:
                raise RuntimeError("MermaidManager could not find the ollama provider")

            prompt = self.ERROR_FIXING_PROMPT.format(syntax=syntax, error_message=error_message)
            llm = await provider.get_chat_llm(guild_id=guild_id)
            messages = convert_to_messages([{"role": "user", "content": prompt}])
            response = await llm.ainvoke(messages)
            fixed_syntax = str(response.content).strip()

            if fixed_syntax.startswith("```"):
                fixed_syntax = fixed_syntax.strip("`")
                if fixed_syntax.lower().startswith("mermaid"):
                    fixed_syntax = fixed_syntax[len("mermaid") :].strip()

            self.logger.debug("Fixed Mermaid syntax: %s", fixed_syntax[:100])
            return fixed_syntax
        except asyncio.TimeoutError as exc:
            raise RuntimeError("LLM timeout during syntax fixing") from exc
        except Exception as exc:
            raise RuntimeError(f"Failed to fix syntax: {exc}") from exc

    async def create_diagram(self, description: str, diagram_type: str, guild_id: int) -> Tuple[str, discord.File]:
        """Generate, render, and repair Mermaid syntax with retries."""
        syntax = await self.generate_syntax(description=description, diagram_type=diagram_type, guild_id=guild_id)

        for attempt in range(self.max_retries):
            try:
                file = await self.mermaid_cog.render_mermaid_syntax(syntax)
                return syntax, file
            except ValueError:
                raise
            except TemplateError as exc:
                self.logger.warning("Template error on attempt %s: %s", attempt + 1, exc)
                syntax = await self.fix_syntax_error(syntax=syntax, error_message=str(exc), guild_id=guild_id)
            except RuntimeError as exc:
                self.logger.warning("Rendering error on attempt %s: %s", attempt + 1, exc)
                syntax = await self.fix_syntax_error(syntax=syntax, error_message=str(exc), guild_id=guild_id)

        raise RuntimeError(f"Failed to create diagram after {self.max_retries} attempts")


class MermaidMessageHandler(MessageHandler):
    """Message handler for sending Mermaid diagram content."""

    def __init__(self, mermaid_cog: "mermaid") -> None:
        self.cog = mermaid_cog

    async def send_text(self, ctx: commands.Context, text: str, **kwargs: Any) -> discord.Message:
        return await ctx.send(text, **kwargs)

    async def send_file(
        self,
        ctx: commands.Context,
        file: discord.File,
        content: Optional[str] = None,
        **kwargs: Any,
    ) -> discord.Message:
        if not isinstance(file, discord.File):
            raise TypeError("MermaidMessageHandler.send_file requires a discord.File.")

        filename = (file.filename or "").lower()
        if not filename.endswith(".png"):
            raise commands.UserFeedbackCheckFailure("MermaidMessageHandler only supports sending PNG diagram files.")

        return await ctx.send(content=content, file=file, **kwargs)

    async def delete_message(self, ctx: commands.Context, message_id: int) -> None:
        message = await ctx.channel.fetch_message(message_id)
        await message.delete()

    async def edit_message(
        self,
        ctx: commands.Context,
        message_id: int,
        content: Optional[str] = None,
        file: Optional[discord.File] = None,
        **kwargs: Any,
    ) -> None:
        message = await ctx.channel.fetch_message(message_id)

        edit_kwargs: dict[str, Any] = dict(kwargs)
        if content is not None:
            edit_kwargs["content"] = content
        if file is not None:
            if not isinstance(file, discord.File):
                raise TypeError("edit_message expects a discord.File when updating attachments.")
            filename = (file.filename or "").lower()
            if not filename.endswith(".png"):
                raise commands.UserFeedbackCheckFailure(
                    "MermaidMessageHandler can only attach PNG diagram files when editing."
                )
            edit_kwargs["attachments"] = [file]

        await message.edit(**edit_kwargs)


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
        self.manager: Optional[MermaidManager] = None
        self.message_handler: Optional[MermaidMessageHandler] = None

    async def cog_load(self) -> None:
        """Ensure Playwright is ready before the cog is used."""
        await super().cog_load()
        await self._ensure_playwright_installed()

    async def cog_unload(self) -> None:
        """Clean up when mermaid cog is unloaded."""
        await super().cog_unload()

        # ChainHub will automatically unregister via langcore's on_cog_remove listener
        self.logger.info("Mermaid cog unloaded")
        self.manager = None
        self.message_handler = None

    @commands.Cog.listener()
    async def on_langcore_cog_add(self, langcore_cog):
        """Register mermaid tool with ChainHub when langcore becomes available."""
        schema = {
            "name": "generate_mermaid",
            "description": (
                "Generate and render Mermaid diagrams from natural language descriptions. "
                "Returns JSON with 'syntax' (Mermaid code for reference) and 'image_data_uri' "
                "(base64-encoded PNG for automatic display). Supports flowcharts, sequence diagrams, "
                "class diagrams, state diagrams, and more. Use this when the user asks to create, "
                "visualize, or diagram something."
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
                "required": ["description"],
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

        self.manager = MermaidManager(mermaid_cog=self, langcore_cog=langcore_cog)
        self.logger.info("MermaidManager initialized as sub-agent")

        self.message_handler = MermaidMessageHandler(self)
        if langcore_cog.register_message_handler(self.qualified_name, self.message_handler):
            self.logger.info("Registered MermaidMessageHandler with langcore")
        else:
            self.logger.warning("Failed to register MermaidMessageHandler with langcore")

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

    async def render_mermaid_syntax(self, syntax: str) -> discord.File:
        """
        Render Mermaid diagram syntax into a Discord-ready PNG file.

        Args:
            syntax: Mermaid diagram syntax string (e.g., "flowchart TD; A-->B").

        Returns:
            discord.File: File containing the rendered PNG image.

        Raises:
            ValueError: If the provided syntax is empty or None.
            TemplateError: If rendering the Mermaid HTML template fails.
            RuntimeError: If PNG generation or other rendering steps fail.

        Usage:
            Used by langcore to auto-render AI-generated diagrams.
        """
        if not syntax or not syntax.strip():
            raise ValueError("Mermaid syntax cannot be empty.")

        self.logger.debug("Rendering Mermaid syntax: %s", syntax[:100])

        try:
            rendered_html = self._render_mermaid_html(syntax)
        except TemplateError as exc:
            self.logger.error("Failed to render Mermaid HTML: %s", exc)
            raise TemplateError(f"Failed to render Mermaid HTML: {exc}") from exc
        except Exception as exc:
            self.logger.exception("Unexpected error while rendering Mermaid HTML")
            raise RuntimeError(f"Unexpected error while rendering Mermaid HTML: {exc}") from exc

        try:
            png_bytes = await self._render_mermaid_png(rendered_html)
        except RuntimeError as exc:
            self.logger.error("Failed to generate PNG from Mermaid diagram: %s", exc)
            raise RuntimeError(f"Failed to generate PNG from Mermaid diagram: {exc}") from exc
        except Exception as exc:
            self.logger.exception("Unexpected error while generating Mermaid PNG")
            raise RuntimeError(f"Unexpected error while generating Mermaid PNG: {exc}") from exc

        png_bytes.seek(0)
        self.logger.debug("Successfully rendered Mermaid diagram to PNG")
        return discord.File(fp=png_bytes, filename="mermaid.png")

    async def generate_mermaid(self, description: str, diagram_type: str = "flowchart") -> dict:
        """Generate Mermaid diagram syntax and a rendered PNG for automatic display.

        This function is called by the AI agent when it needs to create a diagram.
        It returns JSON containing the Mermaid syntax (for conversation storage) and
        a base64-encoded PNG data URI for automatic rendering.

        Args:
            description: Natural language description of what to diagram
            diagram_type: Type of diagram (flowchart, sequence, class, state, graph)

        Returns:
            dict: JSON with 'syntax' (Mermaid code) and 'image_data_uri' (base64 PNG data URI)
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
        try:
            rendered_html = self._render_mermaid_html(syntax)
            png_bytes = await self._render_mermaid_png(rendered_html)
            b64 = base64.b64encode(png_bytes.getvalue()).decode()
            data_uri = f"data:image/png;base64,{b64}"
            self.logger.debug("Generated and rendered mermaid diagram")
            return {"syntax": syntax, "image_data_uri": data_uri}
        except Exception as exc:
            self.logger.error("Failed to render Mermaid diagram: %s", exc)
            return {"syntax": syntax, "error": "Rendering failed"}

    async def red_delete_data_for_user(self, *, requester: RequestType, user_id: int) -> None:
        # TODO: Replace this with the proper end user data removal handling.
        super().red_delete_data_for_user(requester=requester, user_id=user_id)

    @commands.command()
    async def mermaid(self, ctx: commands.Context, *, content: Optional[str] = None) -> None:
        """Create a mermaid image from text.

        The AI assistant now automatically renders diagrams when you ask it to create
        them via [p]chat or mentions. When using the generate_mermaid tool, the AI
        returns both the syntax and a rendered PNG image automatically.

        This command is useful for:
        - Manually rendering Mermaid syntax you've written yourself
        - Re-rendering diagrams with modifications
        - Rendering syntax from external sources

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
