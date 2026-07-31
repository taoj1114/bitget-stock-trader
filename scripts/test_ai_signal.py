#!/usr/bin/env python3
"""AI 交易信号模拟 — 验证 AI 能否在真实数据下输出 BUY/SELL"""

import asyncio, logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

from src.datasources.bitget.market import BitgetMarketSource
from src.analyzers.technical import TechnicalAnalyzer
from src.analyzers.market_regime import MarketRegimeDetector
from src.strategies.ai_composite import AICompositeStrategy
from src.core.types import AnalysisContext
from src.storage.kline_store import KlineStore
from src.features.pipeline import FeaturePipeline
from src.storage.kline_aggregator import KlineAggregator
from src.core.types import Kline

BENCH = ["SPY","QQQ","SOXX"]

async def test(symbol: str):
    m = BitgetMarketSource()
    t = TechnicalAnalyzer()
    r = MarketRegimeDetector()
    store = KlineStore()
    pipeline = FeaturePipeline(store)
    agg = KlineAggregator()

    # 数据
    k_1h = await m.get_klines(symbol, "1H", 500)
    q = await m.get_quote(symbol)
    if not q or not k_1h:
        print(f"❌ {symbol} 无数据"); return

    ind_1h = t.calculate(k_1h)
    regime = r.detect(k_1h)

    # 多周期
    k_4h = [Kline(**x) for x in agg.aggregate(k_1h, "4H")]
    k_1d = [Kline(**x) for x in agg.aggregate(k_1h, "1D")]
    ind_4h = t.calculate(k_4h) if len(k_4h) >= 5 else None
    ind_1d = t.calculate(k_1d) if len(k_1d) >= 3 else None

    # 大盘
    bench = {}
    for b in BENCH:
        try:
            bq = await m.get_quote(b)
            if bq and bq.mark_price > 0: bench[b] = bq
        except: pass

    # 因子
    try:
        store.upsert_batch(symbol, [{
            'timestamp': x.timestamp, 'open': x.open, 'high': x.high,
            'low': x.low, 'close': x.close, 'volume': x.volume,
            'turnover': x.turnover
        } for x in k_1h])
        factors = pipeline.compute_one(symbol)
    except Exception:
        factors = None

    ctx = AnalysisContext(symbol=symbol, quote=q, klines=k_1h,
                          indicators=ind_1h, fundamentals=None, news=[],
                          market_regime=regime)

    # AI 评估
    ai = AICompositeStrategy()
    if factors:
        pipeline._symbol = symbol  # hack: set symbol for compute_one
        ai.set_pipeline(pipeline)

    sig = await ai.evaluate(ctx, ind_4h=ind_4h, ind_1d=ind_1d, bench_quotes=bench)

    print(f"\n{'='*50}")
    print(f"品种: {symbol} ${q.mark_price:.2f} ({q.change_pct*100:+.1f}%)")
    print(f"1H: RSI={ind_1h.rsi14:.0f} MA交叉={'金' if ind_1h.ma10>ind_1h.ma30 else '死'}")
    print(f"4H: RSI={ind_4h.rsi14:.0f}" if ind_4h else "4H: N/A", end=" | ")
    print(f"1D: RSI={ind_1d.rsi14:.0f}" if ind_1d else "1D: N/A")
    print(f"趋势: {regime.regime} ADX={regime.adx:.0f}")
    print(f"大盘: {' '.join(f'{k}{v.change_pct*100:+.1f}%' for k,v in bench.items())}")
    print(f"因子: {'OK' if factors else '无'}")

    if sig:
        print(f"\n✅ AI开仓: {sig.action}({sig.confidence:.2f})")
        print(f"   SL=${sig.stop_loss:.2f} TP=${sig.take_profits[0]:.2f}")
        print(f"   理由: {sig.reason[:120]}...")
    else:
        print(f"\n❌ AI否决 — 返回 HOLD")

    await m.close()

asyncio.run(test("ARM"))
