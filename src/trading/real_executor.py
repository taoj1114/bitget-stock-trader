"""实盘执行器 — 对接 Bitget 真实下单

接口与 PaperExecutor 一致，可在配置中切换 paper/real 模式。
"""

import logging, os
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
        self._equity = 10000  # 兜底（会在 execute_signal 时更新）

    @property
    def ready(self) -> bool:
        return self._ready

    # ═══ 开仓 ═══════════════════════════════════

    async def execute_signal(self, signal: Signal) -> OrderResult:
        if not self._ready:
            return OrderResult(status="REJECTED", reason="Bitget API 未配置")

        side = "buy" if signal.action in ("BUY", "STRONG_BUY") else "sell"

        # 硬校验：必须要有止盈止损
        if signal.stop_loss <= 0 or signal.stop_loss is None:
            return OrderResult(status="REJECTED", reason="缺止损价位")
        if not signal.take_profits or signal.take_profits[0] <= 0:
            return OrderResult(status="REJECTED", reason="缺止盈价位")

        # 实盘：查账户可用保证金
        try:
            acct = await self._trader.get_account()
            self._equity = acct.available  # 可用 = 净值 - 已用保证金
        except Exception:
            pass

        quantity = self._calc_quantity_sync(signal)

        if quantity <= 0:
            return OrderResult(status="REJECTED", reason="仓位为0")

        # 最低订单量检查 ($5)
        notional = quantity * signal.entry_price
        if notional < 5:
            return OrderResult(status="REJECTED", reason=f"订单价值 ${notional:.2f} < 最低 $5")

        # 仓位上限 (Bitget 保证金自然限制)
        max_pos = 5
        if len(self._positions) >= max_pos:
            return OrderResult(status="REJECTED",
                             reason=f"持仓已达上限 ({len(self._positions)}/{max_pos})")

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
            order_id = resp.get("data", {}).get("orderId", "")
            pos_id = f"real_{signal.symbol}"  # 统一 id = real_{symbol}

            # 设置止盈止损
            hold_side = "long" if side == "buy" else "short"
            if signal.stop_loss > 0:
                sl_tpsl = "sell" if side == "buy" else "buy"
                try:
                    sl_resp = await self._trader.place_stop_order(
                        signal.symbol, hold_side, sl_tpsl,
                        signal.stop_loss, quantity, "pos_loss")
                    if sl_resp.get("code") == "00000":
                        logger.info("  ✅ 止损: %s @ $%.2f", signal.symbol, signal.stop_loss)
                    else:
                        logger.warning("  ⚠️ 止损失败: %s", sl_resp.get("msg",""))
                except Exception as e:
                    logger.warning("  ⚠️ 止损异常: %s", e)
            if signal.take_profits and signal.take_profits[0] > 0:
                tp_tpsl = "sell" if side == "buy" else "buy"
                try:
                    tp_resp = await self._trader.place_stop_order(
                        signal.symbol, hold_side, tp_tpsl,
                        signal.take_profits[0], quantity, "pos_profit")
                    if tp_resp.get("code") == "00000":
                        logger.info("  ✅ 止盈: %s @ $%.2f", signal.symbol, signal.take_profits[0])
                    else:
                        logger.warning("  ⚠️ 止盈失败: %s", tp_resp.get("msg",""))
                except Exception as e:
                    logger.warning("  ⚠️ 止盈异常: %s", e)

            self._positions[pos_id] = Position(
                id=pos_id, symbol=signal.symbol,
                side="LONG" if side == "buy" else "SHORT",
                quantity=quantity,
                entry_price=signal.entry_price,
                mark_price=signal.entry_price,  # 初始 mark = entry
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
        seen_ids = set()
        for p in real_positions:
            pid = f"real_{p.symbol}"
            seen_ids.add(pid)
            prev = self._positions.get(pid)
            pos = Position(
                id=f"real_{p.symbol}",
                symbol=p.symbol,
                side="LONG" if p.side == "long" else "SHORT",
                quantity=p.quantity,
                entry_price=p.entry_price,
                mark_price=p.mark_price,
                stop_loss=prev.stop_loss if prev else 0,  # 保留本地 SL/TP
                take_profit_levels=prev.take_profit_levels if prev else [],
                unrealized_pnl=p.unrealized_pnl,
                leverage=p.leverage,
            )
            result.append(pos)
            self._positions[pos.id] = pos  # 同步内存缓存
        # 清理已平仓的残留缓存
        for stale_id in list(self._positions.keys()):
            if stale_id not in seen_ids:
                del self._positions[stale_id]
        return result

    async def get_balance(self) -> AccountBalance:
        if not self._ready:
            return AccountBalance(current_balance=0)
        try:
            acct = await self._trader.get_account()
        except Exception:
            return AccountBalance(current_balance=0)
        return AccountBalance(
            initial_capital=acct.equity,  # 实盘：当前净值为基准
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
        """合约仓位 — 小账户均分保证金, 按最小交易量向上取整。"""
        import math
        equity = self._get_equity_sync()
        if equity <= 0:
            return 0.01

        leverage = self._calc_leverage(signal)
        multiplier = 0.01  # sizeMultiplier

        # 小账户 (≤$20): 总资金/5，每仓等分
        if equity <= 20:
            margin_per_position = equity * 0.95 / 5
            qty = (margin_per_position * leverage) / signal.entry_price
            qty = math.ceil(qty / multiplier) * multiplier
            # 保证名义价值 ≥ $5 且满足最小交易量
            min_qty = math.ceil(5 / signal.entry_price / multiplier) * multiplier
            return max(qty, min_qty)

        # 大账户 (>$20): 2% 风险
        risk_pct = 0.02
        if signal.stop_loss > 0 and signal.entry_price > 0:
            stop_pct = abs(signal.entry_price - signal.stop_loss) / signal.entry_price
            stop_pct = max(stop_pct, 0.01)
        else:
            stop_pct = 0.05

        raw_qty = (equity * risk_pct) / (signal.entry_price * stop_pct)
        max_by_margin = (equity * 0.8 * leverage) / signal.entry_price if equity > 0 else 0
        return max(0.01, min(raw_qty, max_by_margin))

    def _get_equity_sync(self) -> float:
        """同步获取净值（实盘：通过 execute_signal 时 API 更新）。"""
        return self._equity

    def _calc_leverage(self, signal: Signal) -> int:
        """杠杆分档：≤$20 → 10x, >$20 → 5x。"""
        equity = self._get_equity_sync()
        return 10 if equity <= 20 else 5
    async def tick(self, quotes: dict[str, float]) -> list[OrderResult]:
        """实盘 tick：同步持仓 + 本地 SL/TP 备份检查。"""
        results = []
        if not self._ready:
            return results
        # 同步 Bitget 实际持仓
        try:
            await self.get_positions()
        except Exception:
            pass
        # 本地 SL/TP 备份检查
        for pos in self._positions.values():
            price = quotes.get(pos.symbol, 0)
            if not price:
                continue
            if pos.stop_loss > 0:
                if pos.side == "LONG" and price <= pos.stop_loss:
                    r = await self.close_position(pos.id, "LOCAL_STOP_LOSS")
                    results.append(r)
                elif pos.side == "SHORT" and price >= pos.stop_loss:
                    r = await self.close_position(pos.id, "LOCAL_STOP_LOSS")
                    results.append(r)
        return results

    def _save_state(self):
        """写本地状态供仪表盘读取。"""
        import json
        state = {
            "equity": self._equity,
            "current_balance": self._equity,
            "total_pnl": 0,
            "used_margin": 0,
            "positions": [
                {
                    "symbol": p.symbol, "side": p.side, "quantity": p.quantity,
                    "entry_price": p.entry_price, "mark_price": p.mark_price,
                    "stop_loss": p.stop_loss, "opened_at": str(p.opened_at) if p.opened_at else None,
                    "leverage": getattr(p, 'leverage', 10), "unrealized_pnl": p.unrealized_pnl,
                }
                for p in self._positions.values()
            ],
        }
        path = os.path.join(os.path.dirname(__file__), "..", "..", "data", "trader_state.json")
        with open(path, "w") as f:
            json.dump(state, f, default=str)
