"""扩展数据源 — yfinance 额外数据

免费获取:
    - 分析师评级 (共识 + 目标价)
    - 机构持仓 (Top holders + 变动)
    - 上/下行潜力 (targetMeanPrice vs current)

用法:
    from src.datasources.extended import fetch_extended_data
    data = await fetch_extended_data("AAPL")
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ExtendedData:
    """扩展数据（可选字段均可为 None）"""
    symbol: str = ""

    # 分析师
    analyst_count: int = 0
    buy_count: int = 0
    hold_count: int = 0
    sell_count: int = 0
    consensus: str = ""               # buy/hold/sell
    target_high: float = 0.0
    target_low: float = 0.0
    target_mean: float = 0.0
    upside_pct: float = 0.0           # 上行空间 %

    # 机构
    top_holders: list[dict] = field(default_factory=list)  # [{name, shares, value, change_pct}]
    inst_net_change: float = 0.0      # 机构持仓净变动 (近似)

    # 财报
    next_earnings: str = ""           # 下次财报日期

    error: str = ""


async def fetch_extended_data(symbol: str) -> ExtendedData:
    """异步获取扩展数据。"""
    result = ExtendedData(symbol=symbol)

    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol.upper())
        info = ticker.info or {}

        # ── 分析师 ──
        result.analyst_count = int(info.get("numberOfAnalystOpinions", 0))
        result.target_high = float(info.get("targetHighPrice", 0) or 0)
        result.target_low = float(info.get("targetLowPrice", 0) or 0)
        result.target_mean = float(info.get("targetMeanPrice", 0) or 0)
        result.consensus = str(info.get("recommendationKey", ""))

        current = float(info.get("currentPrice", 0) or info.get("regularMarketPrice", 0) or 0)
        if current > 0 and result.target_mean > 0:
            result.upside_pct = (result.target_mean / current - 1) * 100

        # ── 分析师详细分布 ──
        try:
            recs = ticker.recommendations
            if recs is not None and len(recs) > 0:
                latest = recs.iloc[0] if hasattr(recs, 'iloc') else recs[-1]
                result.buy_count = int(latest.get("strongBuy", 0)) + int(latest.get("buy", 0))
                result.hold_count = int(latest.get("hold", 0))
                result.sell_count = int(latest.get("sell", 0)) + int(latest.get("strongSell", 0))
        except Exception:
            pass

        # ── 机构持仓 ──
        try:
            holders = ticker.institutional_holders
            if holders is not None and len(holders) > 0:
                for _, row in holders.head(5).iterrows():
                    result.top_holders.append({
                        "name": str(row.get("Holder", "")),
                        "shares": int(row.get("Shares", 0) or 0),
                        "value": int(row.get("Value", 0) or 0),
                        "change_pct": float(row.get("pctChange", 0) or 0) * 100,
                    })
                # 净变动
                if "pctChange" in holders.columns:
                    result.inst_net_change = float(holders["pctChange"].sum() or 0) * 100
        except Exception:
            pass

        # ── 财报日期 ──
        try:
            ed = ticker.earnings_dates
            if ed is not None and len(ed) > 0:
                import pandas as pd
                future = ed[ed.index > pd.Timestamp.now()]
                if len(future) > 0:
                    result.next_earnings = str(future.index[0].date())
        except Exception:
            pass

    except Exception as e:
        result.error = str(e)
        logger.warning("扩展数据获取失败 %s: %s", symbol, e)

    return result


def format_for_prompt(data: ExtendedData) -> str:
    """格式化为 AI Prompt 友好的文本。"""
    if not data or data.analyst_count == 0:
        return ""

    lines = []
    lines.append("## 📊 分析师 & 机构数据")

    if data.analyst_count > 0:
        consensus_emoji = {"buy": "🟢", "hold": "🟡", "sell": "🔴"}.get(data.consensus, "")
        lines.append(f"分析师: {data.analyst_count}人 | {consensus_emoji}{data.consensus}")
        lines.append(f"评级分布: 买入{data.buy_count} 持有{data.hold_count} 卖出{data.sell_count}")

    if data.target_mean > 0:
        direction = "📈" if data.upside_pct > 10 else ("📉" if data.upside_pct < -10 else "➡️")
        lines.append(f"目标价: \${data.target_mean:.0f} (高\${data.target_high:.0f}/低\${data.target_low:.0f})")
        lines.append(f"上行空间: {direction} {data.upside_pct:+.1f}%")

    if data.top_holders:
        lines.append(f"机构持仓 Top 3:")
        for h in data.top_holders[:3]:
            chg = h["change_pct"]
            chg_str = f"{chg:+.1f}%" if abs(chg) > 0.1 else ""
            lines.append(f"  {h['name']}: \${h['value']/1e9:.1f}B {chg_str}")

    if data.inst_net_change != 0:
        direction = "增持" if data.inst_net_change > 0 else "减持"
        lines.append(f"机构净变动: {direction} {data.inst_net_change:+.1f}%")

    if data.next_earnings:
        lines.append(f"下次财报: {data.next_earnings}")

    return "\n".join(lines)
