# spoilarr Cog

TMDb-powered extension cog that registers tools for searching and retrieving movie/series details. Integrates with LangCore via ChainHub without hard references.

- Tools: search titles, look up details, fetch cast/crew credits.
- Flow: Conversation agent selects the tool, Spoilarr calls TMDb (with retries/rate-limit awareness), returns results via `add_tool_message`, and the agent responds to the user.
- Spoiler mode: when disabled, sensitive information is wrapped in Discord spoiler markup `||...||`; when enabled, raw data is returned.
- Configuration: API key for TMDb and spoiler preference.

Future plan: delegate tool handling to a sub-agent, refine system prompts, and ensure toon formatting works for tool payloads.
