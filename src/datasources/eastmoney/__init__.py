"""Eastmoney 数据源包 — 共享限速器"""

import asyncio
import time

_last_call: float = 0.0
"""上次 API 调用时间戳 (time.monotonic)"""


def _rate_limit_sync():
    """同步限速：确保距上次调用 >= 1s"""
    global _last_call
    elapsed = time.monotonic() - _last_call
    if elapsed < 1.0:
        time.sleep(1.0 - elapsed)
    _last_call = time.monotonic()


async def _rate_limit_async():
    """异步限速：确保距上次调用 >= 1s（使用 asyncio.sleep）"""
    global _last_call
    elapsed = time.monotonic() - _last_call
    if elapsed < 1.0:
        await asyncio.sleep(1.0 - elapsed)
    _last_call = time.monotonic()
