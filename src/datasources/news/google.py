"""Google News RSS 新闻源 — 免费无限，无需 API Key

API:
    GET https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en
    → RSS/XML → {title, link, pubDate, source}

优点:
    - 完全免费，无速率限制
    - 按 symbol 精准搜索 (q=NVDA+stock)
    - 比 yfinance 新闻相关性高
"""

import logging

import httpx
import xml.etree.ElementTree as ET

from src.core.types import NewsItem
from src.datasources.base import BaseNewsSource

logger = logging.getLogger(__name__)

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"


class GoogleNewsSource(BaseNewsSource):
    """Google News RSS 新闻源 (免费无限)。"""

    def __init__(self, timeout: int = 10):
        self._timeout = timeout
        self._client = None

    @property
    def name(self) -> str:
        return "google"

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def fetch_news(self, query: str, max_results: int = 10) -> list[NewsItem]:
        """从 Google News 搜索股票新闻。"""
        try:
            params = {
                "q": f"{query} stock",
                "hl": "en-US",
                "gl": "US",
                "ceid": "US:en",
            }
            resp = await self._get_client().get(GOOGLE_NEWS_RSS, params=params)
            resp.raise_for_status()
            root = ET.fromstring(resp.text)

            items: list[NewsItem] = []
            for item in root.iter("item"):
                if len(items) >= max_results:
                    break
                title = item.findtext("title", "").strip()
                link = item.findtext("link", "").strip()
                pub = item.findtext("pubDate", "").strip()
                source_el = item.find("source")
                source = source_el.text if source_el is not None and source_el.text else "GoogleNews"
                # 摘要: Google News RSS 无 description 时跳过
                desc = item.findtext("description", "")
                import re
                desc = re.sub(r"<[^>]+>", "", desc)[:300]

                if title:
                    items.append(NewsItem(
                        title=title,
                        snippet=desc.strip(),
                        url=link,
                        source=source.strip(),
                        published_at=pub,
                    ))
            return items
        except Exception as e:
            logger.warning("GoogleNews failed for %s: %s", query, e)
            return []

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
