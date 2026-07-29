"""Eastmoney 股票代码搜索

将美股 symbol (AAPL) 解析为 Eastmoney 内部 secid 和 secucode
"""

import json
from typing import Optional

import httpx

from src.core.exceptions import SymbolNotFound, DataSourceError

_SEARCH_URL = "https://searchapi.eastmoney.com/api/suggest/get"
_SEARCH_TOKEN = "D43BF722C8E33BDC906FB84D85E326E8"

# MktNum 映射
MARKET_MAP = {
    105: ("NASDAQ", ".O"),
    106: ("NYSE", ".N"),
    107: ("OTC", ".O"),
}


class EastmoneySearch:
    """Eastmoney 代码搜索"""

    def __init__(self, mode: str = "auto"):
        """
        mode: auto → 直连优先，失败切代理
              direct → 直连
              proxy → 代理
        """
        self.mode = mode
        self._sessions = {}
        self._last_call = 0.0
        self._min_interval = 1.0  # Eastmoney 严格限速

    def _get_session(self, direct: bool) -> httpx.Client:
        if direct not in self._sessions:
            client = httpx.Client(timeout=10)
            client.trust_env = not direct
            self._sessions[direct] = client
        return self._sessions[direct]

    def resolve_symbol(self, query: str) -> Optional[dict]:
        """搜索代码，返回 secid 和 secucode

        返回:
            {"code": "AAPL", "name": "苹果", "secid": "105.AAPL",
             "secucode": "AAPL.O", "market": "NASDAQ"}
        """
        import time
        import random

        # 限速
        wait = self._min_interval - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait + random.uniform(0.1, 0.3))

        q = query.strip().upper()
        params = {
            "input": q,
            "type": 14,
            "token": _SEARCH_TOKEN,
            "count": 10,
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.eastmoney.com/",
        }

        for direct in [True, False] if self.mode == "auto" else [self.mode == "direct"]:
            try:
                session = self._get_session(direct)
                resp = session.get(_SEARCH_URL, params=params, headers=headers)
                self._last_call = time.time()
                data = resp.json()
                rows = (data.get("QuotationCodeTable") or {}).get("Data") or []
                for s in rows:
                    mkt = int(s.get("MktNum"))
                    if mkt in MARKET_MAP and str(s.get("Code", "")).upper() == q:
                        market_name, suffix = MARKET_MAP[mkt]
                        return {
                            "code": s["Code"],
                            "name": s.get("Name", ""),
                            "secid": f"{mkt}.{s['Code']}",
                            "secucode": f"{s['Code']}{suffix}",
                            "market": market_name,
                        }
            except Exception:
                if self.mode != "auto":
                    raise
                continue

        raise SymbolNotFound("Eastmoney", query)

    def close(self):
        for s in self._sessions.values():
            s.close()
