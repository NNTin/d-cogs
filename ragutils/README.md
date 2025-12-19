## Advanced RAG Features
- Sentence-aware chunking (12-120 words) keeps context tight for long knowledge docs; toggle with `[p]ragutils enable chunking`
- Cross-encoder reranking (default `cross-encoder/ms-marco-MiniLM-L-6-v2`, threshold 0.33) upgrades cosine similarity; enable via `[p]ragutils enable reranking`
- MMR deduplication (lambda 0.5) diversifies results and reduces repeats; toggle with `[p]ragutils enable mmr`
- Keyword/numeric boosts lift answers containing numbers, dates, or workflow terms; works automatically when RAG is enabled
- Backends: ChromaDB by default or optional Qdrant (`[p]ragutils backend qdrant`) for larger deployments

**Quickstart**
- `[p]ragutils settings` to view status
- `[p]ragutils test "<query>"` to preview retrieval with the current pipeline
- `[p]ragutils threshold 0.33` to tune rerank cutoff

**Qdrant Migration**
1. Install `qdrant-client`
2. Ensure Qdrant is reachable at `http://localhost:6333` or set your URL with `[p]ragutils backend qdrant`
3. Run `[p]ragutils backend qdrant` to sync existing embeddings
4. Switch back anytime with `[p]ragutils backend chromadb`

**Performance Snapshot**
| Mode | Strength | Notes |
|------|----------|-------|
| ChromaDB (default) | Fast local similarity | Lowest overhead |
| Qdrant | Scales to larger corpora | Network hop; optional |
| RAG enhanced (rerank+MMR) | Higher relevance + diversity | Uses cross-encoder + extra compute |