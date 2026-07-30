#!/usr/bin/env python3
"""Bitget Stock Trader — AI 美股合约交易分析工具

用法:
    python main.py --help               显示帮助
    python main.py --symbols            列出可交易美股合约
    python main.py --quote AAPL         获取实时行情
    python main.py --klines AAPL 1h 100 获取K线
    python main.py --news AAPL          获取相关新闻
    python main.py --analyze AAPL       完整分析 (Phase 2)
    python main.py --scan               全品种扫描 (Phase 2)
    python main.py --server             启动 Web 服务 (Phase 4)

环境变量:
    PYTHONPATH=. 必须设置（或 pip install -e .）
"""

import asyncio
import json
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any


# ── JSON 序列化辅助 ────────────────────────────────────

class _DataclassEncoder(json.JSONEncoder):
    """处理 dataclass / datetime / bytes 等类型的 JSON 编码"""
    def default(self, obj):
        if is_dataclass(obj):
            return asdict(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="replace")
        return super().default(obj)


def _json_dump(obj: Any, pretty: bool = True) -> str:
    """序列化为 JSON 字符串"""
    indent = 2 if pretty else None
    return json.dumps(
        obj, cls=_DataclassEncoder, ensure_ascii=False, indent=indent
    )


# ── 时间戳格式化 ──────────────────────────────────────

def _fmt_kline(k: dict) -> dict:
    """格式化K线时间戳为可读 ISO"""
    ts = k.get("timestamp", 0)
    if isinstance(ts, (int, float)) and ts > 0:
        # 如果是毫秒时间戳
        if ts > 1e12:
            ts = ts / 1000
        try:
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            k = dict(k)
            k["timestamp_iso"] = dt.isoformat()
        except (ValueError, OSError):
            pass
    return k


# ── 区间格式转换（CLI 友好 → API 格式） ─────────────

def _normalize_interval(interval: str) -> str:
    """将 CLI 友好的 "1h" 转换为 Bitget API 格式 "1H"
    
    Bitget 规则: 大写 H/D/W/M, 小写 m
    """
    interval = interval.strip().upper()
    # 处理 "1H" → "1H" (已正确), "1M" → "1m" (分钟), "1D" → "1D" (天)
    if interval.endswith("M") and interval[:-1].isdigit():
        # 可能是分钟或月。Bitget: 分钟用小写 m，月用大写 M
        # 常见分钟值: 1,5,15,30 → 判断为分钟
        n = int(interval[:-1])
        if n <= 60:
            return f"{n}m"
        return interval  # 月
    return interval


# ── CLI 命令实现 ──────────────────────────────────────

async def cmd_symbols(config):
    """列出可交易美股合约（stock + online）"""
    from src.datasources.bitget.symbols import BitgetSymbolSource

    src = BitgetSymbolSource()
    try:
        stocks = await src.get_stock_symbols()
        result = {
            "total": len(stocks),
            "symbols": [
                {
                    "symbol": s.symbol,
                    "raw_symbol": s.raw_symbol,
                    "symbol_type": s.symbol_type,
                    "status": s.status,
                    "min_leverage": s.min_leverage,
                    "max_leverage": s.max_leverage,
                    "price_precision": s.price_precision,
                    "qty_precision": s.qty_precision,
                }
                for s in stocks
            ],
        }
        print(_json_dump(result))
    finally:
        await src.close()


async def cmd_quote(config, symbol: str):
    """获取实时行情"""
    from src.datasources.bitget.market import BitgetMarketSource

    src = BitgetMarketSource()
    try:
        quote = await src.get_quote(symbol)
        if quote is None:
            print(_json_dump({"error": f"无法获取 {symbol} 报价"}))
        else:
            print(_json_dump(quote))
    finally:
        await src.close()


async def cmd_klines(config, symbol: str, interval: str = "1h", limit: int = 100):
    """获取K线数据"""
    from src.datasources.bitget.market import BitgetMarketSource

    api_interval = _normalize_interval(interval)
    src = BitgetMarketSource()
    try:
        klines = await src.get_klines(symbol, interval=api_interval, limit=limit)
        if not klines:
            print(_json_dump({"error": f"无法获取 {symbol} K线"}))
        else:
            formatted = [_fmt_kline(asdict(k)) for k in klines]
            result = {
                "symbol": symbol,
                "interval": interval,
                "api_interval": api_interval,
                "count": len(formatted),
                "klines": formatted,
            }
            print(_json_dump(result))
    finally:
        await src.close()


async def cmd_news(config, symbol: str):
    """获取新闻 + 情绪分析 (多源聚合)"""
    from src.datasources.news.yahoo import YahooNewsSource
    from src.datasources.news.searxng import SearXNGNewsSource
    from src.datasources.news.registry import NewsRegistry
    from src.analyzers.sentiment import SentimentAnalyzer

    registry = NewsRegistry(primary_name="yahoo", fallback_name="searxng")
    registry.register(YahooNewsSource(timeout=10))
    registry.register(SearXNGNewsSource(
        base_url=config.searxng_base_url,
        timeout=config.searxng_timeout,
    ))
    try:
        news_items = await registry.fetch_news(symbol, max_results=config.searxng_max_results, merge_all=True)

        # 情绪分析
        sentiment = SentimentAnalyzer.score(news_items)

        result = {
            "symbol": symbol,
            "query": f"{symbol} stock",
            "news_count": len(news_items),
            "sentiment": sentiment,
            "news": [
                {
                    "title": item.title,
                    "snippet": item.snippet[:300] if item.snippet else "",
                    "url": item.url,
                    "source": item.source,
                    "published_at": item.published_at,
                }
                for item in news_items
            ],
        }
        print(_json_dump(result))
    finally:
        await src.close()


async def cmd_analyze(config, symbol: str):
    """完整分析 (Phase 2)"""
    print(_json_dump({"error": "analyze 命令在 Phase 2 中实现", "symbol": symbol}))


async def cmd_scan(config):
    """全品种扫描 (Phase 2)"""
    print(_json_dump({"error": "scan 命令在 Phase 2 中实现"}))


async def cmd_server(config):
    """启动 Web 服务 (Phase 4)"""
    print(_json_dump({"error": "server 命令在 Phase 4 中实现"}))


# ── 入口 ───────────────────────────────────────────────

def print_help():
    print(__doc__)


async def main():
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help", "help"):
        print_help()
        return

    # 加载配置
    from src.config.loader import get_config
    config = get_config()

    cmd = args[0]

    # -- 去掉前面的横线
    cmd_name = cmd.lstrip("-")

    if cmd_name == "symbols":
        await cmd_symbols(config)

    elif cmd_name == "quote" and len(args) >= 2:
        await cmd_quote(config, args[1])

    elif cmd_name == "klines" and len(args) >= 2:
        symbol = args[1]
        interval = args[2] if len(args) > 2 else "1h"
        limit = int(args[3]) if len(args) > 3 else 100
        await cmd_klines(config, symbol, interval, limit)

    elif cmd_name == "news" and len(args) >= 2:
        await cmd_news(config, args[1])

    elif cmd_name == "analyze" and len(args) >= 2:
        await cmd_analyze(config, args[1])

    elif cmd_name == "scan":
        await cmd_scan(config)

    elif cmd_name == "server":
        await cmd_server(config)

    else:
        print(f"未知命令: {cmd}")
        print_help()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
