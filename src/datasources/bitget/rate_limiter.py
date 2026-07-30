"""Bitget 限速控制 — 基于时间间隔的异步限速器"""

import asyncio
import time
import logging

logger = logging.getLogger(__name__)


class RateLimiter:
    """异步限速器。基于令牌桶的简化版：按最小间隔控制请求频率。

    用法:
        limiter = RateLimiter(max_rate=20)  # 20 req/s
        async with limiter:
            await api_call()
        # 或手动: await limiter.acquire()
    """

    def __init__(self, max_rate: float = 20):
        """
        Args:
            max_rate: 每秒最大请求数（默认 20，匹配 Bitget 限速）
        """
        self._min_interval = 1.0 / max_rate
        self._last_request = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """等待直到可以发送下一个请求。"""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request
            if elapsed < self._min_interval:
                wait = self._min_interval - elapsed
                logger.debug("RateLimiter: waiting %.3fs", wait)
                await asyncio.sleep(wait)
                self._last_request = time.monotonic()
            else:
                self._last_request = now

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, *args):
        pass


# 模块级单例，供 market.py 和 symbols.py 共享
_rate_limiter = RateLimiter(max_rate=20)
