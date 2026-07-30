#!/usr/bin/env python3
"""数据同步脚本 — 定时拉取 Bitget K线 + Eastmoney 基本面 + SearXNG 新闻

cron: */30 * * * *
用法: PYTHONPATH=. python3 scripts/data_sync.py
"""

import asyncio
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config.loader import get_config
from src.datasources.bitget.market import BitgetMarketSource
from src.datasources.bitget.symbols import BitgetSymbolSource
from src.datasources.eastmoney.search import EastmoneySearch
from src.datasources.eastmoney.fundamentals import EastmoneyFundamentalSource
from src.datasources.news.searxng import SearXNGNewsSource
from src.datasources.news.yahoo import YahooNewsSource
from src.datasources.news.finnhub import FinnhubNewsSource
from src.datasources.news.registry import NewsRegistry
from src.analyzers.sentiment import SentimentAnalyzer
from src.storage.kline_store import KlineStore
from src.storage.fund_store import FundStore
from src.storage.news_sentiment_store import NewsSentimentStore

logger = logging.getLogger(__name__)


async def sync_klines(market: BitgetMarketSource, store: KlineStore, symbol: str) -> int:
    """同步单个品种的 1H K线（增量）。"""
    last_ts = store.get_latest_ts(symbol)
    try:
        klines = await market.get_klines(symbol, interval="1H", limit=100)
    except Exception:
        return 0

    if last_ts:
        klines = [k for k in klines if k.timestamp > last_ts]

    if klines:
        rows = [
            {
                "timestamp": k.timestamp, "open": k.open, "high": k.high,
                "low": k.low, "close": k.close, "volume": k.volume,
                "turnover": k.turnover,
            }
            for k in klines
        ]
        return store.upsert_batch(symbol, rows)
    return 0


async def sync_fundamental(fund_src: EastmoneyFundamentalSource, store: FundStore, symbol: str) -> bool:
    """同步单个品种的基本面。"""
    try:
        data = await fund_src.get_fundamentals(symbol)
        if data:
            store.upsert(symbol, {
                "report_date": data.report_date,
                "revenue": data.revenue,
                "revenue_yoy": data.revenue_yoy,
                "net_profit": data.net_profit,
                "net_profit_yoy": data.net_profit_yoy,
                "eps": data.eps, "roe": data.roe,
                "gross_margin": data.gross_margin,
                "net_margin": data.net_margin,
                "debt_ratio": data.debt_ratio,
            })
            return True
    except Exception:
        pass
    return False


async def sync_news(snews: SearXNGNewsSource, store: NewsSentimentStore, symbol: str) -> bool:
    """同步单个品种的新闻情绪。"""
    try:
        items = await snews.fetch_news(symbol, max_results=15)
        sentiment = SentimentAnalyzer.score(items)
        store.snapshot(symbol, sentiment)
        return True
    except Exception:
        pass
    return False


async def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    config = get_config()
    symbols = config.symbols

    market = BitgetMarketSource()
    fund_src = EastmoneyFundamentalSource()

    # 多新闻源注册
    news_registry = NewsRegistry(
        primary_name=config.news_sources.get("primary", "yahoo"),
        fallback_name=config.news_sources.get("fallback", "searxng"),
        fallback2_name=config.news_sources.get("fallback2"),
    )
    news_registry.register(YahooNewsSource(timeout=config.news_sources.get("yahoo", {}).get("timeout", 10)))
    news_registry.register(SearXNGNewsSource(
        base_url=config.searxng_base_url,
        timeout=config.searxng_timeout,
    ))
    finnhub_token = config.news_sources.get("finnhub", {}).get("token", "")
    if finnhub_token:
        news_registry.register(FinnhubNewsSource(api_key=finnhub_token))

    kline_store = KlineStore()
    fund_store = FundStore()
    news_store = NewsSentimentStore()

    stats = {"klines": 0, "fundamentals": 0, "sentiments": 0}

    for symbol in symbols:
        # K线同步（每次都做）
        added = await sync_klines(market, kline_store, symbol)
        stats["klines"] += added

        # 基本面同步（已有时跳过）
        if not fund_store.get_latest(symbol):
            ok = await sync_fundamental(fund_src, fund_store, symbol)
            if ok:
                stats["fundamentals"] += 1

        # 新闻情绪同步（用多源 registry）
        items = await news_registry.fetch_news(symbol, max_results=15)
        if items:
            sentiment = SentimentAnalyzer.score(items)
            news_store.snapshot(symbol, sentiment)
            stats["sentiments"] += 1

        await asyncio.sleep(0.1)  # Rate limit

    await market.close()
    kline_store.close()
    fund_store.close()
    news_store.close()

    # 只在有新数据时输出（cron 模式下空输出 = silent）
    if any(v > 0 for v in stats.values()):
        msg = f"Synced: {stats['klines']} klines, {stats['fundamentals']} funds, {stats['sentiments']} sentiments"
        print(msg)
        return msg
    return ""


if __name__ == "__main__":
    result = asyncio.run(main())
    if result:
        print(result)
