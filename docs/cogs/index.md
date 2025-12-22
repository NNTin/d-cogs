# Cogs Overview

This collection bundles provider, store, and extension cogs that plug into LangCore via shared interfaces from `cogchain`.

## Categories
- **Providers**: LLM backends implementing `ChainProvider` (e.g., `ollama`, `openrouter`).
- **Stores**: Vector storage implementations of `ChainStore` (e.g., `qdrant`).
- **Extensions**: Tools and sub-agents registered through ChainHub (e.g., `mermaid`, `spoilarr`, `embed`, `memory`, `ragutils`).

## Catalog

| Cog | Type | Purpose |
|-----|------|---------|
| `langcore` | Core | Agent orchestration, ChainHub, provider/store abstractions |
| `ollama` | Provider | Local/self-hosted LLM provider with load balancing potential |
| `openrouter` | Provider | BYOK provider using OpenRouter endpoints |
| `qdrant` | Store | Vector storage and RAG pipeline |
| `mermaid` | Extension | Mermaid diagram generation via sub-agent |
| `spoilarr` | Extension | TMDb-powered lookups with spoiler controls |
| `embed` | Extension | AI-assisted Discord embed creation/editing |
| `memory` | Extension | Durable memory capture and retrieval |
| `ragutils` | Extension | Advanced RAG helpers (chunking, rerank, MMR) |
