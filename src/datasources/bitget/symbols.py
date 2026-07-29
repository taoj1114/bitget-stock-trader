"""Bitget V3 — 合约信息"""

from typing import Optional

from src.core.types import ContractInfo
from src.datasources.bitget.market import BitgetMarketSource


class BitgetSymbolSource:
    """合约信息查询"""

    def __init__(self, market: BitgetMarketSource):
        self._market = market
        self._cache = None  # 缓存所有合约列表

    async def get_all_symbols(self) -> list[ContractInfo]:
        """获取所有 USDT-FUTURES 合约"""
        data = await self._market._rate_limited_get("/api/v3/market/instruments", {
            "category": "USDT-FUTURES",
        })

        symbols = []
        for i in data.get("data", []):
            symbols.append(ContractInfo(
                symbol=i["symbol"].replace("USDT", ""),
                raw_symbol=i["symbol"],
                symbol_type=i.get("symbolType", "unknown"),
                status=i.get("status", "unknown"),
                min_leverage=int(i.get("minLeverage", 1)),
                max_leverage=int(i.get("maxLeverage", 1)),
                price_precision=int(i.get("pricePrecision", 2)),
                qty_precision=int(i.get("quantityPrecision", 2)),
                min_order_qty=float(i.get("minOrderQty", 0.01)),
                fund_interval=int(i.get("fundInterval", 8)),
            ))
        return symbols

    async def get_stock_symbols(self) -> list[ContractInfo]:
        """仅获取美股合约 (symbolType=stock)"""
        all_syms = await self.get_all_symbols()
        return [s for s in all_syms if s.symbol_type == "stock" and s.status == "online"]

    async def get_symbol_info(self, symbol: str) -> Optional[ContractInfo]:
        """获取单个合约信息"""
        all_syms = await self.get_all_symbols()
        for s in all_syms:
            if s.symbol == symbol.upper():
                return s
        return None
