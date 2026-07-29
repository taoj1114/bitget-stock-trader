"""Eastmoney 实时报价（含 PE/PB/市值）

============================================================
TODO[Phase1]: 实现 Eastmoney Push2 报价获取
============================================================

API 端点:
    GET https://push2.eastmoney.com/api/qt/stock/get
        params: secid={secid}, fields=f43,f44,f45,f46,f48,f57,f58,f59,f60,f116,f170
        → f43=price, f44=high, f45=low, f46=open, f48=amount
          f57=code, f58=name, f59=decimals, f60=prev_close, f116=mcap, f170=change_pct

接口:
    class EastmoneyQuoteSource(BaseDataSource):
        async def get_quote(self, secid: str) -> Optional[Quote]

参考:
    - 伪代码: PSEUDOCODE.md 第4节 (Eastmoney 部分)
    - Eastmoney API skill 文档

注意事项:
    - 限速 1 req/s
    - push2 可能被海外 VPS 屏蔽，需要 push2delay 备用
    - f59 = decimal places, price = f43 / 10^f59
    - f170 = change_pct (raw), 除以 100 得百分比
"""

from typing import Optional
from src.core.types import Quote
from src.datasources.base import BaseDataSource


class EastmoneyQuoteSource(BaseDataSource):
    """Eastmoney 行情数据源"""

    async def get_quote(self, symbol: str) -> Optional[Quote]:
        raise NotImplementedError("TODO[Phase1]: 实现 EastmoneyQuoteSource.get_quote()")
