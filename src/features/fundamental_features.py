"""基本面动量因子 — 从 fund_history 计算增速变化方向"""

import pandas as pd

from src.features.base import FeatureFunction


class FundamentalMomentum(FeatureFunction):
    name = "fundamental_momentum"

    def compute(self, df: pd.DataFrame, **context) -> dict:
        """需要 context['fund_history'] — 最近 4 季度基本面列表"""
        history = context.get("fund_history", [])
        if not history:
            return {
                "fund_revenue_yoy": 0,
                "fund_rev_momentum": 0,
                "fund_profit_momentum": 0,
                "fund_momentum_signal": "无数据",
                "fund_roe": 0,
                "fund_quality": "无数据",
            }

        current = history[0] if history else {}
        previous = history[1] if len(history) > 1 else {}

        rev_yoy = current.get("revenue_yoy") or 0
        prev_rev_yoy = previous.get("revenue_yoy") or 0
        rev_momentum = round(rev_yoy - prev_rev_yoy, 2)

        profit_yoy = current.get("net_profit_yoy") or 0
        prev_profit_yoy = previous.get("net_profit_yoy") or 0
        profit_momentum = round(profit_yoy - prev_profit_yoy, 2)

        roe = current.get("roe") or 0

        # 动量信号
        if rev_momentum < -3 and profit_momentum < -3:
            momentum_signal = "双双恶化"
        elif rev_momentum < -3:
            momentum_signal = "边际放缓"
        elif rev_momentum > 3 and profit_momentum > 3:
            momentum_signal = "加速增长"
        elif rev_momentum > 3:
            momentum_signal = "增速改善"
        else:
            momentum_signal = "增速稳定"

        # 质量
        if roe > 20:
            quality = "优质"
        elif roe > 10:
            quality = "良好"
        else:
            quality = "一般"

        return {
            "fund_revenue_yoy": rev_yoy,
            "fund_rev_momentum": rev_momentum,
            "fund_profit_momentum": profit_momentum,
            "fund_momentum_signal": momentum_signal,
            "fund_roe": roe,
            "fund_quality": quality,
        }
