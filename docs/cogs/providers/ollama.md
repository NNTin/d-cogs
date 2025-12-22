# ollama Cog

`ollama` is the ChainProvider implementation cog for self-hosted or local LLMs.

- Acts as the LLM backend for AI agents.
- Implements the `ChainProvider` abstraction from `langcore`, enabling agents to query large language models.
- Connects to an LLM service, e.g., `localhost:11434`.

### Provider strategy
- Models defined in Ollama serve as defaults for extension cogs; extensions can override the model.
- Multiple Ollama providers can be configured for fallback or load balancing.
- `langcore.get_provider()` will handle selecting the appropriate provider instance for the current guild.
