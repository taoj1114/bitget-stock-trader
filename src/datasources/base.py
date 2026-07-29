"""数据源基类 + 注册器"""

from typing import Optional

from src.core.interfaces import DataSource, FundamentalSource, NewsSource
from src.core.types import Quote, Kline, OrderBook, ContractInfo, FundamentalData, NewsItem
from src.core.exceptions import NotImplementedForPhase


class BaseDataSource(DataSource):
    """数据源基类。子类只需实现接口定义的方法。"""

    @property
    def name(self) -> str:
        return self.__class__.__name__

    async def get_quote(self, symbol: str) -> Optional[Quote]:
        raise NotImplementedForPhase("1", f"{self.name}.get_quote()")

    async def get_klines(self, symbol: str, interval: str, limit: int = 100) -> list[Kline]:
        raise NotImplementedForPhase("1", f"{self.name}.get_klines()")

    async def get_order_book(self, symbol: str, limit: int = 50) -> Optional[OrderBook]:
        return None


class BaseFundamentalSource(FundamentalSource):
    @property
    def name(self) -> str:
        return self.__class__.__name__

    async def get_fundamentals(self, symbol: str) -> Optional[FundamentalData]:
        raise NotImplementedForPhase("1", f"{self.name}.get_fundamentals()")


class BaseNewsSource(NewsSource):
    @property
    def name(self) -> str:
        return self.__class__.__name__

    async def fetch_news(self, query: str, max_results: int = 15) -> list[NewsItem]:
        raise NotImplementedForPhase("1", f"{self.name}.fetch_news()")


class DataSourceRegistry:
    """数据源注册器 — 管理所有数据源的生命周期"""

    def __init__(self):
        self._sources: dict[str, DataSource] = {}
        self._fundamental_sources: dict[str, FundamentalSource] = {}
        self._news_sources: dict[str, NewsSource] = {}

    def register(self, source: DataSource) -> None:
        self._sources[source.name] = source

    def register_fundamental(self, source: FundamentalSource) -> None:
        self._fundamental_sources[source.name] = source

    def register_news(self, source: NewsSource) -> None:
        self._news_sources[source.name] = source

    def get(self, name: str) -> Optional[DataSource]:
        return self._sources.get(name)

    def get_fundamental(self, name: str) -> Optional[FundamentalSource]:
        return self._fundamental_sources.get(name)

    def get_news(self, name: str) -> Optional[NewsSource]:
        return self._news_sources.get(name)

    @property
    def all(self) -> list[DataSource]:
        return list(self._sources.values())

    @property
    def news_all(self) -> list[NewsSource]:
        return list(self._news_sources.values())

    def summary(self) -> str:
        lines = [f"  📡 {s.name}" for s in self.all]
        lines += [f"  📰 {s.name}" for s in self.news_all]
        return f"已注册 {len(self._sources)} 个数据源, {len(self._fundamental_sources)} 个基本面源, {len(self._news_sources)} 个新闻源\n" + "\n".join(lines)


# 全局注册器
registry = DataSourceRegistry()
