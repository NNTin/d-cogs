"""Advanced retrieval augmentation utilities for the assistant cog.

This module implements a retrieval stack (chunking, reranking, MMR, and
multi-stage retrieval) for the assistant cog. All features are optional and can
be enabled via `RAGConfig`. Defaults retain the existing ChromaDB flow to avoid
breaking current setups.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import re
import typing as t

import discord
import numpy as np
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box, escape, pagify
from pydantic import BaseModel, Field

log = logging.getLogger("red.nntin.d-cogs.ragutils.rag")

try:
    import chromadb
except Exception:  # noqa: BLE001
    chromadb = None

try:  # Optional dependency
    from qdrant_client import QdrantClient
    from qdrant_client.http import models as qmodels
except Exception:  # noqa: BLE001
    QdrantClient = None
    qmodels = None

try:  # Optional dependency for reranking and MMR embeddings
    from sentence_transformers import CrossEncoder, SentenceTransformer
except Exception:  # noqa: BLE001
    CrossEncoder = None
    SentenceTransformer = None

try:  # Optional dependency for sentence tokenization
    import nltk
    from nltk.tokenize import sent_tokenize
except Exception:  # noqa: BLE001
    nltk = None
    sent_tokenize = None

try:  # Optional Groq fallback
    from openai import OpenAI
except Exception:  # noqa: BLE001
    OpenAI = None

NUMERIC_PATTERNS = [
    r"\d{1,2}/\d{1,2}/\d{2,4}",  # dates
    r"\d{1,2}:\d{2}",  # times
    r"\$?\d+(?:\.\d+)?",  # currency/decimals
    r"\d+%",  # percentages
]
PROCEDURE_PATTERNS = [r"\b(step|process|workflow|approval)\b"]
FORMULA_PATTERNS = [r"\b(calculation|formula|equation)\b"]

DEFAULT_BOOSTS = {
    "numeric": NUMERIC_PATTERNS,
    "procedure": PROCEDURE_PATTERNS,
    "formula": FORMULA_PATTERNS,
}

_reranker_model: CrossEncoder | None = None
_embedder_model: SentenceTransformer | None = None
_chroma_client = chromadb.Client() if chromadb else None


class DependencyMissingError(RuntimeError):
    """Raised when an optional dependency is required but missing."""


class RAGConfig(BaseModel):
    enable_reranking: bool = False
    enable_mmr: bool = False
    enable_chunking: bool = False
    rerank_threshold: float = 0.33
    rerank_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    chunk_min_words: int = 12
    chunk_max_words: int = 120
    mmr_lambda: float = 0.5
    use_qdrant: bool = False
    qdrant_url: str = "http://localhost:6333"
    boost_patterns: dict[str, list[str]] = Field(default_factory=lambda: DEFAULT_BOOSTS.copy())
    groq_api_key: str | None = None
    groq_model: str = "llama-3.1-8b-instant"
    last_migration: str | None = None
    migration_status: str = "never"

    model_config = {"arbitrary_types_allowed": True}


def _ensure_nltk():
    if not nltk or not sent_tokenize:
        raise DependencyMissingError(
            "nltk is required for sentence-level chunking. Install it via pip and ensure punkt is available."
        )
    try:
        nltk.data.find("tokenizers/punkt")
    except LookupError:
        try:
            nltk.download("punkt")
        except Exception as e:  # noqa: BLE001
            log.warning("Failed to download nltk punkt: %s", e)


def get_reranker(model_name: str) -> CrossEncoder:
    """Lazy-load and return the cross-encoder reranker."""
    global _reranker_model
    if _reranker_model:
        return _reranker_model
    if CrossEncoder is None:
        raise DependencyMissingError(
            "sentence-transformers is required for reranking. Install it to enable this feature."
        )
    _reranker_model = CrossEncoder(model_name)
    return _reranker_model


def get_embedder(model_name: str = "all-MiniLM-L6-v2") -> SentenceTransformer:
    """Lazy-load a light-weight embedder for MMR and similarity boosts."""
    global _embedder_model
    if _embedder_model:
        return _embedder_model
    if SentenceTransformer is None:
        raise DependencyMissingError(
            "sentence-transformers is required for MMR. Install it to enable this feature."
        )
    _embedder_model = SentenceTransformer(model_name)
    return _embedder_model


def _tokenize_sentences(text: str) -> list[str]:
    if not text:
        return []
    if sent_tokenize:
        try:
            _ensure_nltk()
            return sent_tokenize(text)
        except Exception as e:  # noqa: BLE001
            log.debug("Falling back to regex sentence split: %s", e)
    return re.split(r"(?<=[.!?])\s+", text.strip())


def _clean_content(text: str) -> str:
    text = re.sub(r"<@!?\\d+>", "", text)  # mentions
    text = re.sub(r"https?://\\S+", "", text)  # urls
    text = re.sub(r":[^:\\s]+:", "", text)  # emoji codes
    text = re.sub(r"[\\U00010000-\\U0010ffff]", "", text)  # astral emojis
    return text.strip()


def _same_topic_sentences(a: str, b: str) -> bool:
    if not a or not b:
        return False
    keywords = ["however", "therefore", "meanwhile", "additionally", "also"]
    if any(k in a.lower() for k in keywords) or any(k in b.lower() for k in keywords):
        return True
    a_tokens = set(re.findall(r"[a-zA-Z]{4,}", a.lower()))
    b_tokens = set(re.findall(r"[a-zA-Z]{4,}", b.lower()))
    if not a_tokens or not b_tokens:
        return False
    overlap = len(a_tokens & b_tokens) / max(len(a_tokens), 1)
    return overlap > 0.35


def chunk_text(text: str, config: RAGConfig) -> list[dict[str, t.Any]]:
    """Split text into sentence-level chunks with word bounds."""
    if not config.enable_chunking:
        return [{"text": text, "word_count": len(text.split()), "sentence_count": 1}]

    cleaned = _clean_content(text)
    sentences = _tokenize_sentences(cleaned)
    if not sentences:
        return []

    chunks: list[dict[str, t.Any]] = []
    current: list[str] = []
    for sent in sentences:
        sent_words = sent.split()
        if not sent_words:
            continue
        if not current:
            current.append(sent)
            continue

        combined = " ".join(current + [sent])
        word_count = len(combined.split())
        if word_count <= config.chunk_max_words and (word_count < config.chunk_min_words or _same_topic_sentences(current[-1], sent)):  # noqa: E501
            current.append(sent)
        else:
            chunk = " ".join(current).strip()
            if chunk:
                chunks.append(
                    {
                        "text": chunk,
                        "word_count": len(chunk.split()),
                        "sentence_count": len(current),
                    }
                )
            current = [sent]

    if current:
        chunk = " ".join(current).strip()
        if chunk:
            chunks.append(
                {
                    "text": chunk,
                    "word_count": len(chunk.split()),
                    "sentence_count": len(current),
                }
            )

    filtered = [c for c in chunks if config.chunk_min_words <= c["word_count"] <= config.chunk_max_words]
    result = filtered or chunks
    log.debug(
        "Chunked text into %s segment(s) using bounds %s-%s words.",
        len(result),
        config.chunk_min_words,
        config.chunk_max_words,
    )
    return result


def rerank_results(query: str, candidates: list[dict[str, t.Any]], config: RAGConfig) -> list[dict[str, t.Any]]:
    """Apply cross-encoder reranking to retrieved candidates."""
    if not candidates:
        return candidates
    reranked: list[dict[str, t.Any]] | None = None
    try:
        reranker = get_reranker(config.rerank_model)
    except DependencyMissingError as e:
        log.warning("Reranking skipped: %s", e)
    else:
        try:
            pairs = [[query, c["text"]] for c in candidates]
            scores = reranker.predict(pairs)
            for cand, score in zip(candidates, scores):
                cand["rerank_score"] = float(score)
            filtered = [c for c in candidates if c.get("rerank_score", 0.0) >= config.rerank_threshold]
            if not filtered and candidates:
                best = max(candidates, key=lambda x: x.get("rerank_score", 0.0))
                reranked = [best]
            else:
                filtered.sort(key=lambda c: c.get("rerank_score", 0.0), reverse=True)
                reranked = filtered
            if reranked:
                log.debug(
                    "Reranked %s candidates; top score %.3f",
                    len(reranked),
                    reranked[0].get("rerank_score", 0.0),
                )
        except Exception as e:  # noqa: BLE001
            log.warning("Reranking failed, returning originals: %s", e)

    if (not reranked or not reranked) and config.groq_api_key:
        reranked = groq_rerank(query, candidates, config)

    return reranked or candidates


def groq_rerank(query: str, candidates: list[dict[str, t.Any]], config: RAGConfig) -> list[dict[str, t.Any]]:
    """Fallback reranking using Groq's OpenAI-compatible API."""
    if not candidates or not config.groq_api_key:
        return candidates
    if OpenAI is None:
        log.warning("Groq reranking skipped: openai client not available.")
        return candidates

    try:
        client = OpenAI(api_key=config.groq_api_key, base_url="https://api.groq.com/openai/v1")
        payload = [{"name": c.get("name", f"cand-{i}"), "text": c.get("text", "")} for i, c in enumerate(candidates)]
        prompt = (
            "Rate relevance of each candidate chunk to the query on a 0-10 scale. "
            "Respond ONLY with a JSON array of numbers matching the candidate order."
        )
        response = client.chat.completions.create(
            model=config.groq_model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps({"query": query, "candidates": payload})},
            ],
            temperature=0,
        )
        content = response.choices[0].message.content or "[]"
        try:
            scores = json.loads(content)
        except json.JSONDecodeError:
            log.warning("Groq rerank response was not valid JSON: %s", content)
            return candidates

        ranked: list[dict[str, t.Any]] = []
        for cand, score in zip(candidates, scores):
            try:
                rerank_score = float(score) / 10
            except (TypeError, ValueError):
                rerank_score = 0.0
            cand["rerank_score"] = rerank_score
            ranked.append(cand)

        filtered = [c for c in ranked if c.get("rerank_score", 0.0) >= config.rerank_threshold]
        if not filtered and ranked:
            best = max(ranked, key=lambda x: x.get("rerank_score", 0.0))
            return [best]
        filtered.sort(key=lambda c: c.get("rerank_score", 0.0), reverse=True)
        if filtered:
            log.debug(
                "Groq reranked %s candidates; top score %.3f",
                len(filtered),
                filtered[0].get("rerank_score", 0.0),
            )
        return filtered or candidates
    except Exception as e:  # noqa: BLE001
        log.warning("Groq reranking failed: %s", e)
        return candidates


def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def mmr_rerank(candidates: list[dict[str, t.Any]], top_k: int, lambda_mult: float, query_text: str) -> list[dict[str, t.Any]]:  # noqa: E501
    """Apply Maximal Marginal Relevance to diversify candidates."""
    if not candidates:
        return candidates
    try:
        embedder = get_embedder()
    except DependencyMissingError as e:
        log.warning("MMR skipped: %s", e)
        return candidates

    texts = [query_text] + [c["text"] for c in candidates]
    embeddings = embedder.encode(texts)
    query_vec = np.array(embeddings[0])
    doc_vecs = [np.array(e) for e in embeddings[1:]]

    selected: list[int] = []
    remaining = list(range(len(candidates)))

    if remaining:
        best_initial = max(remaining, key=lambda idx: _cosine_sim(query_vec, doc_vecs[idx]))
        selected.append(best_initial)
        remaining.remove(best_initial)
        log.debug("MMR seed selection: %s", candidates[best_initial]["name"])

    while remaining and len(selected) < top_k:
        mmr_scores = []
        for idx in remaining:
            relevance = _cosine_sim(query_vec, doc_vecs[idx])
            diversity = max(_cosine_sim(doc_vecs[idx], doc_vecs[j]) for j in selected) if selected else 0
            score = lambda_mult * relevance - (1 - lambda_mult) * diversity
            mmr_scores.append((idx, score))
        if not mmr_scores:
            break
        next_idx = max(mmr_scores, key=lambda x: x[1])[0]
        selected.append(next_idx)
        remaining.remove(next_idx)
        log.debug("MMR selected: %s", candidates[next_idx]["name"])

    return [candidates[i] for i in selected]


def _contains_pattern(text: str, patterns: list[str]) -> bool:
    return any(re.search(pat, text, flags=re.IGNORECASE) for pat in patterns)


def apply_boosting(query: str, candidates: list[dict[str, t.Any]], config: RAGConfig) -> list[dict[str, t.Any]]:
    """Apply lightweight boosting for numeric and keyword matches."""
    if not candidates:
        return candidates
    boost_cfg = config.boost_patterns or DEFAULT_BOOSTS
    query_has_numbers = _contains_pattern(query, boost_cfg.get("numeric", NUMERIC_PATTERNS))

    for cand in candidates:
        text = cand.get("text", "")
        if _contains_pattern(text, boost_cfg.get("numeric", NUMERIC_PATTERNS)):
            cand["score"] = cand.get("score", 0.0) + 0.25
            if query_has_numbers:
                cand["score"] += 0.15
        if _contains_pattern(text, boost_cfg.get("procedure", PROCEDURE_PATTERNS)):
            cand["score"] = cand.get("score", 0.0) + 0.1
        if _contains_pattern(text, boost_cfg.get("formula", FORMULA_PATTERNS)):
            cand["score"] = cand.get("score", 0.0) + 0.1
    return candidates


class QdrantBackend:
    """Lightweight Qdrant wrapper mirroring the ChromaDB usage."""

    def __init__(self, url: str):
        if QdrantClient is None or qmodels is None:
            raise DependencyMissingError("qdrant-client is required for Qdrant backend support.")
        self.client = QdrantClient(url=url)

    def _collection(self, guild_id: int) -> str:
        return f"assistant-{guild_id}"

    def sync_embeddings(
        self,
        guild_id: int,
        embeddings: dict[str, t.Any],
        target_dimension: int | None = None,
        force_reset: bool = False,
    ):
        collection = self._collection(guild_id)
        dim = target_dimension or (len(next(iter(embeddings.values())).embedding) if embeddings else None)
        if dim is None:
            log.info("No embeddings to sync to Qdrant for guild %s", guild_id)
            return

        try:
            collections = self.client.get_collections()
            existing = {c.name for c in (collections.collections or [])}
        except Exception as e:  # noqa: BLE001
            log.warning("Failed to list Qdrant collections: %s", e)
            existing = set()

        if force_reset or collection not in existing:
            try:
                self.client.recreate_collection(
                    collection_name=collection,
                    vectors_config=qmodels.VectorParams(size=dim, distance=qmodels.Distance.COSINE),
                )
            except Exception as e:  # noqa: BLE001
                log.warning("Failed to reset Qdrant collection %s: %s", collection, e)

        points = []
        for name, em in embeddings.items():
            if target_dimension and len(em.embedding) != target_dimension:
                continue
            points.append(
                qmodels.PointStruct(
                    id=name,
                    vector=em.embedding,
                    payload=em.model_dump(exclude={"embedding"}),
                )
            )
        if points:
            self.client.upsert(collection_name=collection, points=points)

    def query_embeddings(
        self,
        guild_id: int,
        query_embedding: list[float],
        limit: int,
    ) -> list[dict[str, t.Any]]:
        collection = self._collection(guild_id)
        try:
            search = self.client.search(
                collection_name=collection,
                query_vector=query_embedding,
                limit=limit,
                with_payload=True,
                with_vectors=True,
            )
        except Exception as e:  # noqa: BLE001
            log.error("Qdrant search failed for guild %s: %s", guild_id, e)
            return []

        results = []
        for hit in search:
            payload = hit.payload or {}
            text = payload.get("text", "")
            dim = len(hit.vector) if hit.vector else 0
            results.append(
                {
                    "name": str(hit.id),
                    "text": text,
                    "score": float(hit.score),
                    "dimensions": dim,
                }
            )
        return results

    def add_embeddings(self, guild_id: int, embeddings: dict[str, t.Any]):
        if not embeddings:
            return
        points = [
            qmodels.PointStruct(
                id=name,
                vector=em.embedding,
                payload=em.model_dump(exclude={"embedding"}),
            )
            for name, em in embeddings.items()
        ]
        try:
            self.client.upsert(collection_name=self._collection(guild_id), points=points)
        except Exception as e:  # noqa: BLE001
            log.warning("Failed to add embeddings to Qdrant for guild %s: %s", guild_id, e)

    def delete_embeddings(self, guild_id: int, ids: list[str]):
        if not ids:
            return
        try:
            self.client.delete(
                collection_name=self._collection(guild_id),
                points_selector=qmodels.PointIdsList(points=ids),
            )
        except Exception as e:  # noqa: BLE001
            log.warning("Failed to delete embeddings from Qdrant for guild %s: %s", guild_id, e)


async def enhanced_retrieval(
    query: str,
    query_embedding: list[float],
    guild_id: int,
    conf: t.Any,
    rag_config: RAGConfig,
    top_n: int = 3,
    chroma_client: t.Any = None,
) -> list[tuple[str, str, float, int]]:
    """Pipeline: initial retrieval -> boosting -> reranking -> MMR."""
    top_pool = max(top_n * 4, 40)
    chroma_client = chroma_client or _chroma_client
    candidates: list[dict[str, t.Any]] = []
    q_length = len(query_embedding)

    if not query_embedding or not getattr(conf, "embeddings", None):
        return []

    if rag_config.use_qdrant or getattr(conf, "rag_backend", "").lower() == "qdrant":
        try:
            backend = QdrantBackend(getattr(conf, "qdrant_url", rag_config.qdrant_url))
            candidates = backend.query_embeddings(guild_id, query_embedding, limit=top_pool)
            log.debug("Using Qdrant backend for guild %s, retrieved %s candidates", guild_id, len(candidates))
        except (ConnectionError, TimeoutError, DependencyMissingError) as e:
            log.warning("Qdrant configured for guild %s but unavailable, falling back to ChromaDB: %s", guild_id, e)
        except Exception as e:  # noqa: BLE001
            log.warning("Qdrant retrieval failed for guild %s, falling back to ChromaDB: %s", guild_id, e)

    if not candidates:
        if chroma_client is None:
            log.warning("Chroma client unavailable; cannot retrieve embeddings.")
            return []
        try:
            collection = chroma_client.get_collection(f"assistant-{guild_id}")
        except Exception as e:  # noqa: BLE001
            log.info("Failed to get Chroma collection for guild %s: %s", guild_id, e)
            return []

        try:
            results = collection.query(query_embeddings=[query_embedding], n_results=top_pool)
        except Exception as e:  # noqa: BLE001
            log.error("Chroma query failed for guild %s: %s", guild_id, e)
            return []

        for idx in range(len(results.get("ids", [[]])[0])):
            embed_name = results["ids"][0][idx]
            embed_obj = conf.embeddings.get(embed_name)
            if not embed_obj:
                collection.delete(ids=[embed_name])
                continue
            distance = results["distances"][0][idx] if results.get("distances") else 0.0
            relatedness = 1 - distance
            candidates.append(
                {
                    "name": embed_name,
                    "text": embed_obj.text,
                    "score": relatedness,
                    "dimensions": len(embed_obj.embedding),
                }
            )

    original_candidates = list(candidates)
    filtered = []
    for cand in candidates:
        words = len(cand.get("text", "").split())
        if rag_config.chunk_min_words <= words <= rag_config.chunk_max_words:
            filtered.append(cand)
    candidates = filtered or candidates

    candidates = apply_boosting(query, candidates, rag_config)

    if rag_config.enable_reranking:
        candidates = rerank_results(query, candidates, rag_config)

    if rag_config.enable_mmr:
        candidates = mmr_rerank(candidates, top_k=top_n, lambda_mult=rag_config.mmr_lambda, query_text=query)

    if not candidates and original_candidates:
        original_candidates.sort(key=lambda c: c.get("score", 0.0), reverse=True)
        candidates = original_candidates[:top_n]

    if not candidates:
        return []

    candidates.sort(key=lambda c: c.get("rerank_score", c.get("score", 0.0)), reverse=True)
    final = candidates[:top_n]
    if final:
        log.debug(
            "Enhanced retrieval returned %s candidates (top score %.3f) for guild %s",
            len(final),
            final[0].get("score", 0.0),
            guild_id,
        )
    return [(c["name"], c["text"], c.get("score", 0.0), c.get("dimensions", q_length)) for c in final]


def test_chunking(text: str, rag_config: RAGConfig | None = None) -> list[dict[str, t.Any]]:
    conf = rag_config or RAGConfig(enable_chunking=True)
    return chunk_text(text, conf)


def test_reranking(query: str, candidates: list[str], rag_config: RAGConfig | None = None) -> list[dict[str, t.Any]]:
    conf = rag_config or RAGConfig(enable_reranking=True)
    base = [{"name": f"cand-{i}", "text": c, "score": 0.1 * (i + 1), "dimensions": 0} for i, c in enumerate(candidates)]
    return rerank_results(query, base, conf)


def test_mmr(candidates: list[str], query: str = "test", rag_config: RAGConfig | None = None) -> list[dict[str, t.Any]]:
    conf = rag_config or RAGConfig(enable_mmr=True)
    base = [{"name": f"cand-{i}", "text": c, "score": 0.5, "dimensions": 0} for i, c in enumerate(candidates)]
    return mmr_rerank(base, top_k=3, lambda_mult=conf.mmr_lambda, query_text=query)


async def benchmark_retrieval(
    query: str,
    query_embedding: list[float],
    guild_id: int,
    conf: t.Any,
    top_n: int = 3,
) -> dict[str, list[t.Tuple[str, str, float, int]]]:
    """Compare base Chroma retrieval, Qdrant, and RAG-enhanced results."""
    rag_conf = getattr(conf, "rag_config", None) or RAGConfig()
    chroma_results: list[tuple[str, str, float, int]] = []
    qdrant_results: list[tuple[str, str, float, int]] = []

    if _chroma_client:
        try:
            collection = _chroma_client.get_collection(f"assistant-{guild_id}")
            raw = collection.query(query_embeddings=[query_embedding], n_results=top_n)
            for idx in range(len(raw.get("ids", [[]])[0])):
                embed_name = raw["ids"][0][idx]
                metadata = raw["metadatas"][0][idx] if raw.get("metadatas") else {}
                distance = raw["distances"][0][idx] if raw.get("distances") else 0.0
                chroma_results.append((embed_name, metadata.get("text", ""), 1 - distance, len(query_embedding)))
        except Exception as e:  # noqa: BLE001
            log.warning("Benchmark chroma retrieval failed: %s", e)

    if rag_conf.use_qdrant or getattr(conf, "rag_backend", "") == "qdrant":
        try:
            backend = QdrantBackend(getattr(conf, "qdrant_url", rag_conf.qdrant_url))
            qdrant_candidates = backend.query_embeddings(guild_id, query_embedding, limit=top_n)
            qdrant_results = [
                (cand["name"], cand["text"], cand.get("score", 0.0), cand.get("dimensions", len(query_embedding)))
                for cand in qdrant_candidates
            ]
        except DependencyMissingError as e:
            log.warning("Benchmark qdrant retrieval skipped: %s", e)

    rag_results = await enhanced_retrieval(
        query,
        query_embedding,
        guild_id,
        conf,
        rag_conf,
        top_n=top_n,
        chroma_client=_chroma_client,
    )

    return {"chroma": chroma_results, "qdrant": qdrant_results, "rag": rag_results}


class RAGUtils(commands.Cog):
    """Red cog that exposes per-guild RAG configuration."""

    __author__ = "nntin"
    __version__ = "0.0.1"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=867530901, force_registration=True)
        default_guild = {
            "enable_reranking": False,
            "enable_mmr": False,
            "enable_chunking": False,
            "rerank_threshold": 0.33,
            "rerank_model": "cross-encoder/ms-marco-MiniLM-L-6-v2",
            "chunk_min_words": 12,
            "chunk_max_words": 120,
            "mmr_lambda": 0.5,
            "use_qdrant": False,
            "qdrant_url": "http://localhost:6333",
            "groq_api_key": None,
            "groq_model": "llama-3.1-8b-instant",
            "last_migration": None,
            "migration_status": "never",
        }
        self.config.register_guild(**default_guild)
        self._rag_configs: dict[int, RAGConfig] = {}
        self.assistant_cog = None

    def format_help_for_context(self, ctx: commands.Context) -> str:
        base = super().format_help_for_context(ctx)
        return f"{base}\n\nCog Author: {self.__author__}\nCog Version: {self.__version__}"

    async def _load_guild_config(self, guild_id: int) -> RAGConfig:
        data = await self.config.guild_from_id(guild_id).all()
        try:
            rag_conf = RAGConfig.model_validate(data)
        except Exception as exc:  # noqa: BLE001
            log.warning("Invalid RAG config for guild %s, using defaults: %s", guild_id, exc)
            rag_conf = RAGConfig()
        self._rag_configs[guild_id] = rag_conf
        return rag_conf

    async def _save_guild_config(self, guild_id: int, rag_config: RAGConfig):
        guild_conf = self.config.guild_from_id(guild_id)
        data = rag_config.model_dump()
        fields = [
            "enable_reranking",
            "enable_mmr",
            "enable_chunking",
            "rerank_threshold",
            "rerank_model",
            "chunk_min_words",
            "chunk_max_words",
            "mmr_lambda",
            "use_qdrant",
            "qdrant_url",
            "groq_api_key",
            "groq_model",
            "last_migration",
            "migration_status",
        ]
        for key in fields:
            await getattr(guild_conf, key).set(data.get(key))
        self._rag_configs[guild_id] = rag_config

    def get_rag_config(self, guild_id: int) -> RAGConfig | None:
        return self._rag_configs.get(guild_id)

    @commands.Cog.listener()
    async def on_assistant_cog_add(self, assistant_cog):
        log.info("Registering RAGUtils with assistant cog.")
        self.assistant_cog = assistant_cog
        loaded = 0
        for guild in self.bot.guilds:
            await self._load_guild_config(guild.id)
            loaded += 1
        log.info("RAGUtils registered with assistant; cached configs for %s guild(s).", loaded)

    async def cog_load(self):
        await self.bot.wait_until_red_ready()
        assistant = self.bot.get_cog("Assistant")
        if assistant:
            await self.on_assistant_cog_add(assistant)
        else:
            for guild in self.bot.guilds:
                await self._load_guild_config(guild.id)
        log.info("RAGUtils cog loaded.")

    async def cog_unload(self):
        self._rag_configs.clear()
        log.info("RAGUtils cog unloaded and cache cleared.")

    async def enable_feature(self, guild_id: int, feature: str) -> bool:
        feature_key = {"reranking": "enable_reranking", "mmr": "enable_mmr", "chunking": "enable_chunking"}.get(
            feature.lower()
        )
        if not feature_key:
            return False
        config = await self._load_guild_config(guild_id)
        setattr(config, feature_key, True)
        await self._save_guild_config(guild_id, config)
        return True

    async def disable_feature(self, guild_id: int, feature: str) -> bool:
        feature_key = {"reranking": "enable_reranking", "mmr": "enable_mmr", "chunking": "enable_chunking"}.get(
            feature.lower()
        )
        if not feature_key:
            return False
        config = await self._load_guild_config(guild_id)
        setattr(config, feature_key, False)
        await self._save_guild_config(guild_id, config)
        return True

    async def set_threshold(self, guild_id: int, threshold: float) -> bool:
        if not 0.0 <= threshold <= 1.0:
            return False
        config = await self._load_guild_config(guild_id)
        config.rerank_threshold = threshold
        await self._save_guild_config(guild_id, config)
        return True

    async def set_backend(self, guild_id: int, backend: str) -> bool:
        backend_normalized = backend.lower()
        if backend_normalized not in {"chromadb", "qdrant"}:
            return False
        config = await self._load_guild_config(guild_id)
        config.use_qdrant = backend_normalized == "qdrant"
        await self._save_guild_config(guild_id, config)
        return True

    async def set_mmr_lambda(self, guild_id: int, lambda_value: float) -> bool:
        if not 0.0 <= lambda_value <= 1.0:
            return False
        config = await self._load_guild_config(guild_id)
        config.mmr_lambda = lambda_value
        await self._save_guild_config(guild_id, config)
        return True

    async def set_chunk_size(self, guild_id: int, min_words: int, max_words: int) -> bool:
        if min_words < 1 or max_words < 1 or min_words > max_words:
            return False
        config = await self._load_guild_config(guild_id)
        config.chunk_min_words = min_words
        config.chunk_max_words = max_words
        await self._save_guild_config(guild_id, config)
        return True

    async def set_qdrant_url(self, guild_id: int, url: str) -> bool:
        if not url or not url.startswith(("http://", "https://")):
            return False
        config = await self._load_guild_config(guild_id)
        config.qdrant_url = url
        await self._save_guild_config(guild_id, config)
        return True

    async def sync_embeddings_to_qdrant(self, guild_id: int, force_reset: bool = False) -> tuple[bool, str]:
        rag_config = await self._load_guild_config(guild_id)
        if not self.assistant_cog:
            rag_config.migration_status = "failed"
            await self._save_guild_config(guild_id, rag_config)
            return False, "Assistant cog not loaded"
        conf = self.assistant_cog.db.get_conf(guild_id)
        if not getattr(conf, "embeddings", None):
            rag_config.migration_status = "failed"
            await self._save_guild_config(guild_id, rag_config)
            return False, "No embeddings to migrate"
        if not rag_config.qdrant_url:
            rag_config.migration_status = "failed"
            await self._save_guild_config(guild_id, rag_config)
            return False, "Qdrant URL not configured"
        if QdrantClient is None:
            rag_config.migration_status = "failed"
            await self._save_guild_config(guild_id, rag_config)
            return False, "qdrant-client not installed"

        try:
            backend = QdrantBackend(rag_config.qdrant_url)
            target_dim = len(next(iter(conf.embeddings.values())).embedding)
            await asyncio.to_thread(backend.sync_embeddings, guild_id, conf.embeddings, target_dim, force_reset)
            rag_config.last_migration = datetime.now(timezone.utc).isoformat()
            rag_config.migration_status = "success"
            await self._save_guild_config(guild_id, rag_config)
            return True, f"Migrated {len(conf.embeddings)} embeddings to Qdrant"
        except DependencyMissingError as e:
            log.error("Migration failed due to missing dependency for guild %s: %s", guild_id, e)
            rag_config.migration_status = "failed"
            await self._save_guild_config(guild_id, rag_config)
            return False, "qdrant-client not installed"
        except Exception as e:  # noqa: BLE001
            log.error("Migration to Qdrant failed for guild %s: %s", guild_id, e)
            rag_config.migration_status = "failed"
            await self._save_guild_config(guild_id, rag_config)
            return False, f"Migration failed: {e}"

    async def check_backend_health(self, guild_id: int) -> dict[str, t.Any]:
        rag_config = await self._load_guild_config(guild_id)
        health = {
            "backend": "qdrant" if rag_config.use_qdrant else "chromadb",
            "status": "unknown",
            "details": {},
            "error": None,
        }
        if not rag_config.use_qdrant:
            health["status"] = "healthy"
            health["details"] = {"type": "embedded", "note": "ChromaDB runs in-process"}
            return health

        if not rag_config.qdrant_url:
            health["status"] = "misconfigured"
            health["error"] = "URL not set"
            return health
        if QdrantClient is None:
            health["status"] = "unavailable"
            health["error"] = "qdrant-client not installed"
            return health

        try:
            backend = QdrantBackend(rag_config.qdrant_url)
            backend.client.get_collections()
            collection_name = f"assistant-{guild_id}"
            try:
                info = backend.client.get_collection(collection_name)
                point_count = info.points_count if hasattr(info, "points_count") else 0
                health["details"] = {
                    "url": rag_config.qdrant_url,
                    "collection": collection_name,
                    "documents": point_count,
                }
            except Exception:  # noqa: BLE001
                health["details"] = {"url": rag_config.qdrant_url, "collection": collection_name, "documents": 0}
            health["status"] = "healthy"
        except Exception as e:  # noqa: BLE001
            health["status"] = "unreachable"
            health["error"] = str(e)
            log.warning("Qdrant health check failed for guild %s: %s", guild_id, e)
        return health

    @commands.group(name="ragutils", invoke_without_command=True)
    @commands.admin_or_permissions(administrator=True)
    @commands.guild_only()
    async def ragutils_group(self, ctx: commands.Context):
        """Manage RAG configuration for this guild."""
        if not self.bot.get_cog("Assistant"):
            await ctx.send("Assistant cog is not loaded. Load it before configuring RAG.")
            return
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @ragutils_group.command(name="status")
    async def ragutils_status(self, ctx: commands.Context):
        """Show current RAG configuration for this guild."""
        config = self.get_rag_config(ctx.guild.id) or await self._load_guild_config(ctx.guild.id)
        embed = discord.Embed(title="RAG Utils Status", color=await ctx.embed_color())
        health = await self.check_backend_health(ctx.guild.id)
        backend_label = "Qdrant" if config.use_qdrant else "ChromaDB"
        if health.get("status") == "healthy":
            if config.use_qdrant:
                docs = health.get("details", {}).get("documents", 0)
                backend_label = f"Qdrant ✅ ({docs} docs)"
            else:
                backend_label = f"{backend_label} ✅"
        else:
            backend_label = f"{backend_label} ❌ ({health.get('status')})"
        feature_text = "\n".join(
            [
                f"Reranking: {'enabled' if config.enable_reranking else 'disabled'}",
                f"MMR: {'enabled' if config.enable_mmr else 'disabled'}",
                f"Chunking: {'enabled' if config.enable_chunking else 'disabled'}",
            ]
        )
        embed.add_field(name="Features", value=feature_text, inline=False)
        embed.add_field(
            name="Thresholds",
            value=f"Rerank threshold: {config.rerank_threshold}\nMMR lambda: {config.mmr_lambda}",
            inline=False,
        )
        embed.add_field(
            name="Backend",
            value=backend_label,
            inline=True,
        )
        if config.qdrant_url:
            embed.add_field(name="Qdrant URL", value=config.qdrant_url, inline=False)
        embed.add_field(
            name="Models",
            value=f"Rerank: {config.rerank_model}\nGroq: {config.groq_model}",
            inline=False,
        )
        embed.add_field(
            name="Dependencies",
            value="\n".join(
                [
                    f"sentence-transformers: {'available' if CrossEncoder and SentenceTransformer else 'missing'}",
                    f"qdrant-client: {'available' if QdrantClient else 'missing'}",
                ]
            ),
            inline=False,
        )
        if config.migration_status:
            status_emoji = {"success": "✅", "failed": "❌", "never": "⏸️"}.get(config.migration_status, "ℹ️")
            embed.add_field(name="Migration Status", value=f"{status_emoji} {config.migration_status}", inline=True)
        if config.last_migration:
            try:
                last_dt = datetime.fromisoformat(config.last_migration)
                embed.add_field(name="Last Migration", value=f"<t:{int(last_dt.timestamp())}:R>", inline=True)
            except Exception:  # noqa: BLE001
                embed.add_field(name="Last Migration", value=config.last_migration, inline=True)
        if config.use_qdrant and health.get("error"):
            embed.add_field(name="Backend Error", value=health["error"], inline=False)
        await ctx.send(embed=embed)

    @ragutils_group.command(name="settings", aliases=["config"])
    async def ragutils_settings(self, ctx: commands.Context):
        """Show current RAG configuration (alias for status)."""
        await self.ragutils_status(ctx)

    @ragutils_group.command(name="enable")
    async def ragutils_enable(self, ctx: commands.Context, feature: str):
        """Enable a RAG feature (reranking, mmr, chunking)."""
        success = await self.enable_feature(ctx.guild.id, feature)
        if success:
            await ctx.send(f"Enabled {feature.lower()} for this guild.")
        else:
            await ctx.send("Invalid feature. Choose from reranking, mmr, or chunking.")

    @ragutils_group.command(name="disable")
    async def ragutils_disable(self, ctx: commands.Context, feature: str):
        """Disable a RAG feature (reranking, mmr, chunking)."""
        success = await self.disable_feature(ctx.guild.id, feature)
        if success:
            await ctx.send(f"Disabled {feature.lower()} for this guild.")
        else:
            await ctx.send("Invalid feature. Choose from reranking, mmr, or chunking.")

    @ragutils_group.command(name="threshold")
    async def ragutils_threshold(self, ctx: commands.Context, value: float):
        """Set the rerank threshold (0.0 - 1.0).

        Higher values mean stricter filtering of reranked results.
        Default: 0.33
        """
        if not 0.0 <= value <= 1.0:
            await ctx.send("Threshold must be between 0.0 and 1.0.")
            return
        success = await self.set_threshold(ctx.guild.id, value)
        if not success:
            await ctx.send("Failed to update threshold. Ensure the value is between 0.0 and 1.0.")
            return
        await ctx.send(f"Rerank threshold set to {value}. Higher values filter more results after reranking.")

    @ragutils_group.command(name="backend")
    async def ragutils_backend(self, ctx: commands.Context, backend: str):
        """Switch vector database backend.

        Options: chromadb, qdrant
        Default: chromadb
        """
        backend_normalized = backend.lower()
        if backend_normalized not in {"chromadb", "qdrant"}:
            await ctx.send("Invalid backend. Choose either chromadb or qdrant.")
            return
        if backend_normalized == "qdrant" and QdrantClient is None:
            await ctx.send("qdrant-client is not installed. Install it to use the Qdrant backend.")
            return
        success = await self.set_backend(ctx.guild.id, backend_normalized)
        if not success:
            await ctx.send("Failed to update backend. Please try again.")
            return
        message = f"Backend set to {backend_normalized}."
        config = self.get_rag_config(ctx.guild.id) or await self._load_guild_config(ctx.guild.id)
        if backend_normalized == "qdrant" and not getattr(config, "qdrant_url", ""):
            message += " Warning: Qdrant URL is not configured."
        await ctx.send(message)

    @ragutils_group.command(name="test")
    @commands.bot_has_permissions(embed_links=True)
    async def ragutils_test(self, ctx: commands.Context, *, query: str):
        """Test the RAG retrieval pipeline with a query.

        This will show you what embeddings would be retrieved for the given query
        using the current RAG configuration.
        """
        assistant = self.bot.get_cog("Assistant")
        if not assistant:
            await ctx.send("Assistant cog is not loaded. Load it before testing RAG.")
            return

        conf = assistant.db.get_conf(ctx.guild)
        if not conf.embeddings:
            await ctx.send("You do not have any embeddings configured.")
            return
        top_n = getattr(conf, "top_n", 0) or 0
        if top_n <= 0:
            await ctx.send("Top N is set to 0 so no embeddings will be returned.")
            return

        rag_config = self.get_rag_config(ctx.guild.id) or await self._load_guild_config(ctx.guild.id)

        async with ctx.typing():
            try:
                query_embedding = await assistant.request_embedding(query, conf)
            except Exception as exc:  # noqa: BLE001
                log.exception("Failed to generate query embedding for guild %s: %s", ctx.guild.id, exc)
                await ctx.send("Failed to generate embedding for that query.")
                return

            if not query_embedding:
                await ctx.send("Failed to generate embedding for that query.")
                return

            try:
                results = await enhanced_retrieval(
                    query,
                    query_embedding,
                    ctx.guild.id,
                    conf,
                    rag_config,
                    top_n=top_n,
                )
            except DependencyMissingError as exc:
                await ctx.send(f"Missing dependency needed for retrieval: {exc}")
                return
            except Exception as exc:  # noqa: BLE001
                log.exception("Enhanced retrieval test failed for guild %s: %s", ctx.guild.id, exc)
                await ctx.send("Something went wrong while testing the RAG pipeline.")
                return

        if not results:
            await ctx.send("No embeddings matched this query with the current RAG settings.")
            return

        feature_flags = [
            flag
            for flag, enabled in [
                ("reranking", rag_config.enable_reranking),
                ("mmr", rag_config.enable_mmr),
                ("chunking", rag_config.enable_chunking),
            ]
            if enabled
        ]
        backend_label = (
            "Qdrant"
            if rag_config.use_qdrant or getattr(conf, "rag_backend", "").lower() == "qdrant"
            else "ChromaDB"
        )
        footer_text = (
            f"{len(results)} result(s) • Backend: {backend_label} • Features: {', '.join(feature_flags) if feature_flags else 'none'}"  # noqa: E501
        )

        color = await ctx.embed_color()
        for name, em, score, dimension in results:
            base_text = (
                f"`Entry Name:  `{escape(str(name))}\n"
                f"`Relatedness: `{round(score, 4)}\n"
                f"`Dimensions:  `{dimension}\n"
            )
            for page in pagify(em, page_length=4000):
                escaped = escape(page)
                boxed = box(escaped)
                embed = discord.Embed(description=base_text + boxed, color=color)
                embed.set_footer(text=footer_text)
                await ctx.send(embed=embed)

    @ragutils_group.command(name="mmrlambda")
    async def ragutils_mmr_lambda(self, ctx: commands.Context, value: float):
        """Set MMR lambda parameter (0.0 - 1.0).

        Controls diversity vs relevance tradeoff.
        0.0 = maximum diversity, 1.0 = maximum relevance
        Default: 0.5
        """
        if not 0.0 <= value <= 1.0:
            await ctx.send("MMR lambda must be between 0.0 and 1.0.")
            return
        success = await self.set_mmr_lambda(ctx.guild.id, value)
        if not success:
            await ctx.send("Failed to update MMR lambda. Ensure the value is between 0.0 and 1.0.")
            return
        await ctx.send(
            f"MMR lambda set to {value}. Lower values favor diversity; higher values favor relevance in results."
        )

    @ragutils_group.command(name="chunksize")
    async def ragutils_chunk_size(self, ctx: commands.Context, min_words: int, max_words: int):
        """Set word count range for chunk filtering.

        Default: 12-120 words
        """
        if min_words < 1 or max_words < 1 or min_words > max_words:
            await ctx.send("Chunk size bounds must be positive and the minimum cannot exceed the maximum.")
            return
        success = await self.set_chunk_size(ctx.guild.id, min_words, max_words)
        if not success:
            await ctx.send("Failed to update chunk size. Please provide valid positive bounds.")
            return
        await ctx.send(f"Chunk word range set to {min_words}-{max_words} words.")

    @ragutils_group.command(name="qdranturl")
    async def ragutils_qdrant_url(self, ctx: commands.Context, url: str):
        """Set Qdrant server URL.

        Default: http://localhost:6333
        """
        if not url.startswith(("http://", "https://")):
            await ctx.send("Please provide a valid URL (including http/https).")
            return
        success = await self.set_qdrant_url(ctx.guild.id, url)
        if not success:
            await ctx.send("Failed to update Qdrant URL. Please provide a valid URL.")
            return
        await ctx.send(f"Qdrant URL set to {url}.")

    @ragutils_group.command(name="migrate")
    @commands.bot_has_permissions(embed_links=True)
    async def ragutils_migrate(self, ctx: commands.Context, force_reset: bool = False):
        """Migrate embeddings from ChromaDB to Qdrant.

        This copies all embeddings from the Assistant cog's ChromaDB storage
        to the configured Qdrant server. Existing Qdrant collections will be
        updated unless force_reset is True.

        Arguments:
            force_reset: If True, recreate the Qdrant collection from scratch.
                         Default: False (incremental update)

        Example:
            [p]ragutils migrate
            [p]ragutils migrate True
        """
        if not self.assistant_cog:
            await ctx.send("Assistant cog not loaded. Load it first.")
            return
        rag_config = await self._load_guild_config(ctx.guild.id)
        if not rag_config.use_qdrant:
            await ctx.send(
                "Warning: Backend is set to ChromaDB. Switch to Qdrant first with [p]ragutils backend qdrant"
            )
        msg = await ctx.send("Starting migration to Qdrant...")
        try:
            success, message = await self.sync_embeddings_to_qdrant(ctx.guild.id, force_reset)
        except Exception as exc:  # noqa: BLE001
            log.error("Migration command failed for guild %s: %s", ctx.guild.id, exc)
            success, message = False, f"Migration failed: {exc}"

        status_label = "Success" if success else "Failed"
        color = discord.Color.green() if success else discord.Color.red()
        embed = discord.Embed(title="Embedding Migration", color=color)
        embed.add_field(name="Status", value=status_label, inline=True)
        embed.add_field(name="Details", value=message, inline=False)
        embed.add_field(name="Backend", value=rag_config.qdrant_url or "Not configured", inline=False)
        embed.add_field(name="Force Reset", value="Yes" if force_reset else "No", inline=True)
        await msg.edit(content=None, embed=embed)

    @ragutils_group.command(name="health")
    @commands.bot_has_permissions(embed_links=True)
    async def ragutils_health(self, ctx: commands.Context):
        """Check the health of the configured vector database backend.

        Shows connection status, collection info, and document counts.
        Useful for diagnosing Qdrant connectivity issues.
        """
        async with ctx.typing():
            health = await self.check_backend_health(ctx.guild.id)

        status = health.get("status", "unknown")
        if status == "healthy":
            color = discord.Color.green()
            emoji = "✅"
        elif status == "misconfigured":
            color = discord.Color.gold()
            emoji = "⚠️"
        else:
            color = discord.Color.red()
            emoji = "❌"

        embed = discord.Embed(title="RAG Backend Health", color=color, timestamp=datetime.now(timezone.utc))
        embed.add_field(name="Backend", value=str(health.get("backend", "unknown")).upper(), inline=True)
        embed.add_field(name="Status", value=f"{emoji} {status.title()}", inline=True)
        if health.get("error"):
            embed.add_field(name="Error", value=health["error"], inline=False)
        if health.get("details"):
            detail_text = "\n".join(f"{k.title()}: {v}" for k, v in health["details"].items())
            embed.add_field(name="Details", value=detail_text, inline=False)
        await ctx.send(embed=embed)

    async def get_enhanced_embeddings(
        self,
        guild_id: int,
        query: str,
        query_embedding: list[float],
        conf: t.Any,
        top_n: int = 3,
    ) -> list[tuple[str, str, float, int]]:
        rag_config = self.get_rag_config(guild_id)
        if rag_config is None:
            rag_config = await self._load_guild_config(guild_id)
        if not rag_config or not any(
            [rag_config.enable_reranking, rag_config.enable_mmr, rag_config.enable_chunking, rag_config.use_qdrant]
        ):
            return []
        try:
            return await enhanced_retrieval(
                query=query,
                query_embedding=query_embedding,
                guild_id=guild_id,
                conf=conf,
                rag_config=rag_config,
                top_n=top_n,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Enhanced retrieval failed for guild %s: %s", guild_id, exc)
            return []
