"""成交量因子 — 量比 + 量趋势 + 量价配合"""

import pandas as pd

from src.features.base import FeatureFunction


class VolumeFeatures(FeatureFunction):
    name = "volume_features"

    def compute(self, df: pd.DataFrame, **context) -> dict:
        if len(df) < 20:
            return {}
        volume = df["volume"]
        close = df["close"]
        n = len(volume)

        vol_ma20 = self._safe(self._sma(volume, 20).iloc[-1])
        vol_now = self._safe(volume.iloc[-1])
        vol_ratio = self._safe(vol_now / vol_ma20) if vol_ma20 > 0 else 1.0

        # 成交量趋势：近 5 根 vs 前 5 根
        if n >= 10:
            recent_5 = self._safe(volume.iloc[-5:].mean())
            prior_5 = self._safe(volume.iloc[-10:-5].mean())
            vol_trend = self._safe((recent_5 / prior_5 - 1) * 100) if prior_5 > 0 else 0
        else:
            vol_trend = 0

        # 量价配合：价涨量增 or 价跌量缩 = 健康
        if n >= 6:
            price_up = close.iloc[-1] > close.iloc[-6]
            vol_up = volume.iloc[-1] > volume.iloc[-6]
            confirmation = (price_up and vol_up) or (not price_up and not vol_up)
        else:
            confirmation = False

        return {
            "vol_ratio": round(vol_ratio, 2),
            "vol_trend_5bar": round(vol_trend, 2),
            "vol_price_confirmation": confirmation,
        }
