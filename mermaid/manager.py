import asyncio
import difflib
import logging
from typing import Optional, Tuple

import discord
from jinja2 import TemplateError
from langchain_core.messages import convert_to_messages
from cogchain.interfaces import ExtensionContext

from .prompts import ERROR_FIXING_PROMPT, SYNTAX_GENERATION_PROMPT


class MermaidManager:
    """Sub-agent responsible for generating and repairing Mermaid syntax."""

    def __init__(self, mermaid_cog) -> None:
        self.mermaid_cog = mermaid_cog
        self.logger = logging.getLogger("red.d_cogs.mermaid.manager")
        self.max_retries = 3

    async def generate_syntax(
        self,
        description: str,
        diagram_type: str,
        ctx: ExtensionContext,
        base_syntax: Optional[str] = None,
    ) -> str:
        """Generate Mermaid syntax using the LLM provider."""
        try:
            provider = ctx.get_provider()

            prompt = SYNTAX_GENERATION_PROMPT.format(description=description, diagram_type=diagram_type)
            if base_syntax:
                prompt += (
                    f"\n\n**EDIT MODE** Previous diagram:\n\n```mermaid\n{base_syntax}\n```\n\n"
                    f"User request: {description}"
                )
            llm = await provider.get_chat_llm(guild_id=ctx.guild_id)
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

    async def fix_syntax_error(self, syntax: str, error_message: str, ctx: ExtensionContext) -> str:
        """Attempt to fix Mermaid syntax using the LLM based on an error message."""
        try:
            provider = ctx.get_provider()
            prompt = ERROR_FIXING_PROMPT.format(syntax=syntax, error_message=error_message)
            llm = await provider.get_chat_llm(guild_id=ctx.guild_id)
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
        ctx: ExtensionContext,
    ) -> Tuple[str, discord.File]:
        """Generate, render, and repair Mermaid syntax with retries."""
        provider = ctx.get_provider()

        store = None
        try:
            store = ctx.get_store()
        except Exception as exc:
            store = None
            self.logger.debug("No ChainStore available, proceeding without persistence: %s", exc)

        coll = f"mermaid_{ctx.member_id}"
        base_syntax = None
        if store:
            try:
                results = await store.retrieve_texts(
                    guild,
                    coll,
                    description,
                    top_n=1,
                    min_score=0.75,
                    provider=provider,
                )
                base_syntax = results[0]["text"] if results else None
            except NotImplementedError:
                self.logger.debug("ChainStore.retrieve_texts not implemented; skipping context retrieval")
                base_syntax = None

        syntax = await self.generate_syntax(
            description=description,
            diagram_type=diagram_type,
            ctx=ctx,
            base_syntax=base_syntax,
        )

        for attempt in range(self.max_retries):
            try:
                file = await self.mermaid_cog.render_mermaid_syntax(syntax)
                if store:
                    try:
                        name = f"{diagram_type}_{description.replace(' ', '_').replace('/', '_')[:80]}"
                        full_text = f"{description} {syntax}"
                        embedding = await provider.embed(full_text, guild)
                        metadata = {"diagram_type": diagram_type, "description": description}
                        await store.add_embedding(guild, coll, name, syntax, embedding, metadata)
                    except NotImplementedError:
                        self.logger.debug("ChainStore.add_embedding not implemented; skipping persistence")
                return syntax, file
            except ValueError:
                raise
            except TemplateError as exc:
                self.logger.warning("Template error on attempt %s: %s", attempt + 1, exc)
                syntax = await self.fix_syntax_error(syntax=syntax, error_message=str(exc), ctx=ctx)
            except RuntimeError as exc:
                self.logger.warning("Rendering error on attempt %s: %s", attempt + 1, exc)
                syntax = await self.fix_syntax_error(syntax=syntax, error_message=str(exc), ctx=ctx)

        raise RuntimeError(f"Failed to create diagram after {self.max_retries} attempts")
