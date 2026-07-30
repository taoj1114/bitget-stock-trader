"""价格因子 — 动量 + 均线偏离 + 布林带 + ATR"""

import pandas as pd

from src.features.base import FeatureFunction


class PriceMomentum(FeatureFunction):
    name = "price_momentum"

    def compute(self, df: pd.DataFrame, **context) -> dict:
        if len(df) < 24:
            return {}
        close = df["close"]
        n = len(close)

        mom_1h = self._safe(close.pct_change(1).iloc[-1] * 100)
        mom_4h = self._safe(close.pct_change(4).iloc[-1] * 100) if n >= 4 else 0
        mom_1d = self._safe(close.pct_change(24).iloc[-1] * 100) if n >= 24 else 0
        mom_3d = self._safe(close.pct_change(72).iloc[-1] * 100) if n >= 72 else 0

        # 加速度：1日动量 vs 前1日
        if n >= 48:
            prev_mom_1d = self._safe(close.pct_change(24).iloc[-24] * 100)
            accel = round(mom_1d - prev_mom_1d, 2)
        else:
            accel = 0.0

        return {
            "price_momentum_1h": round(mom_1h, 2),
            "price_momentum_4h": round(mom_4h, 2),
            "price_momentum_1d": round(mom_1d, 2),
            "price_momentum_3d": round(mom_3d, 2),
            "price_momentum_accel": accel,
        }


class PriceDeviation(FeatureFunction):
    name = "price_deviation"

    def compute(self, df: pd.DataFrame, **context) -> dict:
        if len(df) < 240:
            return {}
        close = df["close"]
        ma10 = self._sma(close, 240)  # 10d × 24h
        ma30 = self._sma(close, 720)  # 30d × 24h

        dev_ma10 = self._safe((close.iloc[-1] / ma10.iloc[-1] - 1) * 100) if ma10.iloc[-1] > 0 else 0
        dev_ma30 = self._safe((close.iloc[-1] / ma30.iloc[-1] - 1) * 100) if ma30.iloc[-1] > 0 else 0

        # 偏离变化
        n = len(close)
        if n >= 265:
            prev_dev = self._safe((close.iloc[-25] / ma10.iloc[-25] - 1) * 100)
            dev_change = round(dev_ma10 - prev_dev, 2)
        else:
            dev_change = 0.0

        alignment = "多头排列" if ma10.iloc[-1] > ma30.iloc[-1] else "空头排列"

        return {
            "price_dev_ma10_pct": round(dev_ma10, 2),
            "price_dev_ma30_pct": round(dev_ma30, 2),
            "price_dev_change": dev_change,
            "price_ma_alignment": alignment,
        }


class BBandFeatures(FeatureFunction):
    name = "bb_features"

    def compute(self, df: pd.DataFrame, **context) -> dict:
        if len(df) < 20:
            return {}
        close = df["close"]
        bb_mid = self._sma(close, 20)
        bb_std = close.rolling(20).std()
        bb_upper = bb_mid + 2 * bb_std
        bb_lower = bb_mid - 2 * bb_std

        if bb_upper.iloc[-1] == bb_lower.iloc[-1]:
            return {}

        position = self._safe((close.iloc[-1] - bb_lower.iloc[-1]) / (bb_upper.iloc[-1] - bb_lower.iloc[-1]))
        width = self._safe((bb_upper.iloc[-1] - bb_lower.iloc[-1]) / bb_mid.iloc[-1] * 100)

        n = len(close)
        width_prev = 0
        if n >= 25:
            prev_mid = bb_mid.iloc[-5]
            prev_upper = prev_mid + 2 * bb_std.iloc[-5]
            prev_lower = prev_mid - 2 * bb_std.iloc[-5]
            width_prev = self._safe((prev_upper - prev_lower) / prev_mid * 100)

        width_trend = "扩张" if width > width_prev * 1.1 else ("收缩" if width < width_prev * 0.9 else "稳定")

        return {
            "bb_position": round(position, 3),
            "bb_width_pct": round(width, 2),
            "bb_width_trend": width_trend,
        }


class ATRFeatures(FeatureFunction):
    name = "atr_features"

    def compute(self, df: pd.DataFrame, **context) -> dict:
        if len(df) < 20:
            return {}
        highs = df["high"]
        lows = df["low"]
        closes = df["close"]

        tr = pd.concat([
            highs - lows,
            (highs - closes.shift(1)).abs(),
            (lows - closes.shift(1)).abs(),
        ], axis=1).max(axis=1)

        atr14 = self._safe(tr.rolling(14).mean().iloc[-1])
        price = closes.iloc[-1]
        atr_pct = self._safe(atr14 / price * 100) if price > 0 else 0

        n = len(tr)
        if n >= 20:
            atr_5d_ago = self._safe(tr.rolling(14).mean().iloc[-6])
            atr_change = self._safe((atr14 / atr_5d_ago - 1) * 100) if atr_5d_ago > 0 else 0
        else:
            atr_change = 0.0

        return {
            "atr_14": round(atr14, 4),
            "atr_pct": round(atr_pct, 2),
            "atr_change_5d": round(atr_change, 2),
        }
