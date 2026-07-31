#!/usr/bin/env python3
"""AI 原生决策测试"""
import asyncio, json, logging
logging.basicConfig(level=logging.WARNING)

from src.datasources.bitget.market import BitgetMarketSource
from src.analyzers.technical import TechnicalAnalyzer
from src.analyzers.market_regime import MarketRegimeDetector
from src.storage.kline_aggregator import KlineAggregator
from src.core.types import Kline
from src.strategies.ai_native import AINativeDecisionMaker, AIInput

BENCH = ["SPY", "QQQ", "SOXX"]

async def test():
    m = BitgetMarketSource(); t = TechnicalAnalyzer()
    r = MarketRegimeDetector(); agg = KlineAggregator()
    ai = AINativeDecisionMaker()

    bench = {}
    for b in BENCH:
        try:
            bq = await m.get_quote(b)
            if bq and bq.mark_price > 0:
                bench[b] = bq.change_pct * 100
        except: pass

    print("大盘:", " ".join("%s%+.1f%%" % (k, v) for k, v in bench.items()))

    for sym in ["ARM", "SOXS", "DRAM", "MSFU"]:
        k = await m.get_klines(sym, "1H", 500)
        q = await m.get_quote(sym)
        if not q or len(k) < 30:
            continue
        ind = t.calculate(k); regime = r.detect(k)
        k4r = [Kline(**x) for x in agg.aggregate(k, "4H")]
        k1r = [Kline(**x) for x in agg.aggregate(k, "1D")]
        ind4 = t.calculate(k4r) if len(k4r) >= 5 else None
        ind1d = t.calculate(k1r) if len(k1r) >= 3 else None

        inp = AIInput(
            symbol=sym, mark_price=q.mark_price, change_pct=q.change_pct * 100,
            klines_1h=k, klines_4h=k4r, klines_1d=k1r,
            ind_1h={"rsi": ind.rsi14, "ma10": ind.ma10, "ma30": ind.ma30,
                    "macd": ind.macd, "atr": ind.atr14, "adx": regime.adx,
                    "regime": regime.regime, "bb_position": 0.5},
            ind_4h={"rsi": ind4.rsi14} if ind4 else None,
            ind_1d={"rsi": ind1d.rsi14} if ind1d else None,
            news=[], news_summary="",
            bench=bench, open_interest=q.open_interest,
            funding_rate=getattr(q, "funding_rate", 0) or 0,
            volume_24h=q.volume_24h,
        )

        rsi4 = ind4.rsi14 if ind4 else -1
        rsi1d = ind1d.rsi14 if ind1d else -1
        print("%s: $%.2f (%+.1f%%) RSI 1H=%.0f 4H=%.0f 1D=%.0f ADX=%.0f %s" % (
            sym, q.mark_price, q.change_pct * 100,
            ind.rsi14, rsi4, rsi1d, regime.adx, regime.regime), end=" → ")

        sig = await ai.decide(inp)
        if sig:
            print("Pro=%s SL=$%.2f TP=$%.2f" % (sig.action, sig.stop_loss, sig.take_profits[0]))
        else:
            print("HOLD")
        print()

    await m.close()
    await ai.close()

asyncio.run(test())
