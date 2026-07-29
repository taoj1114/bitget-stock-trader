"""配置管理 — 加载 + 热更新"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import yaml


class Config:
    """配置管理器。支持 YAML 文件 + 环境变量覆盖。"""

    def __init__(self, path: str = "config.yaml"):
        self._path = Path(path)
        self._data: dict[str, Any] = {}
        self._mtime: float = 0
        self.load()

    def load(self) -> None:
        """加载配置"""
        if not self._path.exists():
            raise FileNotFoundError(f"配置文件不存在: {self._path}")
        with open(self._path) as f:
            self._data = yaml.safe_load(f) or {}
        self._mtime = self._path.stat().st_mtime

    def reload_if_changed(self) -> bool:
        """如果文件变更则重新加载。返回是否重载。"""
        try:
            mtime = self._path.stat().st_mtime
            if mtime > self._mtime:
                self.load()
                return True
        except OSError:
            pass
        return False

    def get(self, key: str, default: Any = None) -> Any:
        """通过点号路径取值: config.get('datasources.bitget.base_url')"""
        keys = key.split(".")
        val = self._data
        for k in keys:
            if isinstance(val, dict):
                val = val.get(k)
            else:
                return default
        return val if val is not None else default

    @property
    def raw(self) -> dict:
        return self._data

    # ==== 便捷访问属性 ====

    @property
    def mode(self) -> str:
        return self.get("mode", "paper")

    @property
    def symbols(self) -> list[str]:
        return self.get("symbols", [])

    @property
    def bitget_base_url(self) -> str:
        return self.get("datasources.bitget.base_url", "https://api.bitget.com")

    @property
    def bitget_rate_limit(self) -> int:
        return self.get("datasources.bitget.rate_limit", 20)

    @property
    def eastmoney_mode(self) -> str:
        return self.get("datasources.eastmoney.mode", "auto")

    @property
    def searxng_base_url(self) -> str:
        return self.get("news_sources.searxng.base_url", "http://localhost:8080")

    @property
    def searxng_max_results(self) -> int:
        return self.get("news_sources.searxng.max_results", 15)

    @property
    def memory_ttl(self) -> int:
        return self.get("cache.memory_ttl", 300)

    @property
    def api_host(self) -> str:
        return self.get("api.host", "0.0.0.0")

    @property
    def api_port(self) -> int:
        return self.get("api.port", 8000)


# 全局单例
_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        # 支持自定义路径
        path = os.environ.get("STOCK_TRADER_CONFIG", "config.yaml")
        _config = Config(path)
    return _config


def hot_reload() -> bool:
    """热重载配置。返回是否变更。"""
    return get_config().reload_if_changed()
