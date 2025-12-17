# Qdrant Cog Roadmap

This roadmap outlines the development of the `qdrant` cog, which serves as the `ChainStore` implementation for the `langcore` framework. The goal is to provide a robust, dynamic, and scalable vector storage solution that integrates seamlessly with the RAG pipeline.

## Vision
To create a flexible VectorDB interface that allows multiple cogs to store and retrieve semantic data without collision, while supporting advanced RAG configurations dynamically.

## Milestones

### Iteration 1: Minimum Viable Product (MVP)
**Goal:** Establish connectivity and basic storage capabilities.
*Focus: Connectivity, Basic CRUD, Interface Compliance.*

- [ ] **ChainStore Implementation**: Implement the basic abstract methods defined in `langcore`'s `ChainStore`.
- [ ] **Backend Connection**: Implement connection logic to the Qdrant server (default: `localhost:6333`).
- [ ] **Basic Collection Management**: Create a mechanism to initialize a collection.
- [ ] **Write Operations**: Implement `add_documents` (or equivalent) to store embeddings.
- [ ] **Read Operations**: Implement basic `similarity_search` to retrieve documents.
- [ ] **Integration Test**: Verify `langcore` can load `qdrant` as its storage backend.

### Iteration 2: Dynamic Configuration & Isolation
**Goal:** Support multiple cogs and dynamic runtime parameters.
*Focus: Namespace isolation, Dynamic Configs, Metadata.*

- [ ] **Collection Strategy**: Implement a strategy to prevent collisions between cogs (e.g., separate collections per cog vs. single collection with payload filtering).
- [ ] **Dynamic RAG Config**: Update interfaces to accept runtime parameters for retrieval (e.g., `top_k`, `score_threshold`) instead of hardcoded values.
- [ ] **Metadata/Tagging System**: Evaluate and implement a standard metadata schema to tag memories/documents (e.g., `source_cog`, `user_id`, `timestamp`).
- [ ] **Config Validation**: Ensure passed configurations are valid for the Qdrant backend.

### Iteration 3: Advanced RAG Pipeline Integration
**Goal:** Incorporate advanced retrieval logic (formerly `ragutils`).
*Focus: MMR, Reranking, Thresholds.*

- [ ] **MMR Support**: Implement Maximal Marginal Relevance search to diversify results.
- [ ] **Thresholding**: Implement score threshold filtering to reduce hallucinations/irrelevant results.
- [ ] **Reranking**: Investigate and implement reranking logic (if feasible within the cog or as a post-retrieval step).
- [ ] **Embedding Flexibility**: Ensure the system handles different embedding methods/models dynamically as requested by the calling cog.

### Iteration 4: ChainHub Integration & Tooling
**Goal:** Expose storage capabilities to AI Agents.
*Focus: Tools, Agent Interaction.*

- [ ] **Tool Registration**: Expose Qdrant functions as tools via `ChainHub` (e.g., `remember_this`, `search_memory`).
- [ ] **Agent-Accessible Search**: Allow agents to perform semantic searches with natural language queries.
- [ ] **Context Management**: Implement tools for agents to manage their own context (delete/update entries).

### Iteration 5: Feature Complete & Optimization
**Goal:** Production readiness and full feature set.
*Focus: Optimization, Summarization, Stability.*

- [ ] **Summarization**: Implement logic to summarize retrieved context if it exceeds token limits (potentially utilizing `ChainProvider`).
- [ ] **Performance Tuning**: Optimize payload indexing and vector search parameters.
- [ ] **Error Handling**: Robust handling of connection drops and backend errors.
- [ ] **Final Review**: Ensure all `ragutils` functionality is effectively superseded or integrated.
