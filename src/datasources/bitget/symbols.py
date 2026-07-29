"""Bitget V3 — 合约信息

============================================================
TODO[Phase1]: 实现合约信息查询
============================================================

API 端点:
    GET /api/v3/market/instruments?category=USDT-FUTURES
        → symbol, symbolType, status, minLeverage, maxLeverage
          pricePrecision, quantityPrecision, minOrderQty, fundInterval

接口:
    class BitgetSymbolSource:
        async def get_all_symbols() -> list[ContractInfo]
        async def get_stock_symbols() -> list[ContractInfo]  # 过滤 symbolType=stock
        async def get_symbol_info(symbol) -> Optional[ContractInfo]

参考:
    - 数据模型: src/core/types.py → ContractInfo
    - 已验证: curl .../api/v3/market/instruments?category=USDT-FUTURES  # 250+ stock contracts

注意事项:
    - symbolType == "stock" 才是美股合约
    - status == "online" 才是可交易
    - fundInterval = 8 (8小时资金费率结算)
"""

from typing import Optional
from src.core.types import ContractInfo


class BitgetSymbolSource:
    """合约信息查询"""

    async def get_all_symbols(self) -> list[ContractInfo]:
        raise NotImplementedError("TODO[Phase1]: 实现 BitgetSymbolSource.get_all_symbols()")

    async def get_stock_symbols(self) -> list[ContractInfo]:
        raise NotImplementedError("TODO[Phase1]: 实现 BitgetSymbolSource.get_stock_symbols()")

    async def get_symbol_info(self, symbol: str) -> Optional[ContractInfo]:
        raise NotImplementedError("TODO[Phase1]: 实现 BitgetSymbolSource.get_symbol_info()")
