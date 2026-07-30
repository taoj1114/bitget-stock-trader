"""SearXNG 新闻源

API 端点:
    GET http://localhost:8080/search?q={query}&format=json&categories=news&language=en
        → results: [{title, content, url, engine, publishedDate}]

用法:
    source = SearXNGNewsSource(base_url="http://localhost:8080", timeout=10)
    items = await source.fetch_news("AAPL")

注意事项:
    - ⚠️ publishedDate 可能为 None（SearXNG 不保证时间戳）
    - 搜索失败时返回空列表，不要抛异常
"""

from typing import Optional

import httpx

from src.core.types import NewsItem
from src.datasources.base import BaseNewsSource


class SearXNGNewsSource(BaseNewsSource):
    """SearXNG 新闻源"""

    def __init__(self, base_url: str = "http://localhost:8080", timeout: int = 10):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def name(self) -> str:
        return "searxng"

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def fetch_news(
        self, query: str, max_results: int = 15
    ) -> list[NewsItem]:
        """从 SearXNG 获取新闻。

        Args:
            query: 搜索关键词（如 "AAPL stock"）
            max_results: 最大返回条数

        Returns:
            list[NewsItem]: 新闻列表，失败返回空列表
        """
        url = f"{self._base_url}/search"
        params = {
            "q": f"{query} stock",
            "format": "json",
            "categories": "news",
            "language": "en",
            "pageno": 1,
        }

        try:
            client = self._get_client()
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
        except Exception:
            # 任何错误都返回空列表
            return []

        results = data.get("results", [])
        items: list[NewsItem] = []

        for r in results[:max_results]:
            published_at = r.get("publishedDate")  # 可能为 None
            # 尝试标准化 ISO 格式
            if published_at and isinstance(published_at, str) and published_at.endswith("Z"):
                published_at = published_at.replace("Z", "+00:00")

            items.append(
                NewsItem(
                    title=r.get("title", ""),
                    snippet=r.get("content", "")[:500],  # 截断长文本
                    url=r.get("url", ""),
                    source=r.get("engine", "unknown"),
                    published_at=published_at,
                )
            )

        return items

    async def close(self) -> None:
        """关闭 HTTP 客户端"""
        if self._client:
            await self._client.aclose()
            self._client = None
