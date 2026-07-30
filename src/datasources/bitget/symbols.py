"""Bitget V3 — 合约信息查询

API 端点:
    GET /api/v3/market/instruments?category=USDT-FUTURES
        → symbol, symbolType, status, minLeverage, maxLeverage,
          pricePrecision, quantityPrecision, minOrderQty, fundInterval

所有字段为 STRING，用 _to_int() / _to_float() 安全转换。
"""

import logging
from typing import Optional

import httpx

from src.core.types import ContractInfo
from src.datasources.bitget.rate_limiter import _rate_limiter

logger = logging.getLogger(__name__)


class BitgetSymbolSource:
    """合约信息查询"""

    BASE_URL = "https://api.bitget.com"

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(base_url=self.BASE_URL, timeout=30.0)

    # ── 工具方法 ──────────────────────────────────────────

    @staticmethod
    def _to_float(value, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_int(value, default: int = 0) -> int:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _strip_usdt(raw_symbol: str) -> str:
        if raw_symbol.endswith("USDT"):
            return raw_symbol[:-4]
        return raw_symbol

    # ── 内部 ──────────────────────────────────────────────

    def _to_contract(self, item: dict) -> ContractInfo:
        """原始 API 字典 → ContractInfo。"""
        raw_symbol = item.get("symbol", "")
        return ContractInfo(
            symbol=self._strip_usdt(raw_symbol),
            raw_symbol=raw_symbol,
            symbol_type=item.get("symbolType", ""),
            status=item.get("status", ""),
            min_leverage=self._to_int(item.get("minLeverage")),
            max_leverage=self._to_int(item.get("maxLeverage")),
            price_precision=self._to_int(item.get("pricePrecision")),
            qty_precision=self._to_int(item.get("quantityPrecision")),
            min_order_qty=self._to_float(item.get("minOrderQty")),
            fund_interval=self._to_int(item.get("fundInterval")),
        )

    async def _fetch_instruments(self) -> list[dict]:
        """调用 instruments API，返回原始 data 数组。"""
        await _rate_limiter.acquire()
        try:
            response = await self._client.get(
                "/api/v3/market/instruments",
                params={"category": "USDT-FUTURES"},
            )
            response.raise_for_status()
            body = response.json()

            if body.get("code") != "00000":
                logger.warning(
                    "Bitget instruments API error: code=%s msg=%s",
                    body.get("code"),
                    body.get("msg"),
                )
                return []

            return body.get("data", [])
        except httpx.HTTPError as e:
            logger.warning("Bitget instruments request failed: %s", e)
            return []

    # ── 公开方法 ──────────────────────────────────────────

    async def get_all_symbols(self) -> list[ContractInfo]:
        """获取所有 USDT-FUTURES 合约信息。"""
        data = await self._fetch_instruments()
        return [self._to_contract(item) for item in data]

    async def get_stock_symbols(self) -> list[ContractInfo]:
        """获取美股合约（symbolType=="stock" 且 status=="online"）。"""
        all_symbols = await self.get_all_symbols()
        return [
            s
            for s in all_symbols
            if s.symbol_type == "stock" and s.status == "online"
        ]

    async def get_symbol_info(self, symbol: str) -> Optional[ContractInfo]:
        """查询单个合约信息（在全部合约中搜索，不限于 stock）。"""
        all_symbols = await self.get_all_symbols()
        target_raw = f"{symbol}USDT"
        for s in all_symbols:
            if s.raw_symbol == target_raw:
                return s
        return None

    async def close(self) -> None:
        """关闭 HTTP 客户端。"""
        await self._client.aclose()
