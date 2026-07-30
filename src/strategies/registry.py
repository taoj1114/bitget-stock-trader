"""策略注册器 — 策略发现 + 热加载 + 冷启动管理"""

from src.core.interfaces import SignalStrategy
from src.core.types import StrategyStatus


class StrategyRegistry:
    """策略注册器。"""

    def __init__(self):
        self._strategies: dict[str, SignalStrategy] = {}
        self._statuses: dict[str, StrategyStatus] = {}

    def register(self, strategy: SignalStrategy) -> None:
        sid = strategy.id
        self._strategies[sid] = strategy
        if sid not in self._statuses:
            self._statuses[sid] = StrategyStatus(
                phase="evaluation", weight=0.0, trades_done=0, trades_needed=30,
                message="冷启动: 新策略进入评估期",
            )

    def get(self, strategy_id: str) -> SignalStrategy | None:
        return self._strategies.get(strategy_id)

    def get_all(self) -> dict[str, SignalStrategy]:
        return dict(self._strategies)

    def get_active(self) -> list[SignalStrategy]:
        return [
            s for sid, s in self._strategies.items()
            if self._statuses.get(sid) and self._statuses[sid].phase == "active"
        ]

    def get_status(self, strategy_id: str) -> StrategyStatus | None:
        return self._statuses.get(strategy_id)

    def activate(self, strategy_id: str) -> bool:
        if strategy_id not in self._strategies:
            return False
        self._statuses[strategy_id] = StrategyStatus(phase="active", weight=1.0, message="已激活")
        return True

    def pause(self, strategy_id: str) -> bool:
        if strategy_id not in self._strategies:
            return False
        self._statuses[strategy_id] = StrategyStatus(phase="paused", weight=0.0, message="已暂停")
        return True

    def update_params(self, strategy_id: str, params) -> bool:
        s = self._strategies.get(strategy_id)
        if not s:
            return False
        s.params = params
        return True

    def summary(self) -> str:
        lines = [f"已注册 {len(self._strategies)} 个策略:"]
        for sid, s in self._strategies.items():
            status = self._statuses.get(sid)
            phase = status.phase if status else "?"
            lines.append(f"  {s.name} ({sid}) — {phase}")
        return "\n".join(lines)
