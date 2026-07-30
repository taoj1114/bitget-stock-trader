"""Bitget V3 UTA API — 行情 + K线 + 订单本

API 端点:
    GET /api/v3/market/tickers     → 实时行情
    GET /api/v3/market/candles     → K线数据
    GET /api/v3/market/orderbook   → 订单本深度

所有响应包裹在 {"code":"00000","msg":"success","data":...}
code != "00000" 视为失败。
"""

import logging
from typing import Optional

import httpx

from src.core.types import Kline, OrderBook, OrderBookLevel, Quote
from src.datasources.base import BaseDataSource
from src.datasources.bitget.rate_limiter import _rate_limiter

logger = logging.getLogger(__name__)


class BitgetMarketSource(BaseDataSource):
    """Bitget V3 UTA 行情数据源"""

    BASE_URL = "https://api.bitget.com"

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(base_url=self.BASE_URL, timeout=30.0)

    # ── 工具方法 ──────────────────────────────────────────

    @staticmethod
    def _to_float(value, default: float = 0.0) -> float:
        """安全字符串→float，失败返回 default。"""
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_int(value, default: int = 0) -> int:
        """安全字符串→int，失败返回 default。"""
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _strip_usdt(raw_symbol: str) -> str:
        """去掉尾部 USDT 后缀。"""
        if raw_symbol.endswith("USDT"):
            return raw_symbol[:-4]
        return raw_symbol

    # ── API 方法 ──────────────────────────────────────────

    async def get_quote(self, symbol: str) -> Optional[Quote]:
        """获取实时报价。

        GET /api/v3/market/tickers?category=USDT-FUTURES&symbol={symbol}USDT
        """
        await _rate_limiter.acquire()
        try:
            response = await self._client.get(
                "/api/v3/market/tickers",
                params={"category": "USDT-FUTURES", "symbol": f"{symbol}USDT"},
            )
            response.raise_for_status()
            body = response.json()

            if body.get("code") != "00000":
                logger.warning(
                    "Bitget tickers API error: code=%s msg=%s",
                    body.get("code"),
                    body.get("msg"),
                )
                return None

            data = body.get("data", [])
            if not data:
                return None

            item = data[0]

            return Quote(
                symbol=self._strip_usdt(item.get("symbol", "")),
                price=self._to_float(item.get("lastPrice")),
                open_24h=self._to_float(item.get("openPrice24h")),
                high_24h=self._to_float(item.get("highPrice24h")),
                low_24h=self._to_float(item.get("lowPrice24h")),
                change_pct=self._to_float(item.get("price24hPcnt")),
                volume_24h=self._to_float(item.get("volume24h")),
                turnover_24h=self._to_float(item.get("turnover24h")),
                index_price=self._to_float(item.get("indexPrice")),
                mark_price=self._to_float(item.get("markPrice")),
                funding_rate=self._to_float(item.get("fundingRate")),
                open_interest=self._to_float(item.get("openInterest")),
                bid=self._to_float(item.get("bid1Price")),
                ask=self._to_float(item.get("ask1Price")),
                bid_size=self._to_float(item.get("bid1Size")),
                ask_size=self._to_float(item.get("ask1Size")),
                timestamp=self._to_int(item.get("ts")),
            )
        except httpx.HTTPError as e:
            logger.warning("Bitget tickers request failed: %s", e)
            return None

    async def get_klines(
        self, symbol: str, interval: str = "1H", limit: int = 100
    ) -> list[Kline]:
        """获取K线数据。

        GET /api/v3/market/candles?category=USDT-FUTURES&symbol={symbol}USDT

        Note: interval 区分大小写 — "1H"/"4H"/"1D"大写, "1m"/"5m"/"15m"小写。
        candles 返回升序（旧→新），不 reverse。
        """
        await _rate_limiter.acquire()
        try:
            response = await self._client.get(
                "/api/v3/market/candles",
                params={
                    "category": "USDT-FUTURES",
                    "symbol": f"{symbol}USDT",
                    "interval": interval,
                    "limit": limit,
                },
            )
            response.raise_for_status()
            body = response.json()

            if body.get("code") != "00000":
                logger.warning(
                    "Bitget candles API error: code=%s msg=%s",
                    body.get("code"),
                    body.get("msg"),
                )
                return []

            data = body.get("data", [])
            if not data:
                return []

            klines: list[Kline] = []
            for row in data:
                klines.append(
                    Kline(
                        timestamp=self._to_int(row[0]),
                        open=self._to_float(row[1]),
                        high=self._to_float(row[2]),
                        low=self._to_float(row[3]),
                        close=self._to_float(row[4]),
                        volume=self._to_float(row[5]),
                        turnover=self._to_float(row[6]),
                    )
                )
            return klines
        except httpx.HTTPError as e:
            logger.warning("Bitget candles request failed: %s", e)
            return []

    async def get_order_book(
        self, symbol: str, limit: int = 50
    ) -> Optional[OrderBook]:
        """获取订单本深度。

        GET /api/v3/market/orderbook?category=USDT-FUTURES&symbol={symbol}USDT

        Note: 响应中 asks/bids 的键是 "a"/"b"（小写），值已是 float。
        """
        await _rate_limiter.acquire()
        try:
            response = await self._client.get(
                "/api/v3/market/orderbook",
                params={
                    "category": "USDT-FUTURES",
                    "symbol": f"{symbol}USDT",
                    "limit": limit,
                },
            )
            response.raise_for_status()
            body = response.json()

            if body.get("code") != "00000":
                logger.warning(
                    "Bitget orderbook API error: code=%s msg=%s",
                    body.get("code"),
                    body.get("msg"),
                )
                return None

            data = body.get("data")
            if not data:
                return None

            asks = [
                OrderBookLevel(price=float(item[0]), size=float(item[1]))
                for item in data.get("a", [])
            ]
            bids = [
                OrderBookLevel(price=float(item[0]), size=float(item[1]))
                for item in data.get("b", [])
            ]

            return OrderBook(
                symbol=symbol,
                bids=bids,
                asks=asks,
                timestamp=self._to_int(data.get("ts")),
            )
        except httpx.HTTPError as e:
            logger.warning("Bitget orderbook request failed: %s", e)
            return None

    async def close(self) -> None:
        """关闭 HTTP 客户端。"""
        await self._client.aclose()
