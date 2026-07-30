"""RSI 轨迹因子 — 当前 RSI + 1d/3d/7d 变化 + 轨迹分类"""

import pandas as pd

from src.features.base import FeatureFunction


class RSITrajectory(FeatureFunction):
    name = "rsi_trajectory"

    def compute(self, df: pd.DataFrame, **context) -> dict:
        if len(df) < 30:
            return {}
        close = df["close"]
        rsi_series = self._rsi(close, 14)
        n = len(rsi_series)

        rsi_now = self._safe(rsi_series.iloc[-1])
        rsi_1d = self._safe(rsi_series.iloc[-5]) if n >= 5 else rsi_now  # ~1 day in 1H bars
        rsi_3d = self._safe(rsi_series.iloc[-15]) if n >= 15 else rsi_now
        rsi_7d = self._safe(rsi_series.iloc[-35]) if n >= 35 else rsi_now

        delta_1d = round(rsi_now - rsi_1d, 2)
        delta_3d = round(rsi_now - rsi_3d, 2)
        delta_7d = round(rsi_now - rsi_7d, 2)

        # 区域
        if rsi_now >= 70:
            zone = "超买"
        elif rsi_now >= 60:
            zone = "偏强"
        elif rsi_now >= 40:
            zone = "中性"
        elif rsi_now >= 30:
            zone = "偏弱"
        else:
            zone = "超卖"

        # 轨迹
        trajectory = _classify_trajectory(rsi_now, delta_1d, delta_3d)

        return {
            "rsi_14": round(rsi_now, 1),
            "rsi_delta_1d": delta_1d,
            "rsi_delta_3d": delta_3d,
            "rsi_delta_7d": delta_7d,
            "rsi_zone": zone,
            "rsi_trajectory": trajectory,
        }


def _classify_trajectory(rsi_now: float, delta_1d: float, delta_3d: float) -> str:
    if rsi_now > 70 and delta_3d < 3:
        return "高位钝化"
    if rsi_now > 70 and delta_3d >= 3:
        return "强势冲高"
    if rsi_now > 50 and delta_3d > 10:
        return "快速拉升"
    if rsi_now > 50 and delta_3d > 3:
        return "温和上升"
    if rsi_now < 30 and delta_3d > -3:
        return "低位止跌"
    if rsi_now < 30 and delta_3d <= -3:
        return "持续超卖"
    if rsi_now < 50 and delta_3d < -10:
        return "快速下跌"
    if rsi_now < 50 and delta_3d < -3:
        return "温和下行"
    return "横盘"
