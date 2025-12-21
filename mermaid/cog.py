import asyncio
import logging
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

import discord
from jinja2 import Environment, FileSystemLoader, TemplateError, select_autoescape
from playwright.async_api import TimeoutError as PlaywrightTimeout
from playwright.async_api import async_playwright
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.config import Config
from cogchain.interfaces import ExtensionContext, LangcoreProtocol, MessageHandler

from .manager import MermaidManager
from .types import RequestType


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
        self.message_handler: Optional[MessageHandler] = None

    def _build_message_handler(self) -> MessageHandler:
        """Build a MessageHandler instance for langcore registrations."""

        class _MermaidMessageHandler(MessageHandler):
            """Message handler for sending Mermaid diagram content."""

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
                    raise commands.UserFeedbackCheckFailure(
                        "MermaidMessageHandler only supports sending PNG diagram files."
                    )

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

        return _MermaidMessageHandler()

    async def cog_load(self) -> None:
        """Ensure Playwright is ready before the cog is used."""
        await super().cog_load()
        await self._ensure_playwright_installed()

    async def cog_unload(self) -> None:
        """Clean up when mermaid cog is unloaded."""
        await super().cog_unload()

        langcore_cog = self.bot.get_cog("langcore")
        if isinstance(langcore_cog, LangcoreProtocol):
            try:
                langcore_cog.conversation_manager.unregister_cog_system_prompt(self.qualified_name)
            except Exception as exc:
                self.logger.debug("Failed to unregister Mermaid system prompt on unload: %s", exc)
        self.logger.info("Mermaid cog unloaded")
        self.manager = None
        self.message_handler = None

    @commands.Cog.listener()
    async def on_langcore_cog_add(self, langcore_cog):
        """Register mermaid tool with ChainHub when langcore becomes available."""
        schema = {
            "name": "generate_mermaid",
            "description": (
                "Create a Mermaid diagram from a natural-language description. "
                "Picks the right Mermaid syntax for the requested diagram type, uploads a rendered PNG to the channel, "
                "and adds the raw Mermaid code to the conversation for reuse. "
                "Call this when the user asks to visualize, diagram, map a flow/sequence/state/class/graph, or requests a chart."
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
                            "Optional: desired diagram type. If omitted, the tool will choose the closest fit. "
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

        if hasattr(langcore_cog, "conversation_manager"):
            prompt = (
                "You can render diagrams by calling the `generate_mermaid` tool. "
                "Use it when the user asks to visualize, draw a flow/sequence/state/class/graph, or requests a chart or diagram."
            )
            try:
                langcore_cog.conversation_manager.register_cog_system_prompt(self.qualified_name, prompt)
                self.logger.info("Registered Mermaid system prompt with conversation manager")
            except Exception as exc:
                self.logger.warning("Failed to register Mermaid system prompt: %s", exc)

        if not self.manager:
            self.manager = MermaidManager(mermaid_cog=self)
            self.logger.info("MermaidManager initialized as sub-agent")

        self.message_handler = self._build_message_handler()
        if langcore_cog.register_message_handler(self.qualified_name, self.message_handler):
            self.logger.info("Registered MermaidMessageHandler with langcore")
        else:
            self.logger.warning("Failed to register MermaidMessageHandler with langcore")

    @commands.Cog.listener()
    async def on_langcore_cog_remove(self, langcore_cog=None):
        """Ensure the system prompt is removed when langcore unloads."""
        langcore_cog = langcore_cog or self.bot.get_cog("langcore")
        if isinstance(langcore_cog, LangcoreProtocol):
            try:
                langcore_cog.conversation_manager.unregister_cog_system_prompt(self.qualified_name)
                self.logger.info("Unregistered Mermaid system prompt after langcore removal")
            except Exception as exc:
                self.logger.debug("Failed to unregister Mermaid system prompt after langcore removal: %s", exc)

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
                await page.wait_for_selector(".mermaid-container", timeout=timeout * 1000)
                await page.wait_for_function(
                    "document.querySelector('.mermaid-container svg') || "
                    "document.querySelector('.mermaid-container .error-text')",
                    timeout=timeout * 1000,
                )
                error_locator = page.locator(".mermaid-container .error-text")
                if await error_locator.count() > 0:
                    first_error = error_locator.first
                    raw_error = await first_error.text_content()
                    if raw_error is None:
                        try:
                            raw_error = await first_error.inner_text()
                        except Exception as exc:  # noqa: BLE001
                            raw_error = f"Unable to read error text: {exc}"
                    error_msg = raw_error.strip() if raw_error else "Unknown Mermaid syntax error"
                    raise RuntimeError(f"Syntax error: {error_msg}")
                element = await page.query_selector(".mermaid-container")
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
            self.logger.error("Mermaid syntax causing error: %s", syntax)
            raise RuntimeError(f"Failed to generate PNG from Mermaid diagram: {exc}") from exc
        except Exception as exc:
            self.logger.exception("Unexpected error while generating Mermaid PNG")
            raise RuntimeError(f"Unexpected error while generating Mermaid PNG: {exc}") from exc

        png_bytes.seek(0)
        self.logger.debug("Successfully rendered Mermaid diagram to PNG")
        return discord.File(fp=png_bytes, filename="mermaid.png")

    async def generate_mermaid(
        self,
        description: str,
        diagram_type: str = "flowchart",
        ctx: Optional[ExtensionContext] = None,
        guild_id: Optional[int] = None,
        channel_id: Optional[int] = None,
        member_id: Optional[int] = None,
    ) -> str:
        """Generate a Mermaid diagram via the MermaidManager sub-agent and upload it."""
        if ctx is None and (guild_id is None or channel_id is None or member_id is None):
            self.logger.warning("generate_mermaid called without langcore context or IDs")
            return "Langcore context is required to generate a diagram via the tool."

        if ctx is None:
            self.logger.warning("Langcore context unavailable; cannot generate diagram.")
            return "Langcore context unavailable; cannot generate diagram."

        guild_obj = self.bot.get_guild(ctx.guild_id)
        if not guild_obj:
            return f"Guild {ctx.guild_id} not found. Cannot upload diagram."

        if not self.manager:
            self.manager = MermaidManager(mermaid_cog=self)

        try:
            syntax, file = await self.manager.create_diagram(
                description=description,
                diagram_type=diagram_type,
                guild=guild_obj,
                ctx=ctx,
            )
        except Exception as e:
            self.logger.error("MermaidManager failed to create diagram: %s", e)
            return f"Failed to generate diagram: {str(e)}"

        channel = guild_obj.get_channel(ctx.channel_id)
        if not channel:
            return f"Channel {ctx.channel_id} not found in guild {guild_obj.name}. Cannot upload diagram."

        try:
            msg = await channel.send("Here's your Mermaid diagram:", file=file)
        except discord.HTTPException as e:
            self.logger.error("Failed to upload diagram to channel %s: %s", ctx.channel_id, e)
            return f"Failed to upload diagram: {str(e)}"

        try:
            await ctx.add_to_conversation(f"```mermaid\n{syntax}\n```")
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Failed to inject Mermaid syntax into conversation: %s", exc)
            return (
                f"✅ Diagram uploaded: {msg.jump_url} "
                "(Warning: syntax not added to conversation context)"
            )

        return f"✅ Diagram uploaded: {msg.jump_url}\nMermaid syntax added to conversation context."

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

    @commands.command(name="mdiagrams")
    async def mdiagrams(self, ctx: commands.Context) -> None:
        """List your stored Mermaid diagrams."""
        langcore_cog = self.bot.get_cog("langcore")
        if not langcore_cog:
            await ctx.send("langcore cog is not available.")
            return
        try:
            provider = await langcore_cog.get_default_provider(ctx.guild.id)
            store = langcore_cog.get_store()
        except Exception as exc:
            await ctx.send(f"Unable to access vector store: {exc}")
            return
        if not provider or not store:
            await ctx.send("Vector store or provider is unavailable.")
            return

        coll = f"mermaid_{ctx.author.id}"
        try:
            results = await store.retrieve_texts(ctx.guild, coll, "", top_n=10, provider=provider)
        except NotImplementedError as exc:
            self.logger.debug("ChainStore.retrieve_texts not implemented: %s", exc)
            await ctx.send("Listing diagrams is not supported with the current vector store.")
            return

        if not results:
            await ctx.send("No diagrams found for you yet.")
            return

        lines = []
        for r in results:
            meta = r.get("metadata") or {}
            description = str(meta.get("description", ""))[:50]
            score = r.get("score") or 0.0
            lines.append(f"**{r.get('name','unnamed')}** - {description} (score: {score:.2f})")

        await ctx.send("\n".join(lines))

    @commands.command(name="mdelete")
    async def mdelete(self, ctx: commands.Context, *, query_desc: str) -> None:
        """Delete a Mermaid diagram matching the given description."""
        langcore_cog = self.bot.get_cog("langcore")
        if not langcore_cog:
            await ctx.send("langcore cog is not available.")
            return
        try:
            provider = await langcore_cog.get_default_provider(ctx.guild.id)
            store = langcore_cog.get_store()
        except Exception as exc:
            await ctx.send(f"Unable to access vector store: {exc}")
            return
        if not provider or not store:
            await ctx.send("Vector store or provider is unavailable.")
            return

        coll = f"mermaid_{ctx.author.id}"
        try:
            results = await store.retrieve_texts(
                ctx.guild,
                coll,
                query_desc,
                top_n=1,
                min_score=0.6,
                provider=provider,
            )
        except NotImplementedError:
            self.logger.debug("ChainStore.retrieve_texts not implemented")
            await ctx.send("Listing diagrams is not supported with the current vector store.")
            return

        if not results:
            await ctx.send("No matching diagram.")
            return

        name = results[0]["name"]
        try:
            deleted = await store.delete_embeddings(ctx.guild, coll, [name])
        except NotImplementedError:
            self.logger.debug("ChainStore.delete_embeddings not implemented")
            await ctx.send("Deleting diagrams is not supported with the current vector store.")
            return

        await ctx.send(f"✅ Deleted {deleted} matching '{query_desc}' ({name})")
