"""Eastmoney 基本面数据 — 财务指标

============================================================
TODO[Phase1]: 实现 Eastmoney Datacenter API 获取基本面
============================================================

API 端点:
    GET https://datacenter-web.eastmoney.com/api/data/v1/get
        params: reportName=RPT_USF10_FN_GMAININDICATOR
                filter=(SECUCODE="{secucode}")
                columns=ALL, pageSize=1, pageNumber=1
                sortColumns=REPORT_DATE, sortTypes=-1
        → OPERATE_INCOME, OPERATE_INCOME_YOY, PARENT_HOLDER_NETPROFIT,
          BASIC_EPS, ROE_AVG, GROSS_PROFIT_RATIO, NET_PROFIT_RATIO, DEBT_ASSET_RATIO

接口:
    class EastmoneyFundamentalSource:
        async def get_fundamentals(self, symbol: str) -> Optional[FundamentalData]

参考:
    - 数据模型: src/core/types.py → FundamentalData
    - 伪代码: PSEUDOCODE.md 第4节
    - 需要先通过 search.py 获取 secucode

注意事项:
    - 限速 1 req/s
    - 返回最新一期财报
    - 营收/利润单位是元
    - 百分比字段是原始值 (如 20.5 = 20.5%)
"""

from typing import Optional
from src.core.types import FundamentalData


class EastmoneyFundamentalSource:
    """Eastmoney 基本面数据源"""

    async def get_fundamentals(self, symbol: str) -> Optional[FundamentalData]:
        raise NotImplementedError("TODO[Phase1]: 实现 EastmoneyFundamentalSource.get_fundamentals()")
