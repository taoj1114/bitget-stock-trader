#!/usr/bin/env python3
"""全品种扫描 —— 快速运行一次全扫描并输出分析报告

用法:
    PYTHONPATH=. python3 scripts/scan_all.py               # 仅规则策略
    PYTHONPATH=. python3 scripts/scan_all.py --ai          # 含 AI 验证
    PYTHONPATH=. python3 scripts/scan_all.py --symbol AAPL # 单品种

输出: 每个品种的技术指标、策略信号、AI 判断（如启用）
"""

import asyncio
import logging
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config.loader import get_config
from src.datasources.bitget.market import BitgetMarketSource
from src.datasources.news.yahoo import YahooNewsSource
from src.datasources.news.searxng import SearXNGNewsSource
from src.datasources.news.finnhub import FinnhubNewsSource
from src.datasources.news.registry import NewsRegistry
from src.analyzers.technical import TechnicalAnalyzer
from src.analyzers.market_regime import MarketRegimeDetector
from src.strategies.trend_break import TrendBreakStrategy
from src.strategies.rsi_bounce import RsiBounceStrategy
from src.strategies.momentum_chase import MomentumChaseStrategy
from src.strategies.ai_composite import AICompositeStrategy
from src.core.types import AnalysisContext
from src.core.sanitize import clean_float


async def scan_all(symbols: list[str], use_ai: bool = False):
    """全品种扫描"""
    config = get_config()
    market = BitgetMarketSource()
    tech = TechnicalAnalyzer()
    regime_d = MarketRegimeDetector()

    # 策略
    tb = TrendBreakStrategy()
    rb = RsiBounceStrategy()
    mc = MomentumChaseStrategy()

    ai = None
    news_reg = None
    if use_ai:
        ai = AICompositeStrategy(api_key=config.deepseek.get("api_key", ""))
        from src.storage.kline_store import KlineStore
        from src.features.pipeline import FeaturePipeline
        pipeline = FeaturePipeline(KlineStore())
        ai.set_pipeline(pipeline)

        news_reg = NewsRegistry("yahoo", "finnhub", "searxng")
        news_reg.register(YahooNewsSource())
        news_reg.register(FinnhubNewsSource(
            api_key=config.news_sources.get("finnhub", {}).get("token", "")))
        news_reg.register(SearXNGNewsSource(
            base_url=config.searxng_base_url, timeout=config.searxng_timeout))

    print(f"{'='*80}")
    print(f"全品种扫描: {len(symbols)} 个 | AI: {'✅' if use_ai else '❌'}")
    print(f"{'='*80}")

    signals_found = 0
    for i, sym in enumerate(symbols):
        try:
            q = await market.get_quote(sym)
            k = await market.get_klines(sym, "1H", 100)
        except Exception:
            continue

        if not q or len(k) < 30:
            continue

        ind = tech.calculate(k)
        mr = regime_d.detect(k)

        ctx = AnalysisContext(
            symbol=sym, quote=q, klines=k, indicators=ind,
            fundamentals=None, news=[], market_regime=mr,
        )

        # 规则策略
        sigs = []
        for strat in [tb, rb, mc]:
            try:
                s = await strat.evaluate(ctx)
                if s:
                    sigs.append(s)
            except Exception:
                pass

        # AI 验证
        ai_result = ""
        if use_ai and sigs and ai:
            try:
                news = await news_reg.fetch_news(sym, 5, merge_all=False)
                ctx_ai = AnalysisContext(
                    symbol=sym, quote=q, klines=k, indicators=ind,
                    fundamentals=None, news=news, market_regime=mr,
                )
                ai_sig = await ai.evaluate(ctx_ai)
                if ai_sig:
                    ai_result = f" → AI:{ai_sig.action}({ai_sig.confidence:.2f})"
                else:
                    ai_result = " → AI:否决"
            except Exception:
                ai_result = " → AI:错误"

        if sigs:
            signals_found += 1
            sig_str = " | ".join(f"{s.strategy_id}={s.action}({s.confidence:.2f})" for s in sigs)
            print(f"[{i+1:2d}] {sym:6s} ${q.mark_price:>8.2f} {q.change_pct*100:+.1f}% "
                  f"RSI={ind.rsi14:.0f} ADX={mr.adx:.0f} {mr.regime:12s} "
                  f"| {sig_str}{ai_result}")
        else:
            print(f"[{i+1:2d}] {sym:6s} ${q.mark_price:>8.2f} {q.change_pct*100:+.1f}% "
                  f"RSI={ind.rsi14:.0f} ADX={mr.adx:.0f} {mr.regime:12s} —")

        await asyncio.sleep(0.1)  # rate limit

    print(f"\n{signals_found}/{len(symbols)} 品种有信号")
    await market.close()


async def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    args = sys.argv[1:]
    use_ai = "--ai" in args
    symbol = None

    for i, a in enumerate(args):
        if a == "--symbol" and i + 1 < len(args):
            symbol = args[i + 1]

    config = get_config()
    symbols = [symbol] if symbol else config.symbols

    await scan_all(symbols, use_ai=use_ai)


if __name__ == "__main__":
    asyncio.run(main())
