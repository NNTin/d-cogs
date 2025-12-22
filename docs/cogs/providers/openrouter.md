# openrouter Cog

`openrouter` is a ChainProvider implementation that connects LangCore agents to OpenRouter-hosted models.

- Bring-your-own-key (BYOK) pattern: supply your OpenRouter API key and preferred model.
- Implements the `ChainProvider` abstraction so tools and agents can invoke models the same way as other providers.
- Fits alongside `ollama` to let guilds choose between hosted and self-hosted providers.
