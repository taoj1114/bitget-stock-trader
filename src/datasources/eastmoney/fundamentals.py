"""Eastmoney 基本面数据 — 财务指标

API 端点:
    GET https://datacenter-web.eastmoney.com/api/data/v1/get
        params: reportName=RPT_USF10_FN_GMAININDICATOR
                filter=(SECUCODE="{secucode}")
                columns=ALL, pageSize=1, pageNumber=1
                sortColumns=REPORT_DATE, sortTypes=-1

响应字段:
    OPERATE_INCOME, OPERATE_INCOME_YOY, PARENT_HOLDER_NETPROFIT,
    BASIC_EPS, ROE_AVG, GROSS_PROFIT_RATIO, NET_PROFIT_RATIO,
    DEBT_ASSET_RATIO, REPORT_DATE

⚠️  营收/利润单位是元，百分比字段是原始值（20.5 = 20.5%）
"""

from typing import Optional

import httpx

from src.core.types import FundamentalData
from src.datasources.base import BaseFundamentalSource
from src.datasources.eastmoney import _rate_limit_async
from src.datasources.eastmoney.search import EastmoneySearch

DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
REPORT_NAME = "RPT_USF10_FN_GMAININDICATOR"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://data.eastmoney.com/",
}


class EastmoneyFundamentalSource(BaseFundamentalSource):
    """Eastmoney 基本面数据源 — 通过 datacenter API 获取美股财报"""

    def __init__(self, search: Optional[EastmoneySearch] = None) -> None:
        self._search = search or EastmoneySearch()

    async def get_fundamentals(self, symbol: str) -> Optional[FundamentalData]:
        """获取最新一期财报数据。

        先通过 EastmoneySearch 解析 symbol → secucode，
        再调用 datacenter API 获取财务指标。
        """
        # Step 1: 解析 symbol → secucode
        info = self._search.resolve_symbol(symbol)
        if info is None:
            return None

        secucode = info["secucode"]

        # Step 2: 限速 + 请求
        await _rate_limit_async()

        filter_expr = f'(SECUCODE="{secucode}")'

        try:
            async with httpx.AsyncClient(headers=_HEADERS, timeout=15.0) as client:
                resp = await client.get(
                    DATACENTER_URL,
                    params={
                        "reportName": REPORT_NAME,
                        "filter": filter_expr,
                        "columns": "ALL",
                        "pageSize": "1",
                        "pageNumber": "1",
                        "sortColumns": "REPORT_DATE",
                        "sortTypes": "-1",
                    },
                )
        except Exception:
            return None

        if resp.status_code != 200:
            return None

        try:
            raw = resp.json()
        except Exception:
            return None

        if not raw.get("success"):
            return None

        records = raw.get("result", {}).get("data")
        if not records:
            return None

        item = records[0]

        return FundamentalData(
            symbol=info["code"],
            report_date=str(_safe_str(item.get("REPORT_DATE"))),
            revenue=_safe_float(item.get("OPERATE_INCOME")),
            revenue_yoy=_safe_float(item.get("OPERATE_INCOME_YOY")),
            net_profit=_safe_float(item.get("PARENT_HOLDER_NETPROFIT")),
            net_profit_yoy=None,  # datacenter 不直接提供净利润同比
            eps=_safe_float(item.get("BASIC_EPS")),
            roe=_safe_float(item.get("ROE_AVG")),
            gross_margin=_safe_float(item.get("GROSS_PROFIT_RATIO")),
            net_margin=_safe_float(item.get("NET_PROFIT_RATIO")),
            debt_ratio=_safe_float(item.get("DEBT_ASSET_RATIO")),
        )


def _safe_float(val) -> Optional[float]:
    """安全转型，空值/异常返回 None"""
    if val is None or val == "" or val == "-":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _safe_str(val) -> str:
    """安全转字符串"""
    if val is None:
        return ""
    return str(val)
