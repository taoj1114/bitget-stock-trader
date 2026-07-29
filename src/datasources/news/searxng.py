"""新闻源接口定义"""

from abc import abstractmethod
from typing import Optional

from src.core.types import NewsItem
from src.datasources.base import BaseNewsSource


class SearXNGNewsSource(BaseNewsSource):
    """SearXNG 新闻源 — 通过本地 SearXNG 实例获取聚合新闻"""

    def __init__(self, base_url: str = "http://localhost:8080",
                 max_results: int = 15, timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.max_results = max_results
        self.timeout = timeout

    async def fetch_news(self, query: str, max_results: Optional[int] = None) -> list[NewsItem]:
        """从 SearXNG 获取新闻

        ⚠️ SearXNG 不返回 publishedDate，AI 分析时需注意
        """
        import httpx

        max_results = max_results or self.max_results
        url = f"{self.base_url}/search"
        params = {
            "q": f"{query} stock",
            "format": "json",
            "categories": "news",
            "pageno": 1,
            "language": "en",
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url, params=params)
                data = resp.json()
        except Exception as e:
            print(f"[SearXNG] 请求失败: {e}")
            return []

        items = []
        for r in data.get("results", [])[:max_results]:
            items.append(NewsItem(
                title=r.get("title", ""),
                snippet=r.get("content", ""),
                url=r.get("url", ""),
                source=r.get("engine", "searxng"),
                published_at=r.get("publishedDate"),  # 可能为 None
            ))
        return items
