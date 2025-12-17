# Memory Cog Roadmap

This roadmap covers the incremental delivery of the `memory` cog, which ingests Discord content verbatim (light grammar fixes allowed), attaches helpful metadata/tags, and relies on a vector store (e.g., future `qdrant` cog) for retrieval.

## Vision
Provide durable, queryable memories for LangCore agents without lossy summarization, while remaining extensible enough to adopt features proven in `vertyco/vrt-cogs/assistant` (credit: vertyco) such as optional summarization, conversation-wide capture, and embed-driven UIs.

## Milestones

### Iteration 1: MVP — Verbatim Capture & Vectorization
**Goal:** Persist messages verbatim with minimal cleanup and metadata tagging.
- [ ] **Cog Skeleton**: Create the basic `memory` cog structure, commands, and configuration storage.
- [ ] **Capture Flow**: Implement a command (e.g., `[p]memory remember <message link>`) or background listener that:
    - Fetches the target message content.
    - Performs light grammar/sentence cleanup without altering meaning.
    - Applies user-specified or auto-generated tags (recipe, git repo, project, movie, series, etc.).
- [ ] **Vector Store Interface**: Integrate with the `ChainStore` abstraction (when `qdrant` MVP is ready) so vectors can be queued or stored when the backend is available.
- [ ] **Metadata Schema**: Define the payload stored alongside vectors (origin channel, author, timestamp, tags, cleaned text).
- [ ] **Credit**: Document inspiration from `vertyco/vrt-cogs/assistant` in the cog README/docstrings.

### Iteration 2: Retrieval & Conversation Hooks
**Goal:** Enable agents and users to leverage the stored memories.
- [ ] **Search Commands**: Add `[p]memory search <query>` to fetch top results using the configured vector store.
- [ ] **LangCore Integration**: Provide ChainHub tools ("memory_search", "memory_save") so LangCore agents can query or write memories during conversations.
- [ ] **Context Injection**: When the main agent responds, allow optional auto-fetch of relevant memories to enrich answers.
- [ ] **Embed Menu (Preparation)**: Define the data structures required for a future embed-driven browsing/editing UI.

### Iteration 3: Advanced Features (Adopting Assistant Patterns)
**Goal:** Parity with the richer assistant implementation while keeping the no-summarization default.
- [ ] **Optional Summarization**: Allow admins to toggle summarization and choose between sentence-level or conversation-level summaries (mirroring vertyco’s approach).
- [ ] **Conversation Summaries**: Implement a background job or command to summarize entire threads/DMs and store them as separate memory entries when enabled.
- [ ] **EmbedMenu UI**: Introduce an interactive embed menu for browsing, editing tags, or deleting memories.
- [ ] **Batch Operations**: Provide commands to bulk-import/export memories, re-embed with a new model, or retag entries.
- [ ] **Testing & Docs**: Include integration tests that mock the vector store plus documentation detailing how the cog depends on the `qdrant` (or other ChainStore) implementation.

## Dependencies
- Requires an operational ChainStore backend (planned `qdrant` cog) for production use. Until then, consider an in-memory or file-based fallback to unblock development.
- Relies on LangCore’s ChainHub for tool registration and agent consumption.
