# Langcore Extension Cog Integration Guide

## Required Steps
1. Listen for `on_langcore_cog_add` and receive the langcore cog instance.
2. Register tool schemas with `langcore_cog.hub.register_function(...)`.
3. Register a cog-specific system prompt via `langcore_cog.conversation_manager.register_cog_system_prompt(...)`.
4. (Optional) Register a `MessageHandler` for rich responses with `langcore_cog.register_message_handler(...)`.
5. (Optional) Use a sub-agent (see `SubAgent` below) when your cog orchestrates its own tool loop.

## Tool Signature Contract
- Tool callbacks **must** accept `ctx: ExtensionContext` as the final parameter. The langcore wrapper injects it automatically.
- Backward compatibility: if your signature does not accept `ctx`, the wrapper falls back to the old `guild_id/channel_id/member_id` kwargs and finally to raw tool args.

```python
from langcore.abc import ExtensionContext

async def my_tool(user_query: str, ctx: ExtensionContext) -> str:
    provider = ctx.get_provider()
    store = ctx.get_store()
    await ctx.add_to_conversation("Working on it...")
    # ... do work ...
    return "✅ Done"
```

## ExtensionContext API
- Fields: `guild_id`, `channel_id`, `member_id`, `langcore`, `default_provider` (optional).
- `ctx.get_provider(name=None)`: returns the requested provider or the guild default (see below).
- `ctx.get_store()`: returns the configured `ChainStore`, raising if missing.
- `await ctx.add_to_conversation(content, role="assistant", tool_call_id=None, name=None)`: thread-safe injection into the conversation using langcore's locking helper.

## Provider Selection
- Guilds configure a preferred provider via `[p]langcore defaultprovider <name>`.
- `ctx.get_provider()` uses the configured default when `name` is omitted, falling back to `langcore.DEFAULT_PROVIDER_FALLBACK`.
- `await langcore_cog.get_default_provider(guild_id)` is available when you need the provider outside of a tool callback.

## Sub-Agent Pattern
- Use `langcore.abc.SubAgent` for reusable tool-calling loops.
- Example skeleton:
```python
class MyManager(SubAgent):
    async def handle_request(self, request: str, ctx: ExtensionContext) -> str:
        messages = [
            {"role": "system", "content": "..."},
            {"role": "user", "content": request},
        ]
        callbacks = {"search": self.search}
        return await self.run_tool_loop(
            messages=messages,
            tools=[{"name": "search"}],
            callbacks=callbacks,
            guild_id=ctx.guild_id,
            member_id=ctx.member_id,
            channel_id=ctx.channel_id,
            provider=ctx.get_provider(),
        )
```

## Migration Checklist (old pattern → new pattern)
- Replace `langcore_cog.get_provider("ollama")` with `ctx.get_provider()`.
- Replace manual lock handling and `conversation.add_*` calls with `await ctx.add_to_conversation(...)` or `langcore_cog.inject_conversation_content(...)`.
- Pass `ctx` through to managers and helpers instead of raw IDs.
- Avoid direct `conversation_manager` access unless absolutely needed; rely on the injected helpers.
- Use `[p]langcore defaultprovider <name>` to keep provider selection configurable per guild.

## Troubleshooting
- **Missing ctx**: ensure your tool signature includes `ctx: ExtensionContext`; the wrapper logs a debug message when it falls back.
- **Provider not registered**: load the provider cog and/or update the default with `[p]langcore defaultprovider`.
- **Conversation injection errors**: confirm langcore is loaded and the conversation exists; `ctx.add_to_conversation` handles locking but will surface errors if the conversation cannot be found.
- **Tool args dropped**: if a callback rejects injected kwargs, the wrapper retries with legacy parameters, then raw args. Check debug logs for the rejection details.

See `langcore/ARCHITECTURE.md` for architecture diagrams and deeper background.
