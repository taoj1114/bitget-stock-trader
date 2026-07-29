"""内存缓存 — TTL 自动过期"""

import time
from typing import Any, Optional


_cache: dict[str, tuple[float, Any]] = {}  # key → (expires_at, value)


class MemoryCache:
    """简单的 TTL 内存缓存。线程不安全，协程安全。"""

    def __init__(self, default_ttl: int = 300):
        self._default_ttl = default_ttl

    def get(self, key: str) -> Optional[Any]:
        """获取缓存。过期返回 None。"""
        entry = _cache.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at < time.time():
            del _cache[key]
            return None
        return value

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """设置缓存"""
        ttl = ttl or self._default_ttl
        _cache[key] = (time.time() + ttl, value)

    def delete(self, key: str) -> None:
        """删除"""
        _cache.pop(key, None)

    def clear(self) -> None:
        """清空"""
        _cache.clear()

    def clean_expired(self) -> int:
        """清理过期项，返回清理数"""
        now = time.time()
        expired = [k for k, (exp, _) in _cache.items() if exp < now]
        for k in expired:
            del _cache[k]
        return len(expired)

    def size(self) -> int:
        return len(_cache)


# 全局默认实例
default_cache = MemoryCache()
