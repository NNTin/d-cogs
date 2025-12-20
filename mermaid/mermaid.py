import asyncio
import difflib
import logging
from importlib import import_module
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

RequestType = Literal["discord_deleted_user", "owner", "user", "user_strict"]


class MermaidManager:
    """Sub-agent responsible for generating and repairing Mermaid syntax."""

    SYNTAX_GENERATION_PROMPT = (
        "You are a strict Mermaid syntax generator. Produce one valid {diagram_type} diagram in pure Mermaid code. "
        "Output ONLY Mermaid syntax - no markdown fences, no 'mermaid' tag, no commentary.\n\n"
        "User description: {description}\n\n"
        "Authoring rules:\n"
        "- Start with the correct diagram keyword (flowchart TD|LR; sequenceDiagram; classDiagram; stateDiagram-v2; graph TD|LR).\n"
        "- Node/participant IDs use letters, numbers, or underscores only; labels may have spaces. Declare every participant/node before linking.\n"
        "- Keep edges directional and explicit; ensure each reference exists and arrow syntax is valid.\n"
        "- Prefer concise labels; avoid paragraphs inside nodes.\n"
        "- Styling: define a small palette for readability (e.g., classDef default fill:#0d1117,stroke:#2563eb,color:#e5e7eb,stroke-width:2px; "
        "classDef accent fill:#f5f5f5,stroke:#10b981,color:#111827,stroke-width:2px;). Apply class assignments to related nodes to keep grouping clear.\n"
        "- Use subgraphs/grouping only when it clarifies structure; keep indentation consistent.\n"
        "- Double-check punctuation (semicolons where needed), brace/indent structure, and participant/class/state declarations before returning.\n"
        "Return only the final Mermaid syntax with no wrappers."
    )

    ERROR_FIXING_PROMPT = (
        "You are a Mermaid syntax debugger. The code below failed to render; repair it and return only raw Mermaid syntax (no fences, no 'mermaid' tag).\n\n"
        "Original syntax:\n"
        "{syntax}\n\n"
        "Renderer error:\n"
        "{error_message}\n\n"
        "Correction rules:\n"
        "- Preserve the intended diagram type and structure; do not change content unnecessarily.\n"
        "- Ensure the opening line matches the diagram type (flowchart TD|LR, sequenceDiagram, classDiagram, stateDiagram-v2, graph TD|LR).\n"
        "- Validate IDs (letters/numbers/underscores only), declare missing participants/nodes, and fix malformed arrows or relationships.\n"
        "- Repair common syntax issues: unmatched brackets, missing semicolons/line breaks, mis-indented subgraphs, and broken class/style definitions.\n"
        "- Remove any markdown wrappers or stray commentary.\n"
        "Return only the corrected Mermaid syntax."
    )

    def __init__(self, mermaid_cog, langcore_cog) -> None:
        self.mermaid_cog = mermaid_cog
        self.langcore_cog = langcore_cog
        self.logger = logging.getLogger("red.d_cogs.mermaid.manager")
        self.max_retries = 3

    async def generate_syntax(
        self,
        description: str,
        diagram_type: str,
        guild_id: int,
        base_syntax: Optional[str] = None,
    ) -> str:
        """Generate Mermaid syntax using the LLM provider."""
        try:
            provider = self.langcore_cog.get_provider("ollama")
            if not provider:
                raise RuntimeError("MermaidManager could not find the ollama provider")

            prompt = self.SYNTAX_GENERATION_PROMPT.format(description=description, diagram_type=diagram_type)
            if base_syntax:
                prompt += (
                    f"\n\n**EDIT MODE** Previous diagram:\n\n```mermaid\n{base_syntax}\n```\n\n"
                    f"User request: {description}"
                )
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

            diff_lines = list(
                difflib.unified_diff(
                    syntax.splitlines(keepends=True),
                    fixed_syntax.splitlines(keepends=True),
                    fromfile="original",
                    tofile="fixed",
                )
            )
            if diff_lines:
                self.logger.warning("Mermaid syntax auto-fixed:\n%s", "".join(diff_lines))

            self.logger.debug("Fixed Mermaid syntax: %s", fixed_syntax[:100])
            return fixed_syntax
        except asyncio.TimeoutError as exc:
            raise RuntimeError("LLM timeout during syntax fixing") from exc
        except Exception as exc:
            raise RuntimeError(f"Failed to fix syntax: {exc}") from exc

    async def create_diagram(
        self,
        description: str,
        diagram_type: str,
        guild: discord.Guild,
        member_id: int,
    ) -> Tuple[str, discord.File]:
        """Generate, render, and repair Mermaid syntax with retries."""
        provider = self.langcore_cog.get_provider("ollama")
        store = self.langcore_cog.get_store()
        if not provider or not store:
            raise RuntimeError("MermaidManager could not find langcore provider or store")

        coll = f"mermaid_{member_id}"
        results = await store.retrieve_texts(
            guild,
            coll,
            description,
            top_n=1,
            min_score=0.75,
            provider=provider,
        )
        base_syntax = results[0]["text"] if results else None

        syntax = await self.generate_syntax(
            description=description,
            diagram_type=diagram_type,
            guild_id=guild.id,
            base_syntax=base_syntax,
        )

        for attempt in range(self.max_retries):
            try:
                file = await self.mermaid_cog.render_mermaid_syntax(syntax)
                name = f"{diagram_type}_{description.replace(' ', '_').replace('/', '_')[:80]}"
                full_text = f"{description} {syntax}"
                embedding = await provider.embed(full_text, guild)
                metadata = {"diagram_type": diagram_type, "description": description}
                await store.add_embedding(guild, coll, name, syntax, embedding, metadata)
                return syntax, file
            except ValueError:
                raise
            except TemplateError as exc:
                self.logger.warning("Template error on attempt %s: %s", attempt + 1, exc)
                syntax = await self.fix_syntax_error(syntax=syntax, error_message=str(exc), guild_id=guild.id)
            except RuntimeError as exc:
                self.logger.warning("Rendering error on attempt %s: %s", attempt + 1, exc)
                syntax = await self.fix_syntax_error(syntax=syntax, error_message=str(exc), guild_id=guild.id)

        raise RuntimeError(f"Failed to create diagram after {self.max_retries} attempts")


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
        self.message_handler: Optional[Any] = None

    def _build_message_handler(self) -> Any:
        """
        Build a MessageHandler instance compatible with the current langcore definition.

        This mirrors ollama's dynamic provider construction to tolerate langcore reloads.
        """
        try:
            MessageHandler = getattr(import_module("langcore.abc"), "MessageHandler")
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("Unable to import langcore.abc.MessageHandler: %s", exc)
            MessageHandler = object  # type: ignore[assignment]

        class _MermaidMessageHandler(MessageHandler):  # type: ignore[misc,valid-type]
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

        self.manager = MermaidManager(mermaid_cog=self, langcore_cog=langcore_cog)
        self.logger.info("MermaidManager initialized as sub-agent")

        self.message_handler = self._build_message_handler()
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
                error_locator = page.locator("text.error-text")
                if await error_locator.count() > 0:
                    error_msg = (await error_locator.first.inner_text()).strip()
                    raise RuntimeError(f"Syntax error: {error_msg}")
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

    async def generate_mermaid(
        self,
        description: str,
        diagram_type: str = "flowchart",
        guild_id: Optional[int] = None,
        channel_id: Optional[int] = None,
        member_id: Optional[int] = None,
    ) -> str:
        """Generate a Mermaid diagram via the MermaidManager sub-agent and upload it."""
        if guild_id is None or channel_id is None or member_id is None:
            return "Missing context parameters (guild_id, channel_id, member_id). Cannot generate diagram."

        guild_obj = self.bot.get_guild(guild_id)
        if not guild_obj:
            return f"Guild {guild_id} not found. Cannot upload diagram."

        try:
            syntax, file = await self.manager.create_diagram(
                description=description,
                diagram_type=diagram_type,
                guild=guild_obj,
                member_id=member_id,
            )
        except Exception as e:
            self.logger.error("MermaidManager failed to create diagram: %s", e)
            return f"Failed to generate diagram: {str(e)}"

        channel = guild_obj.get_channel(channel_id)
        if not channel:
            return f"Channel {channel_id} not found in guild {guild_obj.name}. Cannot upload diagram."

        try:
            msg = await channel.send("Here's your Mermaid diagram:", file=file)
        except discord.HTTPException as e:
            self.logger.error("Failed to upload diagram to channel %s: %s", channel_id, e)
            return f"Failed to upload diagram: {str(e)}"

        langcore_cog = self.bot.get_cog("langcore")
        if not langcore_cog:
            self.logger.warning("langcore cog not found, cannot inject syntax into conversation")
            return f"✅ Diagram uploaded: {msg.jump_url} (Warning: syntax not added to conversation context)"

        conv_manager = langcore_cog.conversation_manager
        conversation = conv_manager.get_conversation(member_id, channel_id, guild_id)
        lock = conv_manager.get_conversation_lock(member_id, channel_id, guild_id)

        async with lock:
            conversation.add_assistant_message(f"```mermaid\n{syntax}\n```")

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
            provider = langcore_cog.get_provider("ollama")
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
        except Exception as exc:
            await ctx.send(f"Failed to fetch diagrams: {exc}")
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
            provider = langcore_cog.get_provider("ollama")
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
        except Exception as exc:
            await ctx.send(f"Failed to search diagrams: {exc}")
            return

        if not results:
            await ctx.send("No matching diagram.")
            return

        name = results[0]["name"]
        try:
            deleted = await store.delete_embeddings(ctx.guild, coll, [name])
        except Exception as exc:
            await ctx.send(f"Failed to delete diagram: {exc}")
            return

        await ctx.send(f"✅ Deleted {deleted} matching '{query_desc}' ({name})")
