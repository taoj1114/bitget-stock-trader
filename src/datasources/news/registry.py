"""新闻源注册器 — 多源链式降级 (primary → fallback → fallback2)

支持:
    - 主源成功 → 直接返回
    - 主源失败/空 → 尝试备用1
    - 备用1失败 → 尝试备用2
    - 全部失败 → 返回空列表
    - 多源聚合模式: 合并所有源的结果 (去重)
"""

from typing import Optional

from src.core.interfaces import NewsSource
from src.core.types import NewsItem


class NewsRegistry:
    """新闻源管理器 — 多源降级 + 聚合。"""

    def __init__(self, primary_name: str = "yahoo",
                 fallback_name: Optional[str] = "searxng",
                 fallback2_name: Optional[str] = None):
        self._sources: dict[str, NewsSource] = {}
        self.primary_name = primary_name
        self.fallback_name = fallback_name
        self.fallback2_name = fallback2_name
        self._fallback_chain = [n for n in [primary_name, fallback_name, fallback2_name] if n]

    def register(self, source: NewsSource) -> None:
        self._sources[source.name] = source

    async def fetch_news(self, query: str, max_results: int = 15,
                         merge_all: bool = False) -> list[NewsItem]:
        """获取新闻，默认链式降级，可选多源聚合。

        Args:
            query: 搜索关键词
            max_results: 最大条数
            merge_all: True=合并所有源, False=链式降级
        """
        if merge_all:
            return await self._fetch_merged(query, max_results)
        return await self._fetch_fallback(query, max_results)

    async def _fetch_fallback(self, query: str, max_results: int) -> list[NewsItem]:
        """链式降级：逐个尝试直到成功。"""
        for name in self._fallback_chain:
            source = self._sources.get(name)
            if not source:
                continue
            try:
                items = await source.fetch_news(query, max_results)
                if items:
                    return items
            except Exception:
                continue

        return []

    async def _fetch_merged(self, query: str, max_results: int) -> list[NewsItem]:
        """多源聚合：并发拉取 → 按URL去重 → 合并。"""
        import asyncio

        tasks = []
        for name in self._fallback_chain:
            source = self._sources.get(name)
            if source:
                tasks.append(self._safe_fetch(source, query, max_results))

        if not tasks:
            return []

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # URL 去重
        seen = set()
        merged = []
        for items in results:
            if isinstance(items, list):
                for item in items:
                    if item.url and item.url not in seen:
                        seen.add(item.url)
                        merged.append(item)

        return merged[:max_results]

    async def _safe_fetch(self, source, query, max_results):
        try:
            return await source.fetch_news(query, max_results)
        except Exception:
            return []

    @property
    def status(self) -> dict:
        return {
            "chain": self._fallback_chain,
            "available": list(self._sources.keys()),
        }
