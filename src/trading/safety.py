"""多层安全系统 — 硬限制 + 条件熔断 + 断路器

Layer 1: Hard limits — max_leverage, max_positions, max_order_value → BLOCK
Layer 2: Conditional — consecutive_losses>5, daily_drawdown>5% → PAUSE
Layer 3: Circuit breaker — data failures>5, price spike>5% → READONLY
"""

from datetime import datetime, timezone
from typing import Optional

import logging
logger = logging.getLogger(__name__)

from src.core.types import SafetyVerdict, Signal, Position, AccountBalance


class SafetySystem:
    """多层安全系统。"""

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self.max_leverage = cfg.get("max_leverage", 1)
        self.max_positions = cfg.get("max_positions", 5)
        self.max_consecutive_losses = cfg.get("max_consecutive_losses", 5)
        self.max_daily_drawdown_pct = cfg.get("max_daily_drawdown_pct", 5.0)
        self.max_single_risk_pct = cfg.get("max_single_risk_pct", 2.0)

        # 断路器状态
        self._circuit_broken = False
        self._paused_until: Optional[datetime] = None

        # 累计计数器
        self._consecutive_losses = 0
        self._data_failures = 0

    # ── L1: 硬限制 ─────────────────────────────────

    def check_hard_limits(self, signal: Signal, positions: list[Position],
                          balance: AccountBalance) -> SafetyVerdict:
        """硬限制：下单时检查。"""
        # 断路器
        if self._circuit_broken:
            return SafetyVerdict(passed=False, reason="断路器已触发：只读模式", layer="circuit_breaker")

        # 暂停 (连续亏损/日回撤触发, 2h 自动恢复)
        if self._paused_until:
            if datetime.now(timezone.utc) < self._paused_until:
                return SafetyVerdict(passed=False,
                                    reason=f"暂停中至 {self._paused_until.isoformat()}", layer="conditional")
            else:
                # 暂停到期 → 自动恢复: 重置计数器
                self._paused_until = None
                self._consecutive_losses = 0
                logger.info("暂停到期, 自动恢复交易")

        # 最大持仓数
        if len(positions) >= self.max_positions:
            return SafetyVerdict(passed=False,
                                reason=f"持仓数已达上限 ({len(positions)}/{self.max_positions})", layer="hard")

        # 连续亏损 (暂停由 on_loss 设置的 _paused_until 控制, 上面已检查)
        if self._consecutive_losses >= self.max_consecutive_losses:
            return SafetyVerdict(passed=False,
                                reason=f"连续亏损 {self._consecutive_losses} 笔, 暂停中",
                                layer="conditional")

        # 单笔风险（合约宽容度更高：止损可能 5-10%）
        if signal.stop_loss > 0 and signal.entry_price > 0:
            risk_pct = abs(signal.entry_price - signal.stop_loss) / signal.entry_price * 100
            if risk_pct > self.max_single_risk_pct * 5:  # 5x 容忍（合约允许更大止损）
                return SafetyVerdict(passed=False,
                                    reason=f"单笔风险 {risk_pct:.1f}% 超限", layer="hard")

        return SafetyVerdict(passed=True, reason="", layer="hard")

    # ── L2: 条件熔断 ───────────────────────────────

    def check_daily_drawdown(self, current_balance: float, initial_capital: float) -> SafetyVerdict:
        """检查日内回撤。"""
        if initial_capital <= 0:
            return SafetyVerdict(passed=True, reason="", layer="conditional")

        drawdown_pct = (initial_capital - current_balance) / initial_capital * 100
        if drawdown_pct > self.max_daily_drawdown_pct:
            from datetime import timedelta
            self._paused_until = datetime.now(timezone.utc) + timedelta(hours=2)  # 2h 自动恢复
            logger.info("日内回撤 %.1f%% 超限 → 暂停开仓 2h", drawdown_pct)
            return SafetyVerdict(passed=False,
                                reason=f"日内回撤 {drawdown_pct:.1f}% 超限", layer="conditional")

        return SafetyVerdict(passed=True, reason="", layer="conditional")

    def on_loss(self):
        """记录一笔亏损。连续亏损≥阈值 → 暂停 2h (自动恢复, 防永久死锁)。"""
        self._consecutive_losses += 1
        if self._consecutive_losses >= self.max_consecutive_losses:
            from datetime import timedelta
            self._paused_until = datetime.now(timezone.utc) + timedelta(hours=2)
            logger.info("连续亏损 %d 笔 → 暂停开仓 2h (至 %s)",
                       self._consecutive_losses,
                       self._paused_until.strftime("%H:%M"))

    def on_win(self):
        """记录一笔盈利，重置计数器。"""
        self._consecutive_losses = 0

    # ── L3: 断路器 ─────────────────────────────────

    def record_data_failure(self):
        """记录数据源失败。"""
        self._data_failures += 1
        if self._data_failures > 5:
            self._circuit_broken = True

    def record_data_success(self):
        """数据源成功，重置计数器。"""
        self._data_failures = max(0, self._data_failures - 1)

    def record_price_spike(self, deviation_pct: float):
        """记录异常价格波动。"""
        if deviation_pct > 5.0:
            self._circuit_broken = True

    def reset_circuit(self):
        """手动重置断路器。"""
        self._circuit_broken = False
        self._paused_until = None

    # ── 状态查询 ───────────────────────────────────

    @property
    def is_readonly(self) -> bool:
        return self._circuit_broken

    @property
    def is_paused(self) -> bool:
        if self._paused_until is None:
            return False
        return datetime.now(timezone.utc) < self._paused_until

    def status(self) -> dict:
        return {
            "circuit_broken": self._circuit_broken,
            "paused": self.is_paused,
            "consecutive_losses": self._consecutive_losses,
            "data_failures": self._data_failures,
            "max_positions": self.max_positions,
        }
