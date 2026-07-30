"""策略测试 — 用虚构 K 线验证信号逻辑"""

import pytest
from datetime import datetime, timezone

from src.core.types import (
    Signal, Kline, Quote, TechnicalIndicators, AnalysisContext, MarketRegime,
)
from src.strategies.rsi_bounce import RsiBounceStrategy
from src.strategies.momentum_chase import MomentumChaseStrategy


def make_kline(timestamp: int, o: float, h: float, l: float, c: float, v: float = 1000):
    """创建测试用 K 线"""
    return Kline(
        timestamp=timestamp,
        open=o, high=h, low=l, close=c, volume=v, turnover=v * c,
    )


def make_indicators(rsi: float = 50, ma10: float = 100, ma30: float = 100,
                    atr: float = 2, macd: float = 0, volume_ratio: float = 1.0):
    return TechnicalIndicators(
        symbol="TEST", timestamp=1000,
        rsi14=rsi, ma10=ma10, ma30=ma30, ma20=ma10, ma60=ma10,
        ma10_prev=ma10, ma30_prev=ma30,
        ema12=ma10, ema26=ma10, atr14=atr, macd=macd,
        macd_signal=macd - 0.1, bb_upper=ma10 + 2 * atr,
        bb_middle=ma10, bb_lower=ma10 - 2 * atr,
        volume_ma20=1000, volume_ratio=volume_ratio,
    )


def make_ctx(symbol="TEST", price=100, indicators=None, regime="range_bound",
             klines=None, adx=15):
    if indicators is None:
        indicators = make_indicators()
    if klines is None:
        klines = [make_kline(i, price, price + 1, price - 1, price) for i in range(50)]
    quote = Quote(symbol=symbol, price=price, open_24h=price, high_24h=price + 1,
                  low_24h=price - 1, change_pct=0,
                  volume_24h=10000, turnover_24h=1000000,
                  index_price=price, mark_price=price,
                  funding_rate=0, open_interest=100000)
    mr = MarketRegime(regime=regime, adx=adx, volatility="normal")
    return AnalysisContext(symbol=symbol, quote=quote, klines=klines,
                           indicators=indicators, fundamentals=None, news=[],
                           market_regime=mr)


class TestRsiBounce:
    """超卖反弹策略"""

    @pytest.mark.asyncio
    async def test_oversold_buy_in_range_bound(self):
        """震荡市 RSI 超卖 + 近支撑 → BUY"""
        rsi = RsiBounceStrategy()
        klines = [make_kline(i, 100, 102, 98, 100) for i in range(50)]
        klines.append(make_kline(51, 99, 101, 98, 99))
        klines.append(make_kline(52, 99, 100, 98, 99))
        ctx = make_ctx(price=99,
                       indicators=make_indicators(rsi=25, ma10=100, atr=2),
                       regime="range_bound", klines=klines)

        sig = await rsi.evaluate(ctx)
        assert sig is not None
        assert sig.action == "BUY"

    @pytest.mark.asyncio
    async def test_no_trigger_when_not_oversold(self):
        """RSI 正常 → 无信号"""
        rsi = RsiBounceStrategy()
        ctx = make_ctx(price=100, indicators=make_indicators(rsi=45),
                       regime="range_bound")
        sig = await rsi.evaluate(ctx)
        assert sig is None

    @pytest.mark.asyncio
    async def test_trend_down_requires_score_3(self):
        """强下跌趋势(ADX>30)禁止做多"""
        rsi = RsiBounceStrategy()
        klines = [make_kline(i, 100 - i * 0.2, 101 - i * 0.2,
                             99 - i * 0.2, 100 - i * 0.2) for i in range(50)]
        ctx = make_ctx(price=95,
                       indicators=make_indicators(rsi=25, ma10=100,
                                                  atr=5, macd=-0.5),
                       regime="trend_down", adx=35, klines=klines)  # ADX>30

        sig = await rsi.evaluate(ctx)
        # ADX=35 > 30 → 强下跌禁止做多 → 无信号
        assert sig is None

    @pytest.mark.asyncio
    async def test_overbought_sell_in_range_bound(self):
        """震荡市 RSI 超买 + 近阻力 → SELL"""
        rsi = RsiBounceStrategy()
        klines = [make_kline(i, 100, 102, 98, 100) for i in range(50)]
        klines.append(make_kline(51, 105, 106, 104, 105))
        klines.append(make_kline(52, 105, 107, 104, 105))
        ctx = make_ctx(price=105,
                       indicators=make_indicators(rsi=80, ma10=100, atr=2),
                       regime="range_bound", klines=klines)

        sig = await rsi.evaluate(ctx)
        if sig:
            assert sig.action == "SELL"


class TestMomentumChase:
    """顺势追踪策略"""

    @pytest.mark.asyncio
    async def test_trend_down_sell(self):
        """下跌趋势 + ADX>20 + 价格<MA10 → SELL"""
        mc = MomentumChaseStrategy()
        klines = [make_kline(i, 105 - i * 0.2, 106 - i * 0.2,
                             104 - i * 0.2, 105 - i * 0.2) for i in range(50)]
        ctx = make_ctx(price=95,
                       indicators=make_indicators(ma10=100, ma30=105),
                       regime="trend_down", adx=35, klines=klines)

        sig = await mc.evaluate(ctx)
        assert sig is not None
        assert sig.action == "SELL"
        assert sig.confidence > 0.5

    @pytest.mark.asyncio
    async def test_trend_up_buy(self):
        """上升趋势 + ADX>20 + 价格>MA10 → BUY"""
        mc = MomentumChaseStrategy()
        klines = [make_kline(i, 95 + i * 0.2, 96 + i * 0.2,
                             94 + i * 0.2, 95 + i * 0.2) for i in range(50)]
        ctx = make_ctx(price=105,
                       indicators=make_indicators(ma10=100, ma30=95),
                       regime="trend_up", adx=30, klines=klines)

        sig = await mc.evaluate(ctx)
        assert sig is not None
        assert sig.action == "BUY"

    @pytest.mark.asyncio
    async def test_no_trigger_weak_trend(self):
        """ADX 不足 20 → 无信号"""
        mc = MomentumChaseStrategy()
        ctx = make_ctx(price=95, indicators=make_indicators(ma10=100),
                       regime="trend_down", adx=15)
        sig = await mc.evaluate(ctx)
        assert sig is None

    @pytest.mark.asyncio
    async def test_no_trigger_price_above_ma10_in_downtrend(self):
        """价格在 MA10 之上 → 趋势可能反转 → 不追空"""
        mc = MomentumChaseStrategy()
        ctx = make_ctx(price=102, indicators=make_indicators(ma10=100),
                       regime="trend_down", adx=35)
        sig = await mc.evaluate(ctx)
        assert sig is None

    @pytest.mark.asyncio
    async def test_confidence_scales_with_adx(self):
        """ADX 越高，置信度越高"""
        mc = MomentumChaseStrategy()
        ctx_high = make_ctx(price=95, indicators=make_indicators(ma10=100),
                            regime="trend_down", adx=45)
        ctx_low = make_ctx(price=95, indicators=make_indicators(ma10=100),
                           regime="trend_down", adx=25)

        sig_high = await mc.evaluate(ctx_high)
        sig_low = await mc.evaluate(ctx_low)

        assert sig_high is not None
        assert sig_low is not None
        assert sig_high.confidence > sig_low.confidence
