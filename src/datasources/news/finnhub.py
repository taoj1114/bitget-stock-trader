"""Finnhub 新闻源 — 免费注册 Key，自带情绪分数

API:
    GET https://finnhub.io/api/v1/company-news?symbol=AAPL&from=YYYY-MM-DD&to=YYYY-MM-DD&token={KEY}
    → [{category, datetime, headline, id, image, related, source, summary, url}]

注册: https://finnhub.io/register → 免费 60 req/min
"""

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx

from src.core.types import NewsItem
from src.datasources.base import BaseNewsSource

logger = logging.getLogger(__name__)

FINNHUB_URL = "https://finnhub.io/api/v1/company-news"


class FinnhubNewsSource(BaseNewsSource):
    """Finnhub 新闻源 — 带内部情绪分析 API。"""

    def __init__(self, api_key: str = "", timeout: int = 10):
        self._api_key = api_key or os.environ.get("FINNHUB_API_KEY", "")
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "finnhub"

    async def fetch_news(self, query: str, max_results: int = 15) -> list[NewsItem]:
        """从 Finnhub 获取新闻。

        Args:
            query: 股票代码 (如 AAPL)
            max_results: 最大返回条数
        """
        if not self._api_key:
            return []

        # Finnhub 要求日期范围
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(FINNHUB_URL, params={
                    "symbol": query.upper(),
                    "from": week_ago,
                    "to": today,
                    "token": self._api_key,
                })
                if resp.status_code != 200:
                    logger.warning("Finnhub HTTP %d for %s", resp.status_code, query)
                    return []
                data = resp.json()
        except Exception as e:
            logger.warning("Finnhub request failed for %s: %s", query, e)
            return []

        if not isinstance(data, list):
            return []

        items: list[NewsItem] = []
        for article in data[:max_results]:
            # Finnhub 自带 sentiment 字段（如果订阅了 Premium）或 None
            sentiment = article.get("sentiment")

            items.append(NewsItem(
                title=article.get("headline", ""),
                snippet=article.get("summary", "")[:500],
                url=article.get("url", ""),
                source=article.get("source", "Finnhub"),
                published_at=self._parse_datetime(article.get("datetime")),
                sentiment_score=float(sentiment) if sentiment else None,
            ))

        return items

    @staticmethod
    def _parse_datetime(ts) -> Optional[str]:
        """Finnhub 返回 Unix 秒时间戳。"""
        if not ts:
            return None
        try:
            return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
        except (TypeError, ValueError):
            return None
