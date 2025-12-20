import asyncio
import logging
from typing import Any, Dict, Optional

import aiohttp


class TMDbClient:
    def __init__(self, api_key: str, rate_limiter: asyncio.Semaphore, logger: logging.Logger):
        self.api_key = api_key
        self.rate_limiter = rate_limiter
        self.logger = logger
        self.base_url = "https://api.themoviedb.org/3"

    async def _request(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        params = params or {}
        params["api_key"] = self.api_key

        backoff = 1.0
        attempts = 0
        last_error: Optional[Exception] = None

        while attempts < 3:
            try:
                async with self.rate_limiter:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(f"{self.base_url}{endpoint}", params=params) as resp:
                            if resp.status == 401:
                                raise ValueError("Invalid TMDb API key")
                            if resp.status == 404:
                                raise ValueError("Resource not found")
                            if resp.status == 429:
                                raise RuntimeError("TMDb rate limit exceeded")
                            if resp.status >= 500:
                                raise RuntimeError(f"TMDb server error ({resp.status})")
                            resp.raise_for_status()
                            return await resp.json()
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, RuntimeError) as exc:
                last_error = exc
                attempts += 1
                if attempts >= 3 or isinstance(exc, ValueError):
                    break
                self.logger.warning("TMDb request failed (%s), retrying in %.1fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff *= 2

        if last_error:
            raise last_error

        raise RuntimeError("TMDb request failed with unknown error")

    async def search_movies(self, query: str) -> Dict[str, Any]:
        return await self._request("/search/movie", params={"query": query})

    async def search_tv(self, query: str) -> Dict[str, Any]:
        return await self._request("/search/tv", params={"query": query})

    async def movie_details(self, movie_id: int) -> Dict[str, Any]:
        return await self._request(f"/movie/{movie_id}")

    async def tv_details(self, tv_id: int) -> Dict[str, Any]:
        return await self._request(f"/tv/{tv_id}")

    async def movie_credits(self, movie_id: int) -> Dict[str, Any]:
        return await self._request(f"/movie/{movie_id}/credits")

    async def tv_credits(self, tv_id: int) -> Dict[str, Any]:
        return await self._request(f"/tv/{tv_id}/credits")

    async def discover_movies(self, page: int = 1) -> Dict[str, Any]:
        return await self._request("/discover/movie", params={"page": page})

    async def discover_tv(self, page: int = 1) -> Dict[str, Any]:
        return await self._request("/discover/tv", params={"page": page})

    async def tv_airing_today(self, page: int = 1) -> Dict[str, Any]:
        return await self._request("/tv/airing_today", params={"page": page})

    async def tv_on_the_air(self, page: int = 1) -> Dict[str, Any]:
        return await self._request("/tv/on_the_air", params={"page": page})

    async def tv_popular(self, page: int = 1) -> Dict[str, Any]:
        return await self._request("/tv/popular", params={"page": page})

    async def tv_top_rated(self, page: int = 1) -> Dict[str, Any]:
        return await self._request("/tv/top_rated", params={"page": page})
