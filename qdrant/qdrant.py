import hashlib
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Literal

import discord
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.config import Config
from redbot.core.utils.chat_formatting import box

try:
    from qdrant_client import QdrantClient
    from qdrant_client import models as qmodels

    QDRANT_AVAILABLE = True
except ImportError:
    QDRANT_AVAILABLE = False
    QdrantClient = None
    qmodels = None

RequestType = Literal["discord_deleted_user", "owner", "user", "user_strict"]

log = logging.getLogger("red.d_cogs.qdrant")


class qdrant(commands.Cog):
    """
    Implements the ChainStore abstraction from langcore.

    Provides vector storage with per-cog collection isolation. Each extension cog
    writes to its own collection (named by cog) to avoid collisions.

    Basic Usage:
        # Add embeddings (caller handles chunking)
        store = langcore_cog.get_store()
        provider = langcore_cog.get_provider("ollama")
        embedding = await provider.embed("sample text", guild)
        await store.add_embedding(guild, "my_cog", "doc1", "sample text", embedding)

        # Retrieve similar texts
        results = await store.retrieve_texts(
            guild, "my_cog", "search query", top_n=5, provider=provider
        )

    For chunking guidelines, see retrieve_texts() docstring.
    """

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(
            self,
            identifier=257263088,
            force_registration=True,
        )
        self.config.register_global(
            endpoint="http://localhost:6333",
            api_key="",
            default_dimension=1536,
            distance_metric="Cosine",
        )
        self.config.register_guild(
            collection_overrides={},
        )
        self._client: Optional[QdrantClient] = None
        self._collection_cache: Dict[str, Dict[str, Any]] = {}
        self.chain_store_provider = None

    async def red_delete_data_for_user(self, *, requester: RequestType, user_id: int) -> None:
        # No user-specific data is stored locally by this cog.
        return

    def _name_to_uuid(self, name: str) -> str:
        hashed = hashlib.sha1(name.encode("utf-8")).hexdigest()
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, hashed))

    def _collection_name(self, guild: discord.Guild, collection: str) -> str:
        return f"{collection}_{guild.id}"

    async def _get_collection_config(self, guild_id: int, collection: str) -> Dict[str, Any]:
        overrides = await self.config.guild_from_id(guild_id).collection_overrides()
        override = overrides.get(collection, {})
        default_dimension = await self.config.default_dimension()
        default_metric = await self.config.distance_metric()
        return {
            "dimension": override.get("dimension", default_dimension),
            "metric": override.get("metric", default_metric),
        }

    async def _get_client(self) -> QdrantClient:
        if not QDRANT_AVAILABLE:
            raise commands.UserFeedbackCheckFailure(
                "qdrant-client not installed. Install via: pip install qdrant-client"
            )
        if self._client:
            return self._client
        endpoint = await self.config.endpoint()
        api_key = await self.config.api_key()
        try:
            self._client = QdrantClient(url=endpoint, api_key=api_key or None)
        except Exception as exc:  # pragma: no cover - defensive
            log.error("Failed to initialize Qdrant client: %s", exc)
            raise commands.UserFeedbackCheckFailure(
                "Failed to connect to Qdrant. Check endpoint and API key."
            ) from exc
        return self._client

    async def _ensure_collection(self, collection_name: str, dimension: int, metric: str) -> bool:
        cached = self._collection_cache.get(collection_name)
        if cached and cached.get("dimension") == dimension and cached.get("metric") == metric:
            return True
        try:
            client = await self._get_client()
        except commands.UserFeedbackCheckFailure:
            return False
        try:
            exists = client.collection_exists(collection_name)
        except Exception as exc:
            log.warning("Failed to check collection %s: %s", collection_name, exc)
            return False
        if not exists:
            try:
                distance = qmodels.Distance[metric]
            except Exception:
                log.warning("Invalid distance metric %s for collection %s", metric, collection_name)
                return False
            try:
                client.create_collection(
                    collection_name=collection_name,
                    vectors_config=qmodels.VectorParams(size=dimension, distance=distance),
                )
                log.info(
                    "Created Qdrant collection %s with dim=%s metric=%s",
                    collection_name,
                    dimension,
                    metric,
                )
            except Exception as exc:
                log.warning("Failed to create collection %s: %s", collection_name, exc)
                return False
        self._collection_cache[collection_name] = {"dimension": dimension, "metric": metric}
        return True

    async def add_embedding(
        self,
        guild: discord.Guild,
        collection: str,
        name: str,
        text: str,
        embedding: List[float],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        collection_name = self._collection_name(guild, collection)
        cfg = await self._get_collection_config(guild.id, collection)
        metric = cfg.get("metric", "Cosine")
        dimension = cfg.get("dimension", len(embedding))
        if len(embedding) != dimension:
            log.warning(
                "Embedding dimension %s does not match configured dimension %s for collection %s",
                len(embedding),
                dimension,
                collection_name,
            )
            return False
        ensured = await self._ensure_collection(collection_name, dimension, metric)
        if not ensured:
            return False
        try:
            client = await self._get_client()
            payload = {
                "text": text,
                "name": name,
                "source": f"{collection}:{name}",
                "metadata": metadata or {},
                "timestamp": datetime.utcnow().isoformat(),
            }
            point = qmodels.PointStruct(
                id=self._name_to_uuid(name),
                vector=embedding,
                payload=payload,
            )
            client.upsert(collection_name=collection_name, points=[point])
            return True
        except Exception as exc:
            log.error("Failed to upsert into collection %s: %s", collection_name, exc)
            return False

    async def delete_embeddings(
        self,
        guild: discord.Guild,
        collection: str,
        names: List[str],
    ) -> int:
        coll_name = self._collection_name(guild, collection)
        try:
            client = await self._get_client()
            if not client.collection_exists(coll_name):
                return 0
            filter = qmodels.Filter(
                must=[qmodels.FieldCondition(key="name", match=qmodels.MatchAny(any=names))]
            )
            scroll_res = client.scroll(collection_name=coll_name, scroll_filter=filter, limit=1000)
            ids = [str(p.id) for p in scroll_res.points]
            if ids:
                client.delete_batch(collection_name=coll_name, ids=ids)
            return len(ids)
        except Exception as exc:
            log.error("Failed to delete embeddings from %s: %s", coll_name, exc)
            return 0

    async def query(
        self,
        guild: discord.Guild,
        collection: str,
        query_embedding: List[float],
        top_n: int = 3,
        min_score: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        collection_name = self._collection_name(guild, collection)
        try:
            client = await self._get_client()
            if not client.collection_exists(collection_name):
                return []
        except Exception as exc:
            log.warning("Qdrant query failed during collection check %s: %s", collection_name, exc)
            return []
        try:
            results = client.query_points(
                collection_name=collection_name,
                query=query_embedding,
                limit=top_n,
                with_payload=True,
                with_vectors=False,
            )
        except Exception as exc:
            log.error("Qdrant query failed for %s: %s", collection_name, exc)
            return []

        hits: List[Dict[str, Any]] = []
        for hit in getattr(results, "points", results):
            payload = hit.payload or {}
            if min_score is not None and getattr(hit, "score", 0) < min_score:
                continue
            hits.append(
                {
                    "name": payload.get("name"),
                    "text": payload.get("text"),
                    "score": getattr(hit, "score", None),
                    "metadata": payload.get("metadata", {}),
                    "dimensions": len(query_embedding),
                }
        )
        return hits

    async def retrieve_texts(
        self,
        guild: discord.Guild,
        collection: str,
        query_text: str,
        top_n: int = 3,
        min_score: Optional[float] = None,
        provider: Any = None,  # ChainProvider from langcore.abc
    ) -> List[Dict[str, Any]]:
        """High-level retrieval: embed query text and search the collection.

        This method combines embedding generation with similarity search for convenience.
        Callers are responsible for chunking their documents before adding embeddings.

        Text Chunking Guidelines:
        -------------------------
        Before calling add_embedding, chunk your text appropriately:

        - **Sentence-aware chunking**: Split on sentence boundaries (use nltk.sent_tokenize)
        - **Topic coherence**: Keep related sentences together (check keyword overlap)
        - **Word bounds**: Target 50-200 words per chunk for optimal retrieval
        - **Context preservation**: Include enough context for standalone understanding

        For conversation data:
        - Keep question + immediate answer together
        - Split on speaker changes when topic shifts
        - Preserve tool call + result pairs

        For technical docs:
        - Chunk by section/subsection boundaries
        - Keep code blocks with their explanations
        - Preserve heading context

        Args:
            guild: Guild context for multi-tenant isolation.
            collection: Collection name (typically cog name for namespace isolation).
            query_text: Natural language query to search for.
            top_n: Maximum number of results to return (default 3).
            min_score: Optional minimum similarity score threshold.
            provider: ChainProvider instance for embedding generation (from langcore).
                      If None, raises RuntimeError.

        Returns:
            List of result dictionaries with keys: name, text, score, metadata, dimensions.
            Results are sorted by similarity score (highest first).

        Raises:
            RuntimeError: If provider is None or embedding generation fails.
            commands.UserFeedbackCheckFailure: If Qdrant connection fails.

        Example:
            >>> langcore_cog = bot.get_cog("langcore")
            >>> provider = langcore_cog.get_provider("ollama")
            >>> qdrant_cog = bot.get_cog("qdrant")
            >>> results = await qdrant_cog.retrieve_texts(
            ...     guild=ctx.guild,
            ...     collection="my_cog",
            ...     query_text="How do I configure the bot?",
            ...     top_n=5,
            ...     min_score=0.7,
            ...     provider=provider,
            ... )
            >>> for result in results:
            ...     print(f"{result['name']}: {result['score']:.3f}")
        """
        if provider is None:
            raise RuntimeError(
                "ChainProvider required for retrieve_texts. "
                "Get provider via: langcore_cog.get_provider('ollama')"
            )

        try:
            query_embedding = await provider.embed(query_text, guild)
        except Exception as exc:
            log.error("Failed to generate embedding for query: %s", exc)
            raise RuntimeError(f"Embedding generation failed: {exc}") from exc

        if not query_embedding:
            log.warning("Empty embedding returned for query: %s", query_text)
            return []

        results = await self.query(
            guild=guild,
            collection=collection,
            query_embedding=query_embedding,
            top_n=top_n,
            min_score=min_score,
        )

        log.debug(
            "Retrieved %s results for collection=%s guild=%s (query: %s...)",
            len(results),
            collection,
            guild.id,
            query_text[:50],
        )

        return results

    def _build_chain_store_provider(self):
        try:
            from langcore.abc import ChainStore
        except Exception as exc:
            log.warning("Could not import ChainStore from langcore: %s", exc)
            return None

        cog = self

        class _QdrantChainStore(ChainStore):
            async def add_embedding(
                self,
                guild: discord.Guild,
                collection: str,
                name: str,
                text: str,
                embedding: List[float],
                metadata: Optional[Dict[str, Any]] = None,
            ) -> bool:
                return await cog.add_embedding(
                    guild=guild,
                    collection=collection,
                    name=name,
                    text=text,
                    embedding=embedding,
                    metadata=metadata,
                )

            async def delete_embeddings(
                self,
                guild: discord.Guild,
                collection: str,
                names: List[str],
            ) -> int:
                return await cog.delete_embeddings(
                    guild=guild,
                    collection=collection,
                    names=names,
                )

            async def query(
                self,
                guild: discord.Guild,
                collection: str,
                query_embedding: List[float],
                top_n: int = 3,
                min_score: Optional[float] = None,
            ) -> List[Dict[str, Any]]:
                return await cog.query(
                    guild=guild,
                    collection=collection,
                    query_embedding=query_embedding,
                    top_n=top_n,
                    min_score=min_score,
                )

            async def retrieve_texts(
                self,
                guild: discord.Guild,
                collection: str,
                query_text: str,
                top_n: int = 3,
                min_score: Optional[float] = None,
                provider: Any = None,
            ) -> List[Dict[str, Any]]:
                return await cog.retrieve_texts(
                    guild=guild,
                    collection=collection,
                    query_text=query_text,
                    top_n=top_n,
                    min_score=min_score,
                    provider=provider,
                )

        return _QdrantChainStore()

    def _refresh_provider(self) -> None:
        self.chain_store_provider = self._build_chain_store_provider()

    async def check_health(self) -> tuple[bool, Optional[str]]:
        try:
            client = await self._get_client()
            client.get_collections()
            return True, None
        except Exception as exc:
            log.warning("Qdrant health check failed: %s", exc)
            return False, str(exc)

    async def get_collection_info(self, collection_name: str) -> Optional[Dict[str, Any]]:
        try:
            client = await self._get_client()
            info = client.get_collection(collection_name=collection_name)
            return {
                "name": collection_name,
                "vectors_count": getattr(info, "vectors_count", None),
                "points_count": getattr(info, "points_count", None),
                "config": getattr(info, "config", None),
            }
        except Exception as exc:
            log.warning("Failed to get collection info for %s: %s", collection_name, exc)
            return None

    async def cog_load(self) -> None:
        if not QDRANT_AVAILABLE:
            log.error("qdrant-client not installed. Skipping initialization.")
            return
        await self._get_client()
        ok, err = await self.check_health()
        if not ok:
            log.warning("Qdrant health check failed: %s", err)
        langcore_cog = self.bot.get_cog("langcore")
        if langcore_cog:
            self._refresh_provider()
            if self.chain_store_provider:
                try:
                    langcore_cog.register_chain_store(self.chain_store_provider)
                    log.info("Registered Qdrant ChainStore with langcore.")
                except Exception as exc:
                    log.warning("Failed to register ChainStore with langcore: %s", exc)

    async def cog_unload(self) -> None:
        langcore_cog = self.bot.get_cog("langcore")
        if langcore_cog:
            try:
                langcore_cog.unregister_chain_store()
                log.info("Unregistered Qdrant ChainStore from langcore on unload.")
            except Exception as exc:
                log.warning("Failed to unregister ChainStore from langcore on unload: %s", exc)
        self._client = None
        self._collection_cache.clear()
        self.chain_store_provider = None
        log.info("Qdrant cog unloaded.")

    @commands.Cog.listener()
    async def on_langcore_cog_add(self, langcore_cog) -> None:
        if getattr(langcore_cog, "qualified_name", "") != "langcore":
            return
        self._refresh_provider()
        if self.chain_store_provider:
            try:
                langcore_cog.register_chain_store(self.chain_store_provider)
                log.info("Registered Qdrant ChainStore after langcore load.")
            except Exception as exc:
                log.warning("Failed to register ChainStore on langcore add: %s", exc)

    @commands.Cog.listener()
    async def on_langcore_cog_remove(self, langcore_cog=None) -> None:
        langcore_cog = langcore_cog or self.bot.get_cog("langcore")
        if getattr(langcore_cog, "qualified_name", "") != "langcore":
            return
        try:
            langcore_cog.unregister_chain_store()
            log.info("Unregistered Qdrant ChainStore after langcore unload.")
        except Exception as exc:
            log.warning("Failed to unregister ChainStore on langcore remove: %s", exc)

    @commands.group(name="qdrant")
    @commands.admin_or_permissions(administrator=True)
    @commands.guild_only()
    async def qdrant_group(self, ctx: commands.Context) -> None:
        """Manage Qdrant settings and collections."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @qdrant_group.command(name="settings")
    async def qdrant_settings(self, ctx: commands.Context) -> None:
        endpoint = await self.config.endpoint()
        api_key = await self.config.api_key()
        default_dimension = await self.config.default_dimension()
        distance_metric = await self.config.distance_metric()
        msg = (
            f"Endpoint: {endpoint}\n"
            f"API Key Set: {'Yes' if api_key else 'No'}\n"
            f"Default Dimension: {default_dimension}\n"
            f"Distance Metric: {distance_metric}"
        )
        await ctx.send(box(msg, lang="ini"))

    @qdrant_group.command(name="stats")
    async def qdrant_stats(self, ctx: commands.Context) -> None:
        healthy, error = await self.check_health()
        status = "Healthy" if healthy else f"Unhealthy: {error}"
        version = "unknown"
        try:
            client = await self._get_client()
            service_info = getattr(client, "get_locks", None)
            if service_info is not None:
                version = getattr(service_info(), "version", version)
        except Exception:
            pass
        msg = f"Health: {status}\nVersion: {version}"
        await ctx.send(msg)

    @qdrant_group.command(name="collections")
    async def qdrant_collections(self, ctx: commands.Context) -> None:
        try:
            client = await self._get_client()
            collections = client.get_collections()
            names = []
            for coll in getattr(collections, "collections", []):
                info = await self.get_collection_info(coll.name)
                names.append(
                    f"{coll.name} (points={info.get('points_count') if info else 'unknown'}, "
                    f"vectors={info.get('vectors_count') if info else 'unknown'})"
                )
            message = "\n".join(names) if names else "No collections found."
            await ctx.send(message)
        except commands.UserFeedbackCheckFailure as exc:
            await ctx.send(str(exc))
        except Exception as exc:
            log.warning("Failed to list collections: %s", exc)
            await ctx.send("Unable to retrieve collections.")

    @qdrant_group.command(name="endpoint")
    async def qdrant_endpoint(self, ctx: commands.Context, url: str) -> None:
        if not url.startswith(("http://", "https://")):
            raise commands.UserFeedbackCheckFailure("Endpoint must start with http:// or https://")
        await self.config.endpoint.set(url)
        self._client = None
        await ctx.send(f"Qdrant endpoint set to {url}")

    @qdrant_group.command(name="apikey")
    async def qdrant_apikey(self, ctx: commands.Context, key: str) -> None:
        await self.config.api_key.set(key)
        self._client = None
        await ctx.send("API key updated.")
        try:
            await ctx.message.delete()
        except Exception:
            pass

    @qdrant_group.command(name="dimension")
    async def qdrant_dimension(self, ctx: commands.Context, dimension: int) -> None:
        if dimension <= 0:
            raise commands.UserFeedbackCheckFailure("Dimension must be greater than zero.")
        await self.config.default_dimension.set(dimension)
        await ctx.send(f"Default dimension set to {dimension}")

    @qdrant_group.command(name="metric")
    async def qdrant_metric(self, ctx: commands.Context, metric: str) -> None:
        metric_title = metric.title()
        if metric_title not in {"Cosine", "Euclid", "Dot"}:
            raise commands.UserFeedbackCheckFailure("Metric must be one of: Cosine, Euclid, Dot.")
        await self.config.distance_metric.set(metric_title)
        await ctx.send(f"Distance metric set to {metric_title}")

    @qdrant_group.command(name="purge")
    async def qdrant_purge(self, ctx: commands.Context, collection: str) -> None:
        collection_name = self._collection_name(ctx.guild, collection)
        await ctx.send(
            f"Confirm deletion of collection `{collection_name}` by typing `yes`."
        )

        def check(message: discord.Message) -> bool:
            return (
                message.author == ctx.author
                and message.channel == ctx.channel
                and message.content.lower() == "yes"
            )

        try:
            await ctx.bot.wait_for("message", check=check, timeout=30)
        except Exception:
            await ctx.send("Purge cancelled.")
            return

        try:
            client = await self._get_client()
            client.delete_collection(collection_name=collection_name)
            self._collection_cache.pop(collection_name, None)
            await ctx.send(f"Collection `{collection_name}` deleted.")
        except Exception as exc:
            log.warning("Failed to delete collection %s: %s", collection_name, exc)
            await ctx.send("Failed to delete collection.")
