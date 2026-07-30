"""Yahoo Finance 新闻源 — 基于 yfinance 库 (免费，无需 API Key)

底层: yfinance.Ticker(symbol).news → 最近新闻列表

优点:
    - 免费，无限使用
    - 带发布时间戳
    - 比 SearXNG 更可靠的新闻源
"""

import logging
from typing import Optional

from src.core.types import NewsItem
from src.datasources.base import BaseNewsSource

logger = logging.getLogger(__name__)


class YahooNewsSource(BaseNewsSource):
    """Yahoo Finance 新闻源 (yfinance)。"""

    def __init__(self, timeout: int = 10):
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "yahoo"

    async def fetch_news(self, query: str, max_results: int = 15) -> list[NewsItem]:
        """从 Yahoo Finance 获取新闻。

        Args:
            query: 股票代码 (如 AAPL)
            max_results: 最大返回条数
        """
        try:
            import yfinance as yf
            ticker = yf.Ticker(query.upper())
            raw_news = ticker.news
        except Exception as e:
            logger.warning("yfinance news failed for %s: %s", query, e)
            return []

        if not raw_news:
            return []

        items: list[NewsItem] = []
        for article in raw_news[:max_results]:
            content = article.get("content", {}) or {}
            title = content.get("title", "") or article.get("title", "")
            snippet = content.get("description", "") or content.get("summary", "") or ""
            # 去 HTML 标签
            import re
            snippet = re.sub(r"<[^>]+>", "", snippet)[:500]

            url = content.get("canonicalUrl", {}).get("url", "") or content.get("clickThroughUrl", {}).get("url", "")
            if not url:
                url = content.get("url", "")

            pub_ts = content.get("pubDate") or content.get("providerPublishTime")
            published_at = None
            if pub_ts:
                from datetime import datetime, timezone
                try:
                    dt = datetime.fromtimestamp(int(pub_ts), tz=timezone.utc)
                    published_at = dt.isoformat()
                except (TypeError, ValueError, OSError):
                    published_at = str(pub_ts)

            provider = content.get("provider", {}).get("displayName", "") or article.get("publisher", "")

            if title:
                items.append(NewsItem(
                    title=title.strip(),
                    snippet=snippet.strip(),
                    url=url if url else "",
                    source=provider or "YahooFinance",
                    published_at=published_at,
                ))

        return items
