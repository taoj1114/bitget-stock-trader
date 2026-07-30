"""Eastmoney 股票代码搜索

API 端点:
    GET https://searchapi.eastmoney.com/api/suggest/get
        params: input={TICKER}, type=14, token=..., count=10
        → response.QuotationCodeTable.Data[].{Code, Name, MktNum, SecurityCode}

MktNum 映射:
    105 → NASDAQ  (.O)    106 → NYSE  (.N)    107 → OTC  (.O)
"""

from typing import Optional

import httpx

from src.datasources.eastmoney import _rate_limit_sync

SEARCH_URL = "https://searchapi.eastmoney.com/api/suggest/get"
SEARCH_TOKEN = "D43BF722C8E33BDC906FB84D85E326E8"

# MktNum → (market_name, secucode_suffix)
MKT_MAP: dict[int, tuple[str, str]] = {
    105: ("NASDAQ", ".O"),
    106: ("NYSE",   ".N"),
    107: ("OTC",    ".O"),
}


class EastmoneySearch:
    """Eastmoney 代码搜索 — 将股票代码/名称解析为 secid/secucode"""

    def resolve_symbol(self, query: str) -> Optional[dict]:
        """搜索股票，返回第一个美股匹配项。

        Returns:
            dict(code, name, secid, secucode, market) 或 None
        """
        _rate_limit_sync()

        try:
            resp = httpx.get(
                SEARCH_URL,
                params={
                    "input": query.upper().strip(),
                    "type": "14",
                    "token": SEARCH_TOKEN,
                    "count": "10",
                },
                timeout=10.0,
            )
        except Exception:
            return None

        if resp.status_code != 200:
            return None

        try:
            data = resp.json()
        except Exception:
            return None

        items = data.get("QuotationCodeTable", {}).get("Data", [])
        if not items:
            return None

        for item in items:
            try:
                mkt_num = int(item.get("MktNum", 0))
            except (TypeError, ValueError):
                continue

            info = MKT_MAP.get(mkt_num)
            if info is None:
                continue

            code = item.get("Code", "")
            if not code:
                continue

            market_name, suffix = info
            secucode = item.get("SecurityCode", "") or f"{code}{suffix}"

            return {
                "code": code,
                "name": item.get("Name", ""),
                "secid": f"{mkt_num}.{code}",
                "secucode": secucode,
                "market": market_name,
            }

        return None
