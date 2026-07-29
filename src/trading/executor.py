"""交易执行器接口 — Phase 3 实现完整逻辑"""

from abc import ABC, abstractmethod
from typing import Optional

from src.core.types import Position, OrderResult, AccountBalance, Signal


class Executor(ABC):
    """交易执行器抽象类。PaperExecutor / RealExecutor 继承。"""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    async def execute_signal(self, signal: Signal) -> OrderResult:
        """执行交易信号"""
        raise NotImplementedError("Phase 3 实现")

    @abstractmethod
    async def close_position(self, position_id: str, reason: str) -> OrderResult:
        raise NotImplementedError("Phase 3 实现")

    @abstractmethod
    async def get_positions(self) -> list[Position]:
        raise NotImplementedError("Phase 3 实现")

    @abstractmethod
    async def get_balance(self) -> AccountBalance:
        raise NotImplementedError("Phase 3 实现")
