"""Eastmoney 股票代码搜索

============================================================
TODO[Phase1]: 实现 Eastmoney 代码搜索
============================================================

API 端点:
    GET https://searchapi.eastmoney.com/api/suggest/get
        params: input={TICKER}, type=14, token=D43BF722C8E33BDC906FB84D85E326E8, count=10
        → response.QuotationCodeTable.Data[].{Code, Name, MktNum}

MktNum 映射:
    105 → NASDAQ  (secucode 后缀 .O)
    106 → NYSE    (secucode 后缀 .N)
    107 → OTC     (secucode 后缀 .O)

接口:
    class EastmoneySearch:
        def resolve_symbol(self, query: str) -> Optional[dict]
            → {"code": "AAPL", "name": "苹果", "secid": "105.AAPL",
               "secucode": "AAPL.O", "market": "NASDAQ"}

参考:
    - 伪代码: PSEUDOCODE.md 第4节 (Eastmoney 部分)
    - 已验证: curl "https://searchapi.eastmoney.com/api/suggest/get?input=AAPL&type=14&token=D43BF722C8E33BDC906FB84D85E326E8"

注意事项:
    - 严格限速: 1 req/s
    - token 是公开的硬编码
    - 直连不通时自动切代理 (auto mode)
    - 过滤只取 MktNum 105/106/107
"""

from typing import Optional


class EastmoneySearch:
    """Eastmoney 代码搜索"""

    def resolve_symbol(self, query: str) -> Optional[dict]:
        raise NotImplementedError("TODO[Phase1]: 实现 EastmoneySearch.resolve_symbol()")
