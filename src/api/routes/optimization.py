"""优化路由 — 绩效 / 调优建议 / 应用建议"""

from fastapi import APIRouter

from src.api.dependencies import get_performance, get_tuner, get_registry, get_version_mgr
from src.trading.tracker import Tracker

router = APIRouter(tags=["optimization"])


@router.get("/performance")
async def get_performance_report():
    tracker = Tracker()
    try:
        trades = tracker.get_closed_trades(1000)
        perf = get_performance()
        report = perf.analyze(trades)
        return {
            "strategy_id": report.strategy_id,
            "trades": report.trades,
            "win_rate": report.win_rate,
            "profit_factor": report.profit_factor,
            "total_pnl": report.total_pnl,
            "sharpe": report.sharpe,
            "max_drawdown": report.max_drawdown,
            "per_strategy": report.per_strategy,
        }
    finally:
        tracker.close()


@router.get("/optimization/suggestions")
async def get_suggestions():
    tracker = Tracker()
    try:
        trades = tracker.get_closed_trades(1000)
        perf = get_performance()
        report = perf.analyze(trades)
        tuner = get_tuner()
        registry = get_registry()
        suggestions = await tuner.tune(report, registry.get_all())
        return {"count": len(suggestions), "suggestions": [
            {"strategy_id": s.strategy_id, "param_path": s.param_path,
             "current": s.current_value, "suggested": s.suggested_value,
             "confidence": s.confidence, "reasoning": s.reasoning}
            for s in suggestions
        ]}
    finally:
        tracker.close()


@router.post("/optimization/apply/{suggestion_index}")
async def apply_suggestion(suggestion_index: int):
    tracker = Tracker()
    try:
        trades = tracker.get_closed_trades(1000)
        perf = get_performance()
        report = perf.analyze(trades)
        tuner = get_tuner()
        registry = get_registry()
        suggestions = await tuner.tune(report, registry.get_all())
        if suggestion_index >= len(suggestions):
            return {"error": "Invalid index"}
        s = suggestions[suggestion_index]
        strategy = registry.get(s.strategy_id)
        if not strategy:
            return {"error": "Strategy not found"}
        old_value = getattr(strategy.params, s.param_path, None)
        setattr(strategy.params, str(s.param_path).replace(".", "_"), s.suggested_value)
        get_version_mgr().save(s.strategy_id, strategy.params, perf.win_rate)
        return {"strategy_id": s.strategy_id, "param": s.param_path,
                "old": old_value, "new": s.suggested_value}
    finally:
        tracker.close()
