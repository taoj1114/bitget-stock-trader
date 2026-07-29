"""Bitget V3 UTA API — 行情 + K线 + 订单本"""

import time
import random
from typing import Optional

import httpx

from src.core.types import Quote, Kline, OrderBook, OrderBookLevel, ContractInfo
from src.core.exceptions import DataSourceError, DataSourceRateLimit, DataSourceTimeout
from src.datasources.base import BaseDataSource
from src.cache.memory import default_cache

_BITGET_BASE = "https://api.bitget.com"


class BitgetMarketSource(BaseDataSource):
    """Bitget V3 UTA 行情数据源"""

    def __init__(self, base_url: str = _BITGET_BASE, rate_limit: int = 20):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=15)
        # 限速
        self._min_interval = 1.0 / max(rate_limit, 1)
        self._last_call = 0.0

    async def _rate_limited_get(self, path: str, params: dict = None) -> dict:
        """限速请求"""
        # 确保请求间隔
        wait = self._min_interval - (time.time() - self._last_call)
        if wait > 0:
            await self._async_sleep(wait + random.uniform(0.05, 0.2))

        url = f"{self.base_url}{path}"
        try:
            resp = await self._client.get(url, params=params)
            self._last_call = time.time()
            data = resp.json()
        except httpx.TimeoutException:
            raise DataSourceTimeout("Bitget", 15)
        except Exception as e:
            raise DataSourceError("Bitget", str(e))

        if data.get("code") != "00000":
            raise DataSourceError("Bitget", data.get("msg", "unknown error"))
        return data

    @staticmethod
    async def _async_sleep(seconds: float):
        """简化异步 sleep"""
        import asyncio
        await asyncio.sleep(seconds)

    async def get_quote(self, symbol: str) -> Optional[Quote]:
        """获取实时报价"""
        # 检查缓存
        cached = default_cache.get(f"bitget:quote:{symbol}")
        if cached:
            return cached

        raw_symbol = f"{symbol}USDT"
        data = await self._rate_limited_get("/api/v3/market/tickers", {
            "category": "USDT-FUTURES",
            "symbol": raw_symbol,
        })
        records = data.get("data", [])
        if not records:
            return None

        d = records[0]
        quote = Quote(
            symbol=symbol,
            price=float(d["lastPrice"]),
            open_24h=float(d.get("openPrice24h", 0)),
            high_24h=float(d.get("highPrice24h", 0)),
            low_24h=float(d.get("lowPrice24h", 0)),
            change_pct=float(d.get("price24hPcnt", 0)),
            volume_24h=float(d.get("volume24h", 0)),
            turnover_24h=float(d.get("turnover24h", 0)),
            index_price=float(d.get("indexPrice", 0)),
            mark_price=float(d.get("markPrice", 0)),
            funding_rate=float(d.get("fundingRate", 0)),
            open_interest=float(d.get("openInterest", 0)),
            bid=float(d.get("bid1Price", 0)),
            ask=float(d.get("ask1Price", 0)),
            bid_size=float(d.get("bid1Size", 0)),
            ask_size=float(d.get("ask1Size", 0)),
            timestamp=int(d.get("ts", 0)),
        )

        # 写入缓存 60s
        default_cache.set(f"bitget:quote:{symbol}", quote, ttl=60)
        return quote

    async def get_klines(
        self, symbol: str, interval: str = "1h", limit: int = 100
    ) -> list[Kline]:
        """获取K线

        interval: 1m,5m,15m,30m,1h,4h,6h,12h,1D,3D,1W,1M
        kLineType: MARKET (默认), INDEX, MARK
        """
        raw_symbol = f"{symbol}USDT"
        data = await self._rate_limited_get("/api/v3/market/candles", {
            "category": "USDT-FUTURES",
            "symbol": raw_symbol,
            "interval": interval,
            "limit": str(limit),
            "kLineType": "MARKET",
        })

        klines = []
        for row in data.get("data", []):
            # 格式: [ts, open, high, low, close, volume, turnover]
            klines.append(Kline(
                timestamp=int(row[0]),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
                turnover=float(row[6]),
            ))
        return klines

    async def get_order_book(self, symbol: str, limit: int = 50) -> Optional[OrderBook]:
        """获取订单本"""
        raw_symbol = f"{symbol}USDT"
        data = await self._rate_limited_get("/api/v3/market/orderbook", {
            "category": "USDT-FUTURES",
            "symbol": raw_symbol,
            "limit": str(limit),
        })
        records = data.get("data", {})
        if not records:
            return None

        return OrderBook(
            symbol=symbol,
            bids=[OrderBookLevel(price=float(p), size=float(s))
                  for p, s in records.get("bids", [])],
            asks=[OrderBookLevel(price=float(p), size=float(s))
                  for p, s in records.get("asks", [])],
            timestamp=int(records.get("ts", 0)),
        )

    async def close(self):
        await self._client.aclose()
