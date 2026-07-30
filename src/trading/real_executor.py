"""实盘执行器 — 对接 Bitget 真实下单

接口与 PaperExecutor 一致，可在配置中切换 paper/real 模式。
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from src.core.types import Signal, Position, OrderResult, AccountBalance
from src.datasources.bitget.trader import BitgetTrader, BitgetCredentials

logger = logging.getLogger(__name__)


class RealExecutor:
    """Bitget 实盘执行器。"""

    def __init__(self, credentials: Optional[BitgetCredentials] = None):
        self._trader = BitgetTrader(credentials)
        self._ready = self._trader.ready
        self._positions: dict[str, Position] = {}  # position_id → Position (内存缓存)

    @property
    def ready(self) -> bool:
        return self._ready

    # ═══ 开仓 ═══════════════════════════════════

    async def execute_signal(self, signal: Signal) -> OrderResult:
        if not self._ready:
            return OrderResult(status="REJECTED", reason="Bitget API 未配置")

        side = "buy" if signal.action in ("BUY", "STRONG_BUY") else "sell"
        quantity = self._calc_quantity_sync(signal)

        if quantity <= 0:
            return OrderResult(status="REJECTED", reason="仓位为0")

        try:
            resp = await self._trader.place_order(
                symbol=signal.symbol,
                side=side,
                trade_side="open",
                quantity=quantity,
                leverage=self._calc_leverage(signal),
            )
        except Exception as e:
            return OrderResult(status="REJECTED", reason=str(e))

        if resp.get("code") == "00000":
            logger.info("✅ 真实开仓: %s %s qty=%.1f", signal.symbol, side, quantity)
            pos_id = resp.get("data", {}).get("orderId", f"real_{signal.symbol}")
            self._positions[pos_id] = Position(
                id=pos_id, symbol=signal.symbol,
                side="LONG" if side == "buy" else "SHORT",
                quantity=quantity,
                entry_price=signal.entry_price,
                stop_loss=signal.stop_loss,
                take_profit_levels=[],
            )
            return OrderResult(status="FILLED", position_id=pos_id,
                             fill_price=signal.entry_price, fill_quantity=quantity)
        else:
            return OrderResult(status="REJECTED", reason=resp.get("msg", "unknown"))

    # ═══ 平仓 ═══════════════════════════════════

    async def close_position(self, position_id: str, reason: str) -> OrderResult:
        """平仓：与 PaperExecutor 接口一致。"""
        if not self._ready:
            return OrderResult(status="REJECTED", reason="未配置")

        pos = self._positions.get(position_id)
        symbol = pos.symbol if pos else position_id.replace("real_", "")
        side = ("long" if pos.side == "LONG" else "short") if pos else ""

        try:
            resp = await self._trader.close_position(symbol, side)
        except Exception as e:
            return OrderResult(status="REJECTED", reason=str(e))

        if resp.get("code") == "00000":
            logger.info("✅ 真实平仓: %s %s (%s)", symbol, side, reason)
            if position_id in self._positions:
                del self._positions[position_id]
            return OrderResult(status="CLOSED", reason=reason)
        return OrderResult(status="REJECTED", reason=resp.get("msg", "unknown"))

    # ═══ 查询 ═══════════════════════════════════

    async def get_positions(self) -> list[Position]:
        if not self._ready:
            return []
        try:
            real_positions = await self._trader.get_positions()
        except Exception:
            return list(self._positions.values())

        result = []
        for p in real_positions:
            pos = Position(
                id=f"real_{p.symbol}",
                symbol=p.symbol,
                side="LONG" if p.side == "long" else "SHORT",
                quantity=p.quantity,
                entry_price=p.entry_price,
                mark_price=p.mark_price,
                stop_loss=0,
                take_profit_levels=[],
                unrealized_pnl=p.unrealized_pnl,
                leverage=p.leverage,
            )
            result.append(pos)
            self._positions[pos.id] = pos  # 同步内存缓存
        return result

    async def get_balance(self) -> AccountBalance:
        if not self._ready:
            return AccountBalance(current_balance=0)
        try:
            acct = await self._trader.get_account()
        except Exception:
            return AccountBalance(current_balance=0)
        return AccountBalance(
            current_balance=acct.equity - acct.unrealized_pnl,
            total_pnl=acct.unrealized_pnl,
            used_margin=acct.used_margin,
        )

    async def get_equity(self) -> float:
        """净值 = 余额 + 未实现盈亏。"""
        if not self._ready:
            return 0.0
        try:
            acct = await self._trader.get_account()
            return acct.equity
        except Exception:
            balance = await self.get_balance()
            unrealized = sum(p.unrealized_pnl for p in (await self.get_positions()))
            return balance.current_balance + unrealized

    # ═══ 辅助 ═══════════════════════════════════

    def _calc_quantity_sync(self, signal: Signal) -> float:
        """合约仓位（同步版）。"""
        return max(0.01, round(signal.confidence * 10, 2))

    def _calc_leverage(self, signal: Signal) -> int:
        return 5  # 实盘保守 5x

    async def tick(self, quotes: dict[str, float]) -> list[OrderResult]:
        """实盘 tick：同步交易所持仓 + 检查止盈止损。"""
        if not self._ready:
            return []
        try:
            await self.get_positions()  # 同步缓存
        except Exception:
            pass
        return []  # 止盈止损在交易所托管

    def _save_state(self):
        """实盘无需本地状态（存储在交易所）。"""
        pass
