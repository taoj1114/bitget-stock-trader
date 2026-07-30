"""参数调优器 — 规则优化 + AI 推荐"""

from typing import Any

from src.core.types import ParamSuggestion, PerformanceMetrics


class ParamTuner:
    """参数调优器。基于规则分析绩效，生成参数调整建议。"""

    MIN_TRADES = 30

    async def tune(self, report: PerformanceMetrics, strategies: dict[str, Any]) -> list[ParamSuggestion]:
        suggestions = []
        for sid, perf in report.per_strategy.items():
            if perf.get("trades", 0) < self.MIN_TRADES:
                continue
            strategy = strategies.get(sid)
            if not strategy:
                continue
            suggestions.extend(self._rule_tune(sid, perf, strategy))
        return suggestions

    def _rule_tune(self, sid: str, perf: dict, strategy: Any) -> list[ParamSuggestion]:
        suggestions = []
        wr = perf.get("win_rate", 0)
        pf = perf.get("profit_factor", 0)
        trades = perf.get("trades", 0)
        params = getattr(strategy, "params", None)
        if not params:
            return suggestions

        if sid == "trend_break":
            if wr < 0.4 and pf > 1.5:
                cur = getattr(params, "fast_ma", 10)
                new = min(cur + 5, 50)
                if new != cur:
                    suggestions.append(ParamSuggestion(
                        strategy_id=sid, param_path="fast_ma",
                        current_value=cur, suggested_value=new,
                        confidence=0.6, based_on_trades=trades,
                        reasoning=f"胜率{wr:.0%}低但盈亏比高，延长快线{cur}→{new}",
                    ))
            if perf.get("total_pnl", 0) < 0:
                cur = getattr(params, "atr_sl_multiplier", 2.0)
                new = min(cur + 0.5, 5.0)
                if new != cur:
                    suggestions.append(ParamSuggestion(
                        strategy_id=sid, param_path="atr_sl_multiplier",
                        current_value=cur, suggested_value=new,
                        confidence=0.5, based_on_trades=trades,
                        reasoning="总PnL为负，扩大ATR止损倍数",
                    ))

        elif sid == "rsi_bounce":
            if wr < 0.35:
                cur = getattr(params, "oversold", 30)
                new = max(cur - 5, 15)
                if new != cur:
                    suggestions.append(ParamSuggestion(
                        strategy_id=sid, param_path="oversold",
                        current_value=cur, suggested_value=new,
                        confidence=0.5, based_on_trades=trades,
                        reasoning="胜率过低，收紧超卖阈值",
                    ))

        elif sid == "ai_composite":
            if wr < 0.4:
                cur = getattr(params, "min_confidence", 0.6)
                new = min(cur + 0.1, 0.9)
                if new != cur:
                    suggestions.append(ParamSuggestion(
                        strategy_id=sid, param_path="min_confidence",
                        current_value=cur, suggested_value=new,
                        confidence=0.4, based_on_trades=trades,
                        reasoning="AI策略胜率不足，提高置信度门槛",
                    ))

        return suggestions
