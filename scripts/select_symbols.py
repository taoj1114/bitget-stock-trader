#!/usr/bin/env python3
"""按热度筛选交易品种 — 基于 Bitget 实时数据

根据 24h 成交量、涨跌幅度、持仓量综合评分，
选出最活跃的 N 个美股合约。

用法:
    PYTHONPATH=. python3 scripts/select_symbols.py --top 20
    PYTHONPATH=. python3 scripts/select_symbols.py --top 30 --update-config
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.datasources.bitget.market import BitgetMarketSource
from src.datasources.bitget.symbols import BitgetSymbolSource


async def fetch_all_stock_quotes():
    """获取所有美股合约的行情数据。"""
    symbol_src = BitgetSymbolSource()
    market = BitgetMarketSource()

    stocks = await symbol_src.get_stock_symbols()
    print(f"Bitget 美股合约总数: {len(stocks)}")

    quotes = []
    for i, s in enumerate(stocks):
        try:
            q = await market.get_quote(s.symbol)
            if q and q.mark_price > 0:
                quotes.append({
                    "symbol": s.symbol,
                    "price": q.mark_price,
                    "volume_24h": q.volume_24h,
                    "turnover_24h": q.turnover_24h,
                    "change_pct": abs(q.change_pct),
                    "open_interest": q.open_interest,
                })
        except Exception:
            pass

        if (i + 1) % 20 == 0:
            print(f"  已获取 {i+1}/{len(stocks)}...")

    await market.close()
    await symbol_src.close()
    return quotes


def rank_symbols(quotes: list[dict], top_n: int = 20) -> list[str]:
    """综合评分排名: 成交量(30%) + 涨跌幅度(25%) + 成交额(25%) + 持仓量(20%)

    过滤条件:
        - 价格 > $5 (排除仙股)
        - 24h 成交量 > 1000 (排除无流动性品种)
    """
    # 过滤
    filtered = [
        q for q in quotes
        if q["price"] > 5 and q["volume_24h"] > 1000
    ]
    print(f"过滤后: {len(filtered)} 个 (价格>$5, 成交量>1000)")

    if not filtered:
        return []

    max_vol = max(q["volume_24h"] for q in filtered) or 1
    max_to = max(q["turnover_24h"] for q in filtered) or 1
    max_chg = max(q["change_pct"] for q in filtered) or 0.001
    max_oi = max(q["open_interest"] for q in filtered) or 1

    scored = []
    for q in filtered:
        score = (
            (q["volume_24h"] / max_vol) * 0.30 +
            (q["change_pct"] / max_chg) * 0.25 +
            (q["turnover_24h"] / max_to) * 0.25 +
            (q["open_interest"] / max_oi) * 0.20
        )
        scored.append((q["symbol"], score, q))

    scored.sort(key=lambda x: x[1], reverse=True)
    return [s[0] for s in scored[:top_n]]


async def main():
    top_n = 20
    update_config = False
    args = sys.argv[1:]

    for i, a in enumerate(args):
        if a == "--top" and i + 1 < len(args):
            top_n = int(args[i + 1])
        if a == "--update-config":
            update_config = True

    print(f"正在获取 Bitget 美股合约行情...")
    quotes = await fetch_all_stock_quotes()
    print(f"获取完成: {len(quotes)} 个有效报价")

    ranked = rank_symbols(quotes, top_n)

    print(f"\n=== 热度 Top {top_n} ===")
    for i, sym in enumerate(ranked):
        q = next(q for q in quotes if q["symbol"] == sym)
        print(f"  {i+1:2d}. {sym:5s}  vol={q['volume_24h']:>8.0f}  chg={q['change_pct']:>6.1%}  OI={q['open_interest']:>8.0f}  ${q['price']:.1f}")

    if update_config:
        update_config_yaml(ranked)


def update_config_yaml(symbols: list[str]):
    """更新 config.yaml 中的 symbols 列表。"""
    import yaml
    config_path = "config.yaml"

    with open(config_path) as f:
        config = yaml.safe_load(f)

    config["symbols"] = symbols

    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    print(f"\n✅ config.yaml 已更新为 {len(symbols)} 个品种")


if __name__ == "__main__":
    asyncio.run(main())
