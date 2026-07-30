"""市场状态检测器 — 趋势/震荡/波动率判断

基于 ADX + 均线排列，识别四种市场状态：
    trend_up:    上升趋势 (ADX>25, 均线多头排列)
    trend_down:  下跌趋势
    range_bound: 震荡 (ADX<20)
    weak_trend:  弱趋势 (ADX 20-25)
"""

from src.core.types import Kline, MarketRegime


class MarketRegimeDetector:
    """市场状态检测器。"""

    @staticmethod
    def detect(klines: list[Kline]) -> MarketRegime:
        """从 K线列表判断市场状态。

        Args:
            klines: K线列表（时间升序），至少 30 根
        """
        if len(klines) < 30:
            return MarketRegime(regime="range_bound", volatility="normal")

        closes = [k.close for k in klines]
        highs = [k.high for k in klines]
        lows = [k.low for k in klines]

        adx = MarketRegimeDetector._adx(highs, lows, closes, 14)
        ma10 = sum(closes[-10:]) / 10
        ma30 = sum(closes[-30:]) / 30
        trend = "up" if ma10 > ma30 else "down"

        # ── Regime ──────────────────────────
        if adx > 25:
            regime = f"trend_{trend}"
        elif adx < 20:
            regime = "range_bound"
        else:
            regime = "weak_trend"

        # ── Volatility ──────────────────────
        atr = MarketRegimeDetector._atr(highs, lows, closes, 14)
        atr_pct = atr / closes[-1] * 100 if closes[-1] else 0
        if atr_pct > 3.0:
            volatility = "high"
        elif atr_pct < 1.0:
            volatility = "low"
        else:
            volatility = "normal"

        return MarketRegime(
            regime=regime,
            volatility=volatility,
            adx=round(adx, 2),
            scores={"adx": round(adx, 2), "atr_pct": round(atr_pct, 2)},
        )

    # ── ADX 计算 ────────────────────────────

    @staticmethod
    def _adx(highs, lows, closes, period=14) -> float:
        if len(closes) < period * 2:
            return 20.0

        tr_list = []
        plus_dm_list = []
        minus_dm_list = []

        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            tr_list.append(tr)

            up_move = highs[i] - highs[i - 1]
            down_move = lows[i - 1] - lows[i]

            plus_dm = up_move if up_move > down_move and up_move > 0 else 0
            minus_dm = down_move if down_move > up_move and down_move > 0 else 0

            plus_dm_list.append(plus_dm)
            minus_dm_list.append(minus_dm)

        # Wilder's smoothing
        atr_val = sum(tr_list[:period]) / period
        plus_di = sum(plus_dm_list[:period]) / period
        minus_di = sum(minus_dm_list[:period]) / period

        for i in range(period, len(tr_list)):
            atr_val = (atr_val * (period - 1) + tr_list[i]) / period
            plus_di = (plus_di * (period - 1) + plus_dm_list[i]) / period
            minus_di = (minus_di * (period - 1) + minus_dm_list[i]) / period

        if atr_val == 0:
            return 20.0

        di_diff = abs(plus_di - minus_di)
        di_sum = plus_di + minus_di
        dx = (di_diff / di_sum) * 100 if di_sum > 0 else 0

        return dx  # Simplified: return DX as proxy for ADX (full ADX needs EMA smoothing)

    @staticmethod
    def _atr(highs, lows, closes, period=14) -> float:
        if len(closes) < period:
            return 0.0
        tr_list = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            tr_list.append(tr)
        return sum(tr_list[-period:]) / period if tr_list else 0.0
