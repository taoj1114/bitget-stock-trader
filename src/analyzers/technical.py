"""技术指标计算器 — 纯函数式，输入 Kline[] 输出 TechnicalIndicators"""

import math
from typing import Optional

from src.core.types import Kline, TechnicalIndicators


class TechnicalAnalyzer:
    """纯函数式技术指标计算。不依赖外部状态。"""

    @staticmethod
    def calculate(klines: list[Kline]) -> TechnicalIndicators:
        """从 K线列表计算所有技术指标。

        Args:
            klines: K线列表（时间升序），至少 60 根
        """
        if len(klines) < 2:
            return _empty_indicators(klines[0].timestamp if klines else 0, "")

        symbol = getattr(klines[0], "symbol", "")
        closes = [k.close for k in klines]
        highs = [k.high for k in klines]
        lows = [k.low for k in klines]
        volumes = [k.volume for k in klines]

        n = len(closes)
        ind = TechnicalIndicators(symbol=symbol, timestamp=klines[-1].timestamp)

        # ── 均线 ─────────────────────────────
        ind.ma5 = _sma(closes, 5)
        ind.ma10 = _sma(closes, 10)
        ind.ma20 = _sma(closes, 20)
        ind.ma30 = _sma(closes, min(30, n))
        ind.ma60 = _sma(closes, min(60, n))
        ma30_val = ind.ma30

        # MA10/MA30 前一周期（金叉检测）
        if n >= 32:
            ind.ma10_prev = _sma(closes[:-1], 10)
            ind.ma30_prev = _sma(closes[:-1], min(30, n - 1))

        # ── EMA ──────────────────────────────
        ind.ema12 = _ema(closes, 12)
        ind.ema26 = _ema(closes, 26)

        # ── RSI(14) ──────────────────────────
        ind.rsi14 = _rsi(closes, 14)

        # ── MACD ─────────────────────────────
        ema12 = _ema(closes, 12)
        ema26 = _ema(closes, 26)
        if ema12 and ema26:
            macd_line = ema12[-1] - ema26[-1]
            # Signal line = EMA9 of MACD line
            macd_series = [e12 - e26 for e12, e26 in zip(ema12, ema26)]
            signal_line = _ema(macd_series, 9)[-1] if len(macd_series) >= 9 else macd_line
            ind.macd = round(macd_line, 6)
            ind.macd_signal = round(signal_line, 6)
            ind.macd_hist = round(macd_line - signal_line, 6)

        # ── 布林带 ───────────────────────────
        bb = _bollinger(closes, 20, 2)
        if bb:
            ind.bb_upper, ind.bb_middle, ind.bb_lower = bb

        # ── ATR(14) ──────────────────────────
        ind.atr14 = _atr(highs, lows, closes, 14)

        # ── 成交量 ───────────────────────────
        ind.volume_ma20 = _sma(volumes, min(20, n))
        if ind.volume_ma20 > 0 and volumes:
            ind.volume_ratio = round(volumes[-1] / ind.volume_ma20, 2)

        # ── VWAP (当日累计成交量加权均价) ─────
        # 15m/1H K线: 取最近一个交易日的K线 (非周末, 按UTC自然日近似)
        day_ts = klines[-1].timestamp // 86400000 * 86400000  # 当天0点(UTC)
        day_bars = [k for k in klines if k.timestamp >= day_ts and k.volume > 0]
        if len(day_bars) >= 1:
            pv_sum = sum((k.high + k.low + k.close) / 3 * k.volume for k in day_bars)
            vol_sum = sum(k.volume for k in day_bars)
            if vol_sum > 0:
                ind.vwap = round(pv_sum / vol_sum, 4)

        # ── 其他 ─────────────────────────────
        if n >= 2:
            ind.change_pct = round((closes[-1] / closes[-2] - 1) * 100, 2)

        return ind


# ── 内部计算函数 ──────────────────────────────────────

def _sma(values: list[float], period: int) -> float:
    """简单移动平均。"""
    if period <= 0 or len(values) < period:
        return 0.0
    return round(sum(values[-period:]) / period, 6)


def _ema(values: list[float], period: int) -> list[float]:
    """指数移动平均序列。"""
    if len(values) < period or period <= 0:
        return []
    multiplier = 2.0 / (period + 1)
    ema = [sum(values[:period]) / period]
    for v in values[period:]:
        ema.append((v - ema[-1]) * multiplier + ema[-1])
    return ema


def _rsi(closes: list[float], period: int = 14) -> float:
    """RSI 计算。"""
    if len(closes) < period + 1:
        return 50.0
    gains = []
    losses = []
    for i in range(1, period + 1):
        change = closes[-(period - i + 1)] - closes[-(period - i + 2)]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def _bollinger(closes: list[float], period: int = 20, std_dev: float = 2.0):
    """布林带上/中/下轨。"""
    if len(closes) < period:
        return None
    middle = _sma(closes, period)
    recent = closes[-period:]
    variance = sum((x - middle) ** 2 for x in recent) / period
    std = math.sqrt(variance)
    return (
        round(middle + std_dev * std, 6),
        round(middle, 6),
        round(middle - std_dev * std, 6),
    )


def _atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    """ATR 计算。"""
    if len(closes) < period + 1:
        return 0.0
    tr_list = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        tr_list.append(tr)

    if len(tr_list) < period:
        return round(sum(tr_list) / len(tr_list), 6)
    return round(sum(tr_list[-period:]) / period, 6)


def _empty_indicators(timestamp: int, symbol: str) -> TechnicalIndicators:
    return TechnicalIndicators(symbol=symbol, timestamp=timestamp)
