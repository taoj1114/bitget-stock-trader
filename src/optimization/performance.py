"""绩效分析器 — 从交易记录计算胜率/夏普/回撤等指标"""

import math
from datetime import datetime, timezone
from typing import Optional

from src.core.types import TradeRecord, PerformanceMetrics


class PerformanceAnalyzer:
    """交易绩效分析器。"""

    def analyze(self, trades: list[dict], strategy_id: Optional[str] = None) -> PerformanceMetrics:
        """分析交易记录，输出绩效指标。

        Args:
            trades: 从 Tracker 获取的交易记录列表 (dict 格式)
            strategy_id: 限定策略（None = 全部）
        """
        # 筛选已平仓交易
        closed = [t for t in trades if t.get("type") != "OPEN"]
        if strategy_id:
            closed = [t for t in closed if t.get("strategy_id") == strategy_id]

        if not closed:
            return PerformanceMetrics(
                strategy_id=strategy_id or "all",
                message="无已平仓交易记录",
            )

        pnls = [t.get("pnl") or 0 for t in closed]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        win_rate = len(wins) / len(pnls) if pnls else 0
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        total_pnl = sum(pnls)

        # Profit factor
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (999 if gross_profit > 0 else 0)

        # Sharpe ratio (simplified: mean / std)
        mean_pnl = total_pnl / len(pnls)
        if len(pnls) > 1:
            variance = sum((p - mean_pnl) ** 2 for p in pnls) / len(pnls)
            std = math.sqrt(variance)
            sharpe = (mean_pnl / std * math.sqrt(252)) if std > 0 else 0  # Annualized
        else:
            sharpe = 0

        # Max drawdown (from cumulative PnL)
        cumsum = 0
        peak = 0
        max_dd = 0
        for p in pnls:
            cumsum += p
            peak = max(peak, cumsum)
            dd = peak - cumsum
            max_dd = max(max_dd, dd)

        # Average holding hours
        holding_times = [t.get("holding_hours") or 0 for t in closed]
        avg_holding = sum(holding_times) / len(holding_times) if holding_times else 0

        # Per strategy
        per_strategy = {}
        for t in closed:
            sid = t.get("strategy_id", "unknown")
            if sid not in per_strategy:
                per_strategy[sid] = {"trades": 0, "wins": 0, "losses": 0, "total_pnl": 0}
            per_strategy[sid]["trades"] += 1
            pnl = t.get("pnl") or 0
            per_strategy[sid]["total_pnl"] += pnl
            if pnl > 0:
                per_strategy[sid]["wins"] += 1
            elif pnl < 0:
                per_strategy[sid]["losses"] += 1

        for sid in per_strategy:
            s = per_strategy[sid]
            s["win_rate"] = round(s["wins"] / s["trades"], 3) if s["trades"] else 0

        return PerformanceMetrics(
            strategy_id=strategy_id or "all",
            trades=len(closed),
            win_rate=round(win_rate, 3),
            avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2),
            profit_factor=round(profit_factor, 2),
            total_pnl=round(total_pnl, 2),
            sharpe=round(sharpe, 2),
            max_drawdown=round(max_dd, 2),
            avg_holding_hours=round(avg_holding, 2),
            per_strategy=per_strategy,
        )
