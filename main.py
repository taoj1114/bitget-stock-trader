#!/usr/bin/env python3
"""Bitget Stock Trader — AI 美股合约交易分析工具

用法:
    python main.py --help                 显示帮助
    python main.py --quote AAPL           获取实时行情
    python main.py --klines AAPL 1h 100   获取K线
    python main.py --symbols              列出可交易美股合约
    python main.py --news AAPL            获取新闻
    python main.py --analyze AAPL         完整分析 (Phase 2)
    python main.py --scan                 全品种扫描 (Phase 2)
    python main.py --server               启动 Web 服务 (Phase 4)

环境变量:
    STOCK_TRADER_CONFIG  配置文件路径 (默认 config.yaml)
"""

import asyncio
import json
import sys
from datetime import datetime

from src.config.loader import get_config
from src.datasources.base import registry
from src.datasources.bitget.market import BitgetMarketSource
from src.datasources.bitget.symbols import BitgetSymbolSource
from src.datasources.eastmoney.search import EastmoneySearch
from src.datasources.news.searxng import SearXNGNewsSource
from src.datasources.news.registry import NewsRegistry


def _format_json(data):
    """格式化 JSON 输出"""
    return json.dumps(data, indent=2, ensure_ascii=False, default=str)


async def cmd_quote(symbol: str):
    """获取实时行情"""
    source = registry.get("BitgetMarketSource")
    if not source:
        print("错误: Bitget 数据源未注册")
        return
    quote = await source.get_quote(symbol.upper())
    if quote:
        print(_format_json({
            "symbol": quote.symbol,
            "price": quote.price,
            "change_24h": f"{quote.change_pct*100:.2f}%",
            "high_24h": quote.high_24h,
            "low_24h": quote.low_24h,
            "volume_24h": quote.volume_24h,
            "funding_rate": f"{quote.funding_rate*100:.4f}%",
            "open_interest": quote.open_interest,
            "index_price": quote.index_price,
            "mark_price": quote.mark_price,
            "bid": quote.bid,
            "ask": quote.ask,
            "timestamp": datetime.fromtimestamp(quote.timestamp / 1000).isoformat(),
        }))
    else:
        print(f"未获取到 {symbol} 的行情数据")


async def cmd_klines(symbol: str, interval: str = "1h", limit: int = 100):
    """获取K线"""
    source = registry.get("BitgetMarketSource")
    if not source:
        print("错误: Bitget 数据源未注册")
        return
    klines = await source.get_klines(symbol.upper(), interval, limit)
    print(_format_json([
        {
            "timestamp": datetime.fromtimestamp(k.timestamp / 1000).isoformat(),
            "open": k.open,
            "high": k.high,
            "low": k.low,
            "close": k.close,
            "volume": k.volume,
            "turnover": k.turnover,
        }
        for k in klines
    ]))


async def cmd_symbols():
    """列出美股合约"""
    source = registry.get("BitgetMarketSource")
    if not source:
        print("错误: Bitget 数据源未注册")
        return
    sym_source = BitgetSymbolSource(source)
    symbols = await sym_source.get_stock_symbols()
    print(f"可交易美股合约: {len(symbols)} 个\n")
    print(_format_json([
        {
            "symbol": s.symbol,
            "leverage": f"{s.min_leverage}x-{s.max_leverage}x",
            "precision": f"{s.price_precision}.{s.qty_precision}",
            "funding_interval": f"{s.fund_interval}h",
        }
        for s in symbols
    ]))


async def cmd_news(symbol: str):
    """获取相关新闻"""
    news_reg = getattr(cmd_news, "_registry", None)
    if not news_reg:
        print("错误: 新闻源未注册")
        return
    items = await news_reg.fetch_news(symbol)
    print(_format_json([
        {
            "title": n.title,
            "snippet": n.snippet[:200] + "..." if len(n.snippet) > 200 else n.snippet,
            "source": n.source,
            "url": n.url,
        }
        for n in items
    ]))


async def cmd_analyze(symbol: str):
    """完整分析 (Phase 2)"""
    print(f"[Phase 2] 完整分析 {symbol} 尚未实现")
    print("请等待 Phase 2: 分析引擎 + 策略系统")


async def cmd_scan():
    """全品种扫描 (Phase 2)"""
    print("[Phase 2] 全品种扫描尚未实现")
    print("请等待 Phase 2: 分析引擎 + 策略系统")


async def cmd_server():
    """启动 Web 服务 (Phase 4)"""
    print("[Phase 4] Web 服务尚未实现")
    print("请等待 Phase 4: 参数进化 + Web API")


def print_help():
    print(__doc__)


async def main():
    # ===== 先解析命令，help 不需要初始化 =====
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        print_help()
        return

    config = get_config()

    # ===== 初始化数据源 =====
    bitget = BitgetMarketSource(
        base_url=config.bitget_base_url,
        rate_limit=config.bitget_rate_limit,
    )
    registry.register(bitget)

    # 初始化新闻源
    news_reg = NewsRegistry(
        primary_name=config.get("news_sources.primary", "searxng"),
        fallback_name=config.get("news_sources.fallback"),
    )
    searxng = SearXNGNewsSource(
        base_url=config.searxng_base_url,
        max_results=config.searxng_max_results,
    )
    news_reg.register(searxng)
    cmd_news._registry = news_reg  # 注入到命令函数

    print(f"✅ {registry.summary()}")
    print()

    cmd = args[0].lstrip("-")

    if cmd == "quote" and len(args) >= 2:
        await cmd_quote(args[1])
    elif cmd == "klines" and len(args) >= 2:
        interval = args[2] if len(args) > 2 else "1h"
        limit = int(args[3]) if len(args) > 3 else 100
        await cmd_klines(args[1], interval, limit)
    elif cmd == "symbols":
        await cmd_symbols()
    elif cmd == "news" and len(args) >= 2:
        await cmd_news(args[1])
    elif cmd == "analyze" and len(args) >= 2:
        await cmd_analyze(args[1])
    elif cmd == "scan":
        await cmd_scan()
    elif cmd == "server":
        await cmd_server()
    else:
        print(f"未知命令: {cmd}")
        print_help()


if __name__ == "__main__":
    asyncio.run(main())
