"""SearXNG 新闻源

============================================================
TODO[Phase1]: 实现 SearXNG 新闻获取
============================================================

API 端点:
    GET http://localhost:8080/search?q={query}+stock&format=json&categories=news&language=en
        → results: [{title, content, url, engine, publishedDate}]

接口:
    class SearXNGNewsSource(BaseNewsSource):
        async def fetch_news(self, query: str, max_results: int = 15) -> list[NewsItem]

参考:
    - 伪代码: PSEUDOCODE.md 第5节
    - SearXNG 运行在 localhost:8080 (Docker)
    - 已验证: curl "http://localhost:8080/search?q=AAPL+stock&format=json&categories=news"  # 41 results

注意事项:
    - ⚠️ publishedDate 可能为 None（SearXNG 不保证时间戳）
    - AI 分析时需在 prompt 中标注"新闻无确切时间"
    - 搜索失败时返回空列表，不要抛异常
"""

from typing import Optional
from src.core.types import NewsItem
from src.datasources.base import BaseNewsSource


class SearXNGNewsSource(BaseNewsSource):
    """SearXNG 新闻源"""

    async def fetch_news(self, query: str, max_results: Optional[int] = None) -> list[NewsItem]:
        raise NotImplementedError("TODO[Phase1]: 实现 SearXNGNewsSource.fetch_news()")
