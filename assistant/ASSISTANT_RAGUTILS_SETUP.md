# Assistant + RAG Utils: Initial Setup & Smoke Tests

Assumes Red is running, the cogs `assistant` and `ragutils` are loaded, and your prefix is `[p]` (replace it if different).

## Prereqs (Ollama only)
- Ensure `ollama` is running on the host.
- Pull the models we will use:\
  `ollama pull gemma3` (chat) and `ollama pull qwen3-embedding` (embeddings).
- Python deps: `chromadb`, `sentence-transformers`, `nltk`, `qdrant-client` (only if using Qdrant), `ollama==0.6.1`. In Red you can run `[p]pipinstall sentence-transformers nltk qdrant-client` if needed.
- If you want chunking: NLTK needs the `punkt` tokenizer. Run once on the host: `python -m nltk.downloader punkt`.

## Assistant: Minimum Setup (Ollama)
1) Point Assistant to Ollama (no OpenAI key needed):\
   `[p]assistant endpointoverride http://localhost:11434/`
2) Pick models:
   - Chat: `[p]assistant model gemma3`
   - Embeddings: `[p]assistant embedmodel qwen3-embedding` then refresh stored vectors if you had any: `[p]assistant refreshembeds`
3) Enable retrieval context:
   - How many embeddings to include: `[p]assistant topn 3`
   - Relatedness cutoff: `[p]assistant relatedness 0.25` (raise for stricter filtering)
   - Embedding method: `[p]assistant embedmethod static` (embeddings are applied in front of each user message)
4) Optional quality-of-life:
   - Set a channel for auto-replies: `[p]assistant channel #ai-chat`
   - Allow mentions anywhere: `[p]assistant mentionrespond`
   - Check the current config: `[p]assistant view`

## Seed Some Embeddings (needed for RAG testing)
1) Run `[p]embeddings` to open the menu.
2) Click **Add**, supply a short title (e.g., `handbook-wifi`) and paste a few sentences of reference text.
3) Save. Repeat for a second entry so you can see reranking/MMR effects.

## Quick Assistant Test
1) In the same guild/channel, ask: `[p]chat What are the Wi‑Fi instructions?`
2) Verify the reply uses your stored text. If not, check `[p]convostats` to ensure a convo exists, and confirm `topn` is >0.

## RAG Utils Setup
1) Inspect status: `[p]ragutils status` (shows backend, feature toggles, dependency availability).
2) Enable the enhancements you want:
   - Sentence chunking: `[p]ragutils enable chunking` (12-120 word slices by default; adjust with `[p]ragutils chunksize 20 160`).
   - Cross-encoder rerank: `[p]ragutils enable reranking` then tune cutoff `[p]ragutils threshold 0.33`.
   - MMR diversity: `[p]ragutils enable mmr` and tune lambda `[p]ragutils mmrlambda 0.5`.
3) Optional backend swap to Qdrant:
   - Point to your server: `[p]ragutils qdranturl http://localhost:6333`
   - Switch backend: `[p]ragutils backend qdrant`
   - Migrate existing embeddings: `[p]ragutils migrate` (use `True` to force-reset the collection)
   - Check connectivity: `[p]ragutils health`

## RAG Utils Test
1) Make sure you already added embeddings and that Assistant `topn` is above 0.
2) Run a dry run retrieval: `[p]ragutils test "How do we join the Wi‑Fi?"`
   - You should see embeds with the matched entries, scores, and which features were active.
3) Ask the assistant a question that should hit your embeddings: `[p]chat Summarize the Wi‑Fi steps in one line.`
   - If nothing is pulled, re-check `[p]ragutils status` and ensure rerank/MMR dependencies are installed.

## Troubleshooting
- Missing deps in status: install via `[p]pipinstall` (or system pip) and reload the cog.
- “No embeddings configured” in tests: add at least one entry via `[p]embeddings`.
- “Top N is set to 0”: set `[p]assistant topn 3` and rerun the test.
