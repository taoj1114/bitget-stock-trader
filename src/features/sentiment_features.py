"""情绪轨迹因子 — 从 sentiment_history 计算情绪方向和变化"""

import pandas as pd

from src.features.base import FeatureFunction


class SentimentTrajectory(FeatureFunction):
    name = "sentiment_trajectory"

    def compute(self, df: pd.DataFrame, **context) -> dict:
        """需要 context['sentiment_history'] — 最近 7 天情绪快照列表"""
        history = context.get("sentiment_history", [])
        if not history:
            return {
                "sentiment_pos_ratio": 0,
                "sentiment_neg_ratio": 0,
                "sentiment_delta_1d": 0,
                "sentiment_delta_3d": 0,
                "sentiment_trajectory": "无数据",
                "sentiment_signal": "中性",
            }

        today = history[0] if history else {}
        yesterday = history[1] if len(history) > 1 else {}
        three_days = history[2] if len(history) > 2 else {}

        pos_today = today.get("positive_ratio", 0) or 0
        neg_today = today.get("negative_ratio", 0) or 0
        pos_yest = yesterday.get("positive_ratio", 0) or 0
        pos_3d = three_days.get("positive_ratio", 0) or 0

        delta_1d = round(pos_today - pos_yest, 3)
        delta_3d = round(pos_today - pos_3d, 3)

        # 轨迹判断
        trajectory = _sentiment_trajectory(pos_today, delta_1d, delta_3d)

        # 信号
        if trajectory in ("持续改善", "温和改善"):
            signal = "积极"
        elif trajectory in ("持续恶化", "温和恶化"):
            signal = "消极"
        else:
            signal = "中性"

        return {
            "sentiment_pos_ratio": round(pos_today, 3),
            "sentiment_neg_ratio": round(neg_today, 3),
            "sentiment_delta_1d": delta_1d,
            "sentiment_delta_3d": delta_3d,
            "sentiment_trajectory": trajectory,
            "sentiment_signal": signal,
        }


def _sentiment_trajectory(pos: float, d1d: float, d3d: float) -> str:
    if d3d > 0.15:
        return "持续改善"
    if d3d > 0.05:
        return "温和改善"
    if d3d < -0.15:
        return "持续恶化"
    if d3d < -0.05:
        return "温和恶化"
    return "情绪稳定"
