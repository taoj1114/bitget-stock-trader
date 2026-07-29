"""Bitget V3 UTA API — 行情 + K线 + 订单本

============================================================
TODO[Phase1]: 实现 Bitget V3 行情数据源
============================================================

API 端点:
    GET /api/v3/market/tickers?category=USDT-FUTURES&symbol={SYMBOL}
        → 实时行情: lastPrice, openPrice24h, highPrice24h, lowPrice24h
          price24hPcnt, volume24h, turnover24h, indexPrice, markPrice
          fundingRate, openInterest, bid1Price, ask1Price

    GET /api/v3/market/candles?category=USDT-FUTURES&symbol={SYMBOL}&interval={1m}&limit={100}
        → K线: [ts, open, high, low, close, volume, turnover]

    GET /api/v3/market/orderbook?category=USDT-FUTURES&symbol={SYMBOL}&limit={50}
        → 订单本: {bids: [[price, size], ...], asks: [...], ts}

接口:
    class BitgetMarketSource(BaseDataSource):
        async def get_quote(self, symbol: str) -> Optional[Quote]
        async def get_klines(self, symbol: str, interval: str, limit: int) -> list[Kline]
        async def get_order_book(self, symbol: str, limit: int) -> Optional[OrderBook]

参考:
    - 数据模型: src/core/types.py → Quote, Kline, OrderBook
    - 接口定义: src/core/interfaces.py → DataSource
    - 伪代码: PSEUDOCODE.md 第3节
    - 已验证: curl https://api.bitget.com/api/v3/market/tickers?category=USDT-FUTURES&symbol=AAPLUSDT  # 200 OK

注意事项:
    - 限速: 20 req/s, 需要 rate limiter
    - 不需要 API Key (公开数据)
    - 返回 symbol = raw_symbol.replace('USDT', '')
    - 所有数值字段转 float
"""

from typing import Optional
from src.core.types import Quote, Kline, OrderBook
from src.datasources.base import BaseDataSource


class BitgetMarketSource(BaseDataSource):
    """Bitget V3 UTA 行情数据源"""

    async def get_quote(self, symbol: str) -> Optional[Quote]:
        raise NotImplementedError("TODO[Phase1]: 实现 Bitget.get_quote()")

    async def get_klines(
        self, symbol: str, interval: str = "1h", limit: int = 100
    ) -> list[Kline]:
        raise NotImplementedError("TODO[Phase1]: 实现 Bitget.get_klines()")

    async def get_order_book(self, symbol: str, limit: int = 50) -> Optional[OrderBook]:
        raise NotImplementedError("TODO[Phase1]: 实现 Bitget.get_order_book()")
