# d-cogs

AI automation cogs for Red-DiscordBot built on LangChain. This site documents the architecture, core packages, and individual cogs that make up the collection.

## Why this project
- Shared interfaces via `cogchain` keep cogs decoupled.
- LangCore orchestrates AI agents and tool registration.
- Providers, stores, and extension cogs plug in through well-defined abstractions.

## Quick links
- Architecture overview: [architecture/overview.md](architecture/overview.md)
- Core components: [core/cogchain.md](core/cogchain.md), [core/langcore.md](core/langcore.md)
- Setup guide: [setup.md](setup.md)

## Getting started
1. Install Red-DiscordBot and add the repo path for these cogs.
2. Load `langcore` plus at least one provider (e.g., `ollama` or `openrouter`) and store (`qdrant` recommended).
3. Enable extension cogs like `mermaid` or `ragutils` to register tools via ChainHub.

## Feature highlights
- Interface-driven design: providers, stores, and tools share contracts from `cogchain`.
- Extensible tool registration via LangCore’s ChainHub.
- Multiple providers (BYOK or self-hosted) and vector stores.
- Built-in support for mermaid diagrams, RAG pipelines, and domain-specific extensions.
