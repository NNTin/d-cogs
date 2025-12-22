# memory Cog

Captures Discord content verbatim, tags it, and prepares it for retrieval through the `ChainStore` interface (planned `qdrant` backend).

## Roadmap highlights
- **MVP**: commands to remember messages, light grammar cleanup, tagging, and vectorization through ChainStore once available.
- **Retrieval**: `[p]memory search <query>` and ChainHub tools (`memory_search`, `memory_save`) to surface memories during conversations.
- **Context injection**: optionally enrich agent replies with relevant memories.
- **Advanced plans**: optional summarization, conversation-wide summaries, embed-driven UI, and batch operations for import/export or re-embedding.
- **Dependencies**: requires an operational ChainStore backend; inspired by `vertyco/vrt-cogs/assistant`.
