"""核心抽象接口 — 所有模块之间的契约"""

from abc import ABC, abstractmethod
from typing import Optional, Protocol, runtime_checkable

from src.core.types import (
    Quote, Kline, OrderBook, ContractInfo,
    FundamentalData, NewsItem,
    Signal, AnalysisContext,
    Position, OrderResult, AccountBalance,
    StrategyParams,
)


# ==================== 数据源接口 ====================

class DataSource(ABC):
    """行情/基本面数据源。

    所有行情源（Bitget、Eastmoney 等）实现此接口。
    调用者只依赖此接口，不依赖具体实现。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """数据源唯一标识"""
        ...

    @abstractmethod
    async def get_quote(self, symbol: str) -> Optional[Quote]:
        """获取实时报价"""
        ...

    @abstractmethod
    async def get_klines(
        self, symbol: str, interval: str, limit: int = 100
    ) -> list[Kline]:
        """获取K线数据

        Args:
            symbol: 交易对 (如 AAPL, NVDA)
            interval: 周期 (1m, 5m, 15m, 30m, 1h, 4h, 6h, 12h, 1D, 3D, 1W, 1M)
            limit: 条数 (最大 1000)
        """
        ...

    @abstractmethod
    async def get_order_book(
        self, symbol: str, limit: int = 50
    ) -> Optional[OrderBook]:
        """获取订单本深度"""
        ...

    @abstractmethod
    async def get_symbols(self) -> list[ContractInfo]:
        """获取可交易合约列表"""
        ...


class FundamentalSource(ABC):
    """基本面数据源（Eastmoney 等）"""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    async def get_fundamentals(self, symbol: str) -> Optional[FundamentalData]:
        """获取基本面数据"""
        ...


# ==================== 新闻源接口 ====================

class NewsSource(ABC):
    """新闻源。

    所有新闻源（SearXNG、Tavily 等）实现此接口。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """新闻源唯一标识"""
        ...

    @abstractmethod
    async def fetch_news(
        self, query: str, max_results: int = 15
    ) -> list[NewsItem]:
        """获取相关新闻"""
        ...


# ==================== 策略接口 ====================

class SignalStrategy(ABC):
    """交易策略。

    所有策略实现此接口，通过 evaluate() 方法输出信号。
    参数通过 params 属性热更新。
    """

    @property
    @abstractmethod
    def id(self) -> str:
        """策略唯一标识"""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """策略可读名称"""
        ...

    @property
    @abstractmethod
    def params(self) -> StrategyParams:
        """当前参数"""
        ...

    @params.setter
    def params(self, p: StrategyParams) -> None:
        """热更新参数（可选实现）"""
        ...

    @abstractmethod
    async def evaluate(self, ctx: AnalysisContext) -> Optional[Signal]:
        """评估并返回交易信号。

        返回 None 表示无信号。
        """
        ...


# ==================== 交易执行接口 ====================

class Executor(ABC):
    """交易执行器。

    RealExecutor 实现此接口。
    上层代码不关心具体实现。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """执行器名称 (real)"""
        ...

    @abstractmethod
    async def execute_signal(self, signal: Signal) -> OrderResult:
        """执行交易信号"""
        ...

    @abstractmethod
    async def close_position(self, position_id: str, reason: str) -> OrderResult:
        """平仓"""
        ...

    @abstractmethod
    async def get_positions(self) -> list[Position]:
        """获取当前持仓"""
        ...

    @abstractmethod
    async def get_balance(self) -> AccountBalance:
        """获取账户余额"""
        ...


# ==================== 风控接口 ====================

@runtime_checkable
class SafetyRule(Protocol):
    """安全规则协议 (鸭子类型)"""

    @property
    def name(self) -> str:
        ...

    async def check_order(self, order) -> "SafetyVerdict":
        """检查订单。返回 SafetyVerdict(passed=True/False, reason=...)"""
        ...


# ==================== 数据源健康接口 ====================

class DataHealth(ABC):
    """数据源健康状态"""

    @abstractmethod
    async def health_check(self) -> dict:
        """返回健康状态"""
        ...
