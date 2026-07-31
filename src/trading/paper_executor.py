"""纸盘交易执行器 — Bitget 美股合约模拟

合约特性:
    - USDT 本位线性合约
    - 杠杆 1-100x（默认 1x）
    - 资金费率 8h 结算
    - PnL = (exit - entry) × 张数（LONG）/ (entry - exit) × 张数（SHORT）
    - 保证金 = 仓位价值 / 杠杆
    - 精度: 价格 precision, 数量 precision

状态文件: data/trader_state.json
"""

import asyncio
import json
import logging
import math
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from src.core.interfaces import Executor
from src.core.types import (
    Position, OrderResult, AccountBalance, Signal, TradeRecord,
)
from src.trading.tracker import Tracker
from src.trading.safety import SafetySystem
from src.trading.slippage import SlippageModel
from src.core.sanitize import safe_json_dumps

logger = logging.getLogger(__name__)

STATE_FILE = "data/trader_state.json"
DEFAULT_LEVERAGE = 1       # 默认 1x 杠杆


class PaperExecutor(Executor):
    """纸盘合约模拟执行器。"""

    def __init__(self, initial_capital: float = 10000.0,
                 slippage: SlippageModel | None = None,
                 safety: SafetySystem | None = None,
                 tracker: Tracker | None = None):
        self._slippage = slippage or SlippageModel()
        self._safety = safety or SafetySystem()
        self._tracker = tracker or Tracker()
        self._contract_cache: dict[str, any] = {}  # symbol → ContractInfo

        state = self._load_state()
        self._balance = AccountBalance(
            initial_capital=initial_capital,
            current_balance=state.get("current_balance", initial_capital),
            total_pnl=state.get("total_pnl", 0.0),
            total_trades=state.get("total_trades", 0),
            win_count=state.get("win_count", 0),
            loss_count=state.get("loss_count", 0),
            used_margin=state.get("used_margin", 0.0),
            total_funding_cost=state.get("total_funding_cost", 0.0),
        )
        self._positions: dict[str, Position] = {}
        for pdata in state.get("positions", []):
            pos = self._deserialize_position(pdata)
            self._positions[pos.id] = pos

        # 累计已使用的保证金
        self._recalc_margin()

    # ═══ 合约信息缓存 ═══════════════════════════

    async def _get_contract(self, symbol: str):
        """获取合约信息（带缓存）。"""
        if symbol in self._contract_cache:
            return self._contract_cache[symbol]
        try:
            from src.datasources.bitget.symbols import BitgetSymbolSource
            src = BitgetSymbolSource()
            info = await src.get_symbol_info(symbol)
            if info:
                self._contract_cache[symbol] = info
            return info
        except Exception:
            return None

    def _recalc_margin(self):
        """重新计算已用保证金。"""
        total_margin = 0.0
        for pos in self._positions.values():
            position_value = pos.quantity * pos.entry_price
            total_margin += position_value / max(pos.leverage, 1)
        self._balance.used_margin = total_margin

    # ═══ Executor 接口 ═══════════════════════════

    @property
    def name(self) -> str:
        return "paper"

    async def execute_signal(self, signal: Signal) -> OrderResult:
        """执行合约交易信号。"""
        positions = list(self._positions.values())

        # 同品种去重
        existing = [p for p in positions if p.symbol == signal.symbol]
        if existing:
            return OrderResult(status="REJECTED", reason=f"{signal.symbol}已有仓位")

        # 安全检查
        verdict = self._safety.check_hard_limits(signal, positions, self._balance)
        if not verdict.passed:
            logger.warning("Safety blocked %s: %s", signal.symbol, verdict.reason)
            return OrderResult(status="REJECTED", reason=verdict.reason)

        # 获取合约信息
        contract = await self._get_contract(signal.symbol)
        qty_precision = 0
        min_qty = 1.0
        price_precision = 2
        max_available = DEFAULT_LEVERAGE
        if contract:
            qty_precision = contract.qty_precision
            min_qty = contract.min_order_qty
            price_precision = contract.price_precision
            max_available = contract.max_leverage

        # 杠杆按资金分档
        balance = self._balance.current_balance
        if balance <= 20:
            leverage = min(max_available, 20)   # 小资金 ≤20x
        else:
            leverage = min(max_available, 5)     # 大资金 ≤5x

        # 滑点
        spread = self._slippage.get_spread(signal.symbol)

        if signal.action in ("BUY", "STRONG_BUY"):
            fill_price = signal.entry_price + signal.entry_price * spread
            side = "LONG"
        else:
            fill_price = signal.entry_price - signal.entry_price * spread
            side = "SHORT"

        # 精度取整
        fill_price = round(fill_price, price_precision)

        # 计算仓位（杠杆感知）
        quantity = self._calc_quantity(signal, fill_price, leverage, qty_precision, min_qty)
        if quantity < min_qty:
            return OrderResult(status="REJECTED", reason=f"Quantity {quantity} < min {min_qty}")

        # 保证金检查（含浮亏）
        position_value = quantity * fill_price
        margin_required = position_value / leverage
        equity = self._equity()
        available = equity - self._balance.used_margin
        if margin_required > available:
            return OrderResult(status="REJECTED",
                             reason=f"保证金不足: 需要${margin_required:.0f} 可用${available:.0f}")

        # 扣保证金
        self._balance.used_margin += margin_required

        position_id = str(uuid.uuid4())[:8]
        position = Position(
            id=position_id, symbol=signal.symbol, side=side,
            quantity=quantity, entry_price=fill_price,
            mark_price=fill_price,
            stop_loss=signal.stop_loss,
            take_profit_levels=[
                type("TakeProfitLevel", (), {
                    "level": i+1, "price": tp, "filled": False,
                    "ratio": 1/len(signal.take_profits),
                })
                for i, tp in enumerate(signal.take_profits)
            ] if signal.take_profits else [],
            opened_at=datetime.now(timezone.utc),
            strategy_id=signal.strategy_id,
            leverage=leverage,
            funding_rate=0.0,
            generating_params=signal.generating_params,  # 记录参数
        )

        self._positions[position_id] = position
        self._tracker.record_open(position, signal, spread)

        logger.info(
            "开仓: %s %s qty=%.1f @ $%.2f leverage=%dx margin=$%.0f",
            signal.symbol, side, quantity, fill_price, leverage, margin_required,
        )

        return OrderResult(
            status="FILLED", position_id=position_id,
            fill_price=fill_price, fill_quantity=quantity,
            timestamp=int(time.time() * 1000),
        )

    async def close_position(self, position_id: str, reason: str) -> OrderResult:
        """平仓（含资金费率 + 释放保证金）。"""
        pos = self._positions.pop(position_id, None)
        if pos is None:
            return OrderResult(status="REJECTED", reason="Position not found")

        exit_price = pos.mark_price
        spread = self._slippage.get_spread(pos.symbol)
        if pos.side == "LONG":
            exit_price *= (1 - spread)
        else:
            exit_price *= (1 + spread)

        # ── 合约 PnL ──
        if pos.side == "LONG":
            pnl = (exit_price - pos.entry_price) * pos.quantity
        else:
            pnl = (pos.entry_price - exit_price) * pos.quantity

        # ── 资金费率成本 ──
        holding_hours = 0.0
        if pos.opened_at:
            try:
                now = datetime.now(timezone.utc)
                opened = pos.opened_at
                if opened.tzinfo is None:
                    opened = opened.replace(tzinfo=timezone.utc)
                holding_hours = (now - opened).total_seconds() / 3600
            except Exception:
                pass

        funding_cost = 0.0
        fund_interval = 8.0  # 默认 8h（可从 contract 获取）
        if pos.funding_rate != 0 and holding_hours > 0:
            # 每 8h 结算一次
            settlements = math.floor(holding_hours / fund_interval)
            if settlements > 0:
                position_value = pos.quantity * pos.entry_price
                funding_cost = position_value * pos.funding_rate * settlements
                # LONG 持有多头且费率为正 → 支付资金费
                # SHORT 持有空头且费率为正 → 收取资金费
                if pos.side == "LONG":
                    pnl -= funding_cost
                else:
                    pnl += funding_cost

        # ── 更新余额 ──
        self._balance.total_pnl += pnl
        self._balance.current_balance += pnl
        self._balance.total_funding_cost += funding_cost
        self._balance.total_trades += 1

        if pnl > 0:
            self._balance.win_count += 1
            self._safety.on_win()
        else:
            self._balance.loss_count += 1
            self._safety.on_loss()

        # 释放保证金
        position_value = pos.quantity * pos.entry_price
        self._balance.used_margin -= position_value / max(pos.leverage, 1)
        if self._balance.used_margin < 0:
            self._balance.used_margin = 0.0

        self._tracker.record_close(pos, exit_price, pnl, reason, pos.strategy_id, funding_cost)
        self._save_state()

        logger.info(
            "平仓: %s %s qty=%.1f PnL=$%.0f 资金费=$%.2f",
            pos.symbol, pos.side, pos.quantity, pnl, funding_cost,
        )

        return OrderResult(status="FILLED", position_id=position_id,
                          fill_price=exit_price, fill_quantity=pos.quantity,
                          reason=f"Closed: {reason} (PnL=${pnl:.2f})")

    async def get_positions(self) -> list[Position]:
        return list(self._positions.values())

    async def get_balance(self) -> AccountBalance:
        return self._balance

    async def get_equity(self) -> float:
        """净值 = 余额 + 所有持仓浮盈浮亏。"""
        unrealized = sum(p.unrealized_pnl for p in self._positions.values())
        return self._balance.current_balance + unrealized

    def _equity(self) -> float:
        """同步版净值（内部用）。"""
        unrealized = sum(p.unrealized_pnl for p in self._positions.values())
        return self._balance.current_balance + unrealized

    # ═══ 止盈止损 + 未实现盈亏 ══════════════════

    async def tick(self, quotes: dict[str, float]) -> list[OrderResult]:
        results = []
        to_close = []

        for pid, pos in self._positions.items():
            new_price = quotes.get(pos.symbol)
            if new_price:
                pos.mark_price = new_price

            # 更新未实现盈亏
            if pos.side == "LONG":
                pos.unrealized_pnl = (pos.mark_price - pos.entry_price) * pos.quantity
            else:
                pos.unrealized_pnl = (pos.entry_price - pos.mark_price) * pos.quantity

            # 止损
            if pos.stop_loss > 0:
                if pos.side == "LONG" and pos.mark_price <= pos.stop_loss:
                    to_close.append((pid, "STOP_LOSS"))
                    continue
                if pos.side == "SHORT" and pos.mark_price >= pos.stop_loss:
                    to_close.append((pid, "STOP_LOSS"))
                    continue

            # 止盈
            for tp in pos.take_profit_levels:
                if tp.filled:
                    continue
                if pos.side == "LONG" and pos.mark_price >= tp.price:
                    tp.filled = True
                    to_close.append((pid, f"TAKE_PROFIT_{tp.level}"))
                    break
                if pos.side == "SHORT" and pos.mark_price <= tp.price:
                    tp.filled = True
                    to_close.append((pid, f"TAKE_PROFIT_{tp.level}"))
                    break

        for pid, reason in to_close:
            result = await self.close_position(pid, reason)
            results.append(result)

        # ── 爆仓检测 ──
        equity = self._equity()
        if self._balance.used_margin > 0:
            margin_ratio = equity / self._balance.used_margin
            if margin_ratio < 0.5:  # 维持保证金率 50%
                logger.warning("⚠️ 爆仓警告: 净值/保证金=%.0f%%", margin_ratio * 100)
                # 强制平掉亏损最大的仓位
                worst = max(self._positions.values(),
                           key=lambda p: abs(p.unrealized_pnl))
                result = await self.close_position(worst.id, "LIQUIDATION")
                results.append(result)

        return results

    # ═══ 动态仓位 ═══════════════════════════════

    def _calc_quantity(self, signal: Signal, fill_price: float,
                       leverage: int, qty_precision: int,
                       min_qty: float) -> float:
        """全仓模式动态仓位。

        核心公式：quantity = (equity × risk%) / (entry × stop_pct)
        浮盈可加仓，AI 止盈止损控制风险。

        约束：
            - margin_required ≤ available × 80%（总敞口限制，AI保障安全）
        """
        equity = self._equity()
        if equity <= 0:
            return min_qty

        risk_pct = 0.02  # 每笔最多亏 2% 净值

        if signal.stop_loss > 0 and signal.entry_price > 0:
            stop_pct = abs(signal.entry_price - signal.stop_loss) / signal.entry_price
            stop_pct = max(stop_pct, 0.01)
        else:
            stop_pct = 0.05

        # 基于净值风险计算基准仓位
        risk_amount = equity * risk_pct
        raw_qty = risk_amount / (fill_price * stop_pct)

        # 保证金约束：不超过可用余额 80%（全仓模式）
        available = equity - self._balance.used_margin
        max_by_margin = (available * 0.8 * leverage) / fill_price if available > 0 else 0

        quantity = min(raw_qty, max_by_margin)

        if qty_precision >= 0:
            quantity = round(quantity, qty_precision)

        return max(min_qty, min(1000.0, quantity))

    # ═══ 状态持久化 ═══════════════════════════

    def _save_state(self):
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        state = {
            "current_balance": self._balance.current_balance,
            "equity": self._equity(),
            "total_pnl": self._balance.total_pnl,
            "total_trades": self._balance.total_trades,
            "win_count": self._balance.win_count,
            "loss_count": self._balance.loss_count,
            "used_margin": self._balance.used_margin,
            "total_funding_cost": self._balance.total_funding_cost,
            "positions": [self._serialize_position(p) for p in self._positions.values()],
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(STATE_FILE, "w") as f:
            f.write(safe_json_dumps(state, indent=2, default=str))

    def _load_state(self) -> dict:
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE) as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    @staticmethod
    def _serialize_position(pos: Position) -> dict:
        return {
            "id": pos.id, "symbol": pos.symbol, "side": pos.side,
            "quantity": pos.quantity, "entry_price": pos.entry_price,
            "mark_price": pos.mark_price, "stop_loss": pos.stop_loss,
            "take_profit_levels": [
                {"level": tp.level, "price": tp.price, "filled": tp.filled, "ratio": tp.ratio}
                for tp in pos.take_profit_levels
            ],
            "unrealized_pnl": pos.unrealized_pnl,
            "opened_at": pos.opened_at.isoformat() if pos.opened_at else None,
            "strategy_id": pos.strategy_id,
            "leverage": pos.leverage,
            "funding_rate": pos.funding_rate,
        }

    @staticmethod
    def _deserialize_position(data: dict) -> Position:
        opened_at = data.get("opened_at")
        if opened_at:
            opened_at = datetime.fromisoformat(opened_at)
        return Position(
            id=data["id"], symbol=data["symbol"], side=data["side"],
            quantity=data["quantity"], entry_price=data["entry_price"],
            mark_price=data.get("mark_price", data["entry_price"]),
            stop_loss=data["stop_loss"],
            take_profit_levels=[
                type("TakeProfitLevel", (), tp) for tp in data.get("take_profit_levels", [])
            ],
            unrealized_pnl=data.get("unrealized_pnl", 0.0),
            opened_at=opened_at,
            strategy_id=data.get("strategy_id", ""),
            leverage=data.get("leverage", 1),
            funding_rate=data.get("funding_rate", 0.0),
        )
