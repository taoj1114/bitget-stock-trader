"""交易路由 — 持仓 / 余额 / 历史 / 下单"""

from dataclasses import asdict

from fastapi import APIRouter

from src.api.dependencies import get_executor

router = APIRouter(tags=["trading"])


@router.get("/positions")
async def get_positions():
    executor = get_executor()
    positions = await executor.get_positions()
    return [{
        "id": p.id, "symbol": p.symbol, "side": p.side,
        "quantity": p.quantity, "entry_price": p.entry_price,
        "mark_price": p.mark_price, "stop_loss": p.stop_loss,
        "unrealized_pnl": p.unrealized_pnl,
        "opened_at": p.opened_at.isoformat() if p.opened_at else None,
    } for p in positions]


@router.get("/balance")
async def get_balance():
    executor = get_executor()
    balance = await executor.get_balance()
    return {
        "initial_capital": balance.initial_capital,
        "current_balance": balance.current_balance,
        "total_pnl": balance.total_pnl,
        "total_trades": balance.total_trades,
        "win_count": balance.win_count,
        "loss_count": balance.loss_count,
    }


@router.get("/history")
async def get_history(limit: int = 50):
    from src.trading.tracker import Tracker
    tracker = Tracker()
    try:
        trades = tracker.get_closed_trades(limit)
        return {"count": len(trades), "trades": trades}
    finally:
        tracker.close()


@router.post("/close/{position_id}")
async def close_position(position_id: str):
    executor = get_executor()
    result = await executor.close_position(position_id, "MANUAL")
    return {"status": result.status, "reason": result.reason}
