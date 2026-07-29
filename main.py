#!/usr/bin/env python3
"""Bitget Stock Trader — AI 美股合约交易分析工具

============================================================
TODO[Phase1]: 实现 CLI 入口
============================================================

用法:
    python main.py --help               显示帮助
    python main.py --symbols            列出可交易美股合约
    python main.py --quote AAPL         获取实时行情
    python main.py --klines AAPL 1h 100 获取K线
    python main.py --news AAPL          获取相关新闻
    python main.py --analyze AAPL       完整分析 (Phase 2)
    python main.py --scan               全品种扫描 (Phase 2)
    python main.py --server             启动 Web 服务 (Phase 4)

初始化流程:
    1. 加载配置 (Config)
    2. 注册数据源 (DataSourceRegistry)
    3. 根据命令分发

命令实现参考:
    --symbols:  PSEUDOCODE.md 第3节 (BitgetSymbolSource)
    --quote:    PSEUDOCODE.md 第3节 (BitgetMarketSource.get_quote)
    --klines:   PSEUDOCODE.md 第3节 (BitgetMarketSource.get_klines)
    --news:     PSEUDOCODE.md 第5节 (NewsService)
    --analyze:  PHASE2_DESIGN.md (Scorer)

注意事项:
    - 所有输出用 JSON 格式
    - 异步命令用 asyncio.run()
    - 数据源初始化失败时给出清晰错误信息
"""

import asyncio
import sys


def print_help():
    print(__doc__)


async def main():
    # 先解析命令
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        print_help()
        return

    # 初始化 (TODO[Phase1]: 加载配置 + 注册数据源)
    print("TODO[Phase1]: 初始化数据源...")

    cmd = args[0].lstrip("-")

    if cmd == "quote" and len(args) >= 2:
        print(f"TODO[Phase1]: 获取 {args[1]} 实时行情")
    elif cmd == "klines" and len(args) >= 2:
        interval = args[2] if len(args) > 2 else "1h"
        print(f"TODO[Phase1]: 获取 {args[1]} K线 ({interval})")
    elif cmd == "symbols":
        print("TODO[Phase1]: 列出美股合约")
    elif cmd == "news" and len(args) >= 2:
        print(f"TODO[Phase1]: 获取 {args[1]} 新闻")
    elif cmd == "analyze" and len(args) >= 2:
        print(f"[Phase 2] 完整分析 {args[1]} 尚未实现")
    elif cmd == "scan":
        print("[Phase 2] 全品种扫描尚未实现")
    elif cmd == "server":
        print("[Phase 4] Web 服务尚未实现")
    else:
        print(f"未知命令: {cmd}")
        print_help()


if __name__ == "__main__":
    asyncio.run(main())
