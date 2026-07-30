"""MACD 轨迹因子 — MACD线/柱 + 扩散/收敛"""

import pandas as pd

from src.features.base import FeatureFunction


class MACDTrajectory(FeatureFunction):
    name = "macd_trajectory"

    def compute(self, df: pd.DataFrame, **context) -> dict:
        if len(df) < 35:
            return {}
        close = df["close"]

        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        hist = macd_line - signal_line

        n = len(close)

        macd_now = self._safe(macd_line.iloc[-1])
        signal_now = self._safe(signal_line.iloc[-1])
        hist_now = self._safe(hist.iloc[-1])

        # 1 天前
        hist_1d = self._safe(hist.iloc[-5]) if n >= 5 else hist_now
        hist_change = round(hist_now - hist_1d, 4)

        # 状态
        if macd_now > signal_now and hist_change > 0:
            status = "金叉后扩散中↑"
        elif macd_now > signal_now and hist_change < 0:
            status = "多头减弱"
        elif macd_now < signal_now and hist_change < 0:
            status = "死叉后扩散中↓"
        elif macd_now < signal_now and hist_change > 0:
            status = "空头减弱"
        else:
            status = "粘合"

        return {
            "macd_line": round(macd_now, 4),
            "macd_signal": round(signal_now, 4),
            "macd_hist": round(hist_now, 4),
            "macd_hist_change": hist_change,
            "macd_status": status,
        }
