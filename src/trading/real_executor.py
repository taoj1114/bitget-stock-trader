"""实盘执行器 — 对接 Bitget 真实下单

接口与 Executor 规范一致。
"""

import logging, os
from typing import Optional

from src.core.types import Signal, Position, OrderResult, AccountBalance
from src.datasources.bitget.trader import BitgetTrader, BitgetCredentials

logger = logging.getLogger(__name__)


class RealExecutor:
    """Bitget 实盘执行器。"""

    def __init__(self, credentials: Optional[BitgetCredentials] = None,
                 safety: Optional[object] = None):
        self._trader = BitgetTrader(credentials)
        self._ready = self._trader.ready
        self._positions: dict[str, Position] = {}  # position_id → Position (内存缓存)
        self._equity = 10000  # 兜底（会在 execute_signal 时更新）
        self._contract_info: dict = {}  # symbol → 合约规格缓存
        self._on_position_closed = None  # 回调(symbol, pnl, reason): 托管SL/TP平仓通知
        self._safety = safety  # SafetySystem (连续亏损/日回撤熔断)
        self._day_initial_equity = None  # 当日初始净值 (日回撤基准)
        self._day_base_date = ""          # 当日日期 (跨天重置基准)
        from src.trading.tracker import Tracker
        self._tracker = Tracker(mode="real")

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

        # 实盘：查账户总净值 (余额 + 未实现盈亏)
        try:
            acct = await self._trader.get_account()
            self._equity = acct.equity  # 总净值, 用于每仓 = equity/5
        except Exception as e:
            # 账户查询失败 → 拒绝下单 (绝不用兜底值算仓位, 防灾难仓位)
            logger.error("账户查询失败, 拒绝开仓 %s: %s", signal.symbol, e)
            return OrderResult(status="REJECTED", reason="账户查询失败, 无法计算仓位")

        # ── 安全系统: 熔断检查 (连续亏损/日回撤) ──
        if self._safety:
            try:
                # 当日基准 (每天第一次调用时锁定)
                from datetime import datetime, timezone
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if self._day_base_date != today:
                    self._day_base_date = today
                    self._day_initial_equity = self._equity
                if self._day_initial_equity and self._day_initial_equity > 0:
                    verdict = self._safety.check_daily_drawdown(self._equity, self._day_initial_equity)
                    if not verdict.passed:
                        return OrderResult(status="REJECTED", reason=verdict.reason)
                verdict = self._safety.check_hard_limits(signal, list(self._positions.values()),
                                                        self.get_balance_sync())
                if not verdict.passed:
                    return OrderResult(status="REJECTED", reason=verdict.reason)
            except Exception as e:
                logger.warning("安全检查异常(放行): %s", e)

        # 预取合约规格 (minTradeNum/sizeMultiplier/minTradeUSDT)
        if signal.symbol not in self._contract_info:
            try:
                self._contract_info[signal.symbol] = await self._trader.get_contract_info(signal.symbol)
            except Exception:
                pass

        quantity = self._calc_quantity_sync(signal)

        if quantity <= 0:
            return OrderResult(status="REJECTED", reason="仓位为0")

        # 杠杆校验 (safety.max_leverage 配置生效)
        leverage = self._calc_leverage(signal)
        if self._safety and leverage > getattr(self._safety, "max_leverage", 100):
            return OrderResult(status="REJECTED",
                             reason=f"杠杆 {leverage}x 超过安全上限 {self._safety.max_leverage}x")

        # 最低订单量检查 ($5)
        notional = quantity * signal.entry_price
        if notional < 5:
            return OrderResult(status="REJECTED", reason=f"订单价值 ${notional:.2f} < 最低 $5")

        # 仓位上限 + 同品种去重 (先同步交易所实际持仓, 防止缓存脱节)
        try:
            await self.get_positions()
        except Exception:
            pass
        if signal.symbol in self._positions:
            return OrderResult(status="REJECTED",
                             reason=f"同品种已有持仓 {signal.symbol}")
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
            # 记录开仓
            try:
                self._tracker.record_open(
                    self._positions[pos_id], signal,
                    spread=0.0006)  # taker 费率
            except Exception as e:
                logger.warning("记录开仓失败: %s", e)
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
            # 记录平仓 (用持仓浮盈作为 PnL)
            try:
                if pos:
                    pnl = getattr(pos, "unrealized_pnl", 0) or 0
                    self._tracker.record_close(
                        pos, pos.mark_price, pnl, reason,
                        pos.strategy_id, funding_cost=0)
                    # 安全系统: 连续亏损计数
                    if self._safety:
                        try:
                            if pnl < 0:
                                self._safety.on_loss()
                            else:
                                self._safety.on_win()
                        except Exception:
                            pass
            except Exception as e:
                logger.warning("记录平仓失败: %s", e)
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
        # 清理已平仓的残留缓存 + 记录托管SL/TP触发的平仓
        for stale_id in list(self._positions.keys()):
            if stale_id not in seen_ids:
                stale = self._positions[stale_id]
                # 记录被 Bitget 托管 SL/TP 触发平掉的仓位 (本地 close_position 未调用)
                try:
                    pnl = getattr(stale, "unrealized_pnl", 0) or 0
                    self._tracker.record_close(
                        stale, stale.mark_price, pnl, "EXCHANGE_SLTP",
                        stale.strategy_id, funding_cost=0)
                    # 通知 AI 记忆层 (复盘学习需要看到止损结果)
                    if self._on_position_closed:
                        try:
                            self._on_position_closed(stale.symbol, pnl, "EXCHANGE_SLTP",
                                                     stale.mark_price)
                        except Exception:
                            pass
                    # 安全系统: 连续亏损计数 (托管平仓也算)
                    if self._safety:
                        try:
                            if pnl < 0:
                                self._safety.on_loss()
                            else:
                                self._safety.on_win()
                        except Exception:
                            pass
                except Exception:
                    pass
                del self._positions[stale_id]
        return result

    def get_balance_sync(self) -> AccountBalance:
        """同步版账户余额 (安全检查用)。"""
        return AccountBalance(
            initial_capital=self._equity,
            current_balance=self._equity,
            total_pnl=0,
            used_margin=0,
        )

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
        """净值 = 余额 + 未实现盈亏 (同时刷新 self._equity 供仓位计算)。"""
        if not self._ready:
            return 0.0
        try:
            acct = await self._trader.get_account()
            self._equity = acct.equity  # 同步内存值 (防过期兜底)
            return acct.equity
        except Exception:
            balance = await self.get_balance()
            unrealized = sum(p.unrealized_pnl for p in (await self.get_positions()))
            return balance.current_balance + unrealized

    # ═══ 辅助 ═══════════════════════════════════

    def _calc_quantity_sync(self, signal: Signal) -> float:
        """合约仓位 — 每仓=总余额1/5保证金 × 杠杆, 按合约规格取整。

        规则:
          1. 最多5仓 → 每仓保证金 = 总余额 / 5
          2. 仓位价值 = 保证金 × 杠杆倍数
          3. 名义价值 ≥ min_trade_usdt ($5): qty ≥ $5 / 价格
          4. 满足 min_trade_num (最小下单量) + size_multiplier (步进)
        """
        import math
        equity = self._get_equity_sync()
        if equity <= 0:
            return 0.0

        leverage = self._calc_leverage(signal)
        # 合约规格 (动态获取, 失败时默认 0.01/$5)
        contract = self._contract_info.get(signal.symbol, {})
        multiplier = contract.get("size_multiplier", 0.01)
        min_num = contract.get("min_trade_num", 0.01)
        min_usdt = contract.get("min_trade_usdt", 5)

        # 每仓保证金 = 总余额 1/5
        margin_per_position = equity / 5.0
        # 仓位价值 = 保证金 × 杠杆 → 数量
        qty = (margin_per_position * leverage) / signal.entry_price if signal.entry_price > 0 else 0
        # 满足最低名义价值 $5 → 最低数量
        min_qty_val = min_usdt / signal.entry_price if signal.entry_price > 0 else min_num
        # 数量取整: 步进向上取整, 且 ≥ 最小下单量
        qty = math.ceil(max(qty, min_qty_val) / multiplier) * multiplier
        if qty < min_num:
            qty = math.ceil(min_num / multiplier) * multiplier
        return round(qty, 4)

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
