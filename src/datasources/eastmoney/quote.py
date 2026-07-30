"""Eastmoney 实时报价（含 PE/PB/市值）

API 端点:
    GET https://push2.eastmoney.com/api/qt/stock/get
        params: secid={secid}, fields=f43,f44,f45,f46,f48,f57,f58,f59,f60,f116,f170

字段说明:
    f43=price, f44=high, f45=low, f46=open, f48=amount(成交额)
    f57=code, f58=name, f59=decimals, f60=prev_close, f116=mcap, f170=change_pct

⚠️  price = f43 / 10^f59,   change_pct = f170 / 100
"""

import time
from typing import Optional

import httpx

from src.core.types import Quote
from src.datasources.base import BaseDataSource
from src.datasources.eastmoney import _rate_limit_async
from src.datasources.eastmoney.search import EastmoneySearch

# push2delay 为备用域名，海外 VPS 直连 push2 可能 502
PUSH2_PRIMARY = "https://push2delay.eastmoney.com/api/qt/stock/get"
PUSH2_FALLBACK = "https://push2.eastmoney.com/api/qt/stock/get"
PUSH2_FIELDS = "f43,f44,f45,f46,f48,f57,f58,f59,f60,f116,f170"

# 公共请求头，避免被拦截
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
}


class EastmoneyQuoteSource(BaseDataSource):
    """Eastmoney 行情数据源 — 通过 push2 接口获取美股实时报价"""

    def __init__(self, search: Optional[EastmoneySearch] = None) -> None:
        self._search = search or EastmoneySearch()

    async def get_quote(self, symbol: str) -> Optional[Quote]:
        """获取美股实时报价。

        先通过 EastmoneySearch 解析 symbol → secid，
        再调用 push2 API 获取行情数据。
        """
        # Step 1: 解析 symbol → secid
        info = self._search.resolve_symbol(symbol)
        if info is None:
            return None

        secid = info["secid"]

        # Step 2: 限速 + 请求（优先 push2delay，失败回退 push2）
        await _rate_limit_async()

        data = None
        for url in (PUSH2_PRIMARY, PUSH2_FALLBACK):
            try:
                async with httpx.AsyncClient(headers=_HEADERS, timeout=10.0) as client:
                    resp = await client.get(
                        url,
                        params={
                            "secid": secid,
                            "fields": PUSH2_FIELDS,
                        },
                    )
                if resp.status_code == 200:
                    raw = resp.json()
                    data = raw.get("data")
                    if data:
                        break
            except Exception:
                continue
        if data is None or not isinstance(data, dict):
            return None

        return self._parse_quote(data, info["code"])

    def _parse_quote(self, data: dict, code: str) -> Optional[Quote]:
        """将 push2 原始 JSON 映射为 Quote DTO"""
        try:
            decimals = int(data.get("f59", 2))
            scale = 10 ** decimals

            price = _safe_float(data.get("f43")) / scale
            high = _safe_float(data.get("f44")) / scale
            low = _safe_float(data.get("f45")) / scale
            open_price = _safe_float(data.get("f46")) / scale
            prev_close = _safe_float(data.get("f60")) / scale
            amount = _safe_float(data.get("f48"))          # 成交额
            mcap = _safe_float(data.get("f116"))            # 总市值

            # change_pct: 原始值 / 100 = 小数形式 (0.0153 = +1.53%)
            change_pct = _safe_float(data.get("f170")) / 100.0

        except (TypeError, ValueError, ZeroDivisionError):
            return None

        return Quote(
            symbol=code,
            price=price,
            open_24h=open_price,
            high_24h=high,
            low_24h=low,
            change_pct=change_pct,
            volume_24h=0.0,            # push2 不直接提供成交量
            turnover_24h=amount,
            index_price=prev_close,
            mark_price=price,
            funding_rate=0.0,
            open_interest=0.0,
            timestamp=int(time.time() * 1000),
        )


def _safe_float(val) -> float:
    """安全转型，空值/异常返回 0.0"""
    if val is None or val == "-":
        return 0.0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0
