# embed Cog

Utility extension for AI-driven creation and editing of Discord embeds. Connects through ChainHub so agents can generate or update embeds from natural language prompts.

## Roadmap highlights
- Commands: `[p]embed create #channel <prompt>` and `[p]embed edit <messagelink> <prompt>` to generate or modify embeds.
- AI generation with validation loop: prompt LLM for raw JSON, validate against Discord embed schema, and retry with LLM-guided fixes on errors.
- Context management: store generated JSON in conversation history so the LLM understands the current embed state.
- Execution: send new embeds or update existing messages once validation passes.
