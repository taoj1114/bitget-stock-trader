"""新闻源注册器 — 主源/备用管理"""

from typing import Optional

from src.core.interfaces import NewsSource
from src.core.types import NewsItem


class NewsRegistry:
    """新闻源管理器

    支持主源 + 备用源，主源失败自动降级。
    """

    def __init__(self, primary_name: str = "searxng", fallback_name: Optional[str] = None):
        self._sources: dict[str, NewsSource] = {}
        self.primary_name = primary_name
        self.fallback_name = fallback_name

    def register(self, source: NewsSource) -> None:
        self._sources[source.name] = source

    async def fetch_news(self, query: str, max_results: int = 15) -> list[NewsItem]:
        """获取新闻，主源失败自动降级到备用"""
        # 尝试主源
        primary = self._sources.get(self.primary_name)
        if primary:
            try:
                items = await primary.fetch_news(query, max_results)
                if items:
                    return items
            except Exception as e:
                print(f"[新闻] 主源 {self.primary_name} 失败: {e}")

        # 降级到备用
        if self.fallback_name:
            fallback = self._sources.get(self.fallback_name)
            if fallback:
                try:
                    items = await fallback.fetch_news(query, max_results)
                    if items:
                        return items
                except Exception:
                    pass

        return []

    @property
    def status(self) -> dict:
        return {
            "primary": self.primary_name,
            "fallback": self.fallback_name,
            "available": list(self._sources.keys()),
        }
