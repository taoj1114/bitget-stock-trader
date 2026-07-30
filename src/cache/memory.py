"""内存缓存 — TTL 自动过期

接口:
    class MemoryCache:
        def __init__(self, default_ttl: int = 300)
        def get(self, key: str) -> Optional[Any]
        def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None
        def delete(self, key: str) -> None
        def clear(self) -> None
        def clean_expired(self) -> int

用途:
    - 减少重复 API 调用
    - Quote 缓存 60s
    - 合约列表缓存 300s
    - K线数据缓存 60s
"""

import time
from typing import Any, Optional


class MemoryCache:
    """简单的 TTL 内存缓存"""

    def __init__(self, default_ttl: int = 300):
        self._default_ttl = default_ttl
        self._store: dict = {}
        self._expires: dict = {}

    def get(self, key: str) -> Optional[Any]:
        """获取缓存。过期返回 None。"""
        if key not in self._store:
            return None
        if key in self._expires and self._expires[key] < time.time():
            del self._store[key]
            del self._expires[key]
            return None
        return self._store[key]

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """设置缓存"""
        ttl = ttl or self._default_ttl
        self._store[key] = value
        self._expires[key] = time.time() + ttl

    def delete(self, key: str) -> None:
        """删除"""
        self._store.pop(key, None)
        self._expires.pop(key, None)

    def clear(self) -> None:
        """清空"""
        self._store.clear()
        self._expires.clear()

    def clean_expired(self) -> int:
        """清理过期项，返回清理数"""
        now = time.time()
        expired = [k for k, exp in self._expires.items() if exp < now]
        for k in expired:
            del self._store[k]
            del self._expires[k]
        return len(expired)

    def size(self) -> int:
        return len(self._store)


# 全局默认实例 (延迟初始化，避免 import 时抛出)
_default_cache: Optional[MemoryCache] = None


def get_default_cache() -> MemoryCache:
    """获取全局缓存实例"""
    global _default_cache
    if _default_cache is None:
        _default_cache = MemoryCache()
    return _default_cache
