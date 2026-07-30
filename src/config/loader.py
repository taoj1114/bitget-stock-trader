"""配置管理 — 加载 + 热更新

配置源:
    1. config.yaml (主配置)
    2. 环境变量覆盖 (STOCK_TRADER_CONFIG)
    3. 热更新: 检测文件 mtime 变更

用法:
    config = get_config()
    base_url = config.get("datasources.bitget.base_url")
    symbols = config.symbols
"""

import os
import time
from pathlib import Path
from typing import Any, Optional

import yaml

from src.core.exceptions import ConfigurationError


class Config:
    """配置管理器 — 支持 YAML 加载和热重载"""

    def __init__(self, path: str = "config.yaml"):
        self._path = Path(path)
        self._data: dict = {}
        self._mtime: float = 0.0
        self._loaded = False

    # ── 加载 ──────────────────────────────────────────

    def load(self) -> None:
        """加载 config.yaml，失败抛出 ConfigurationError"""
        if not self._path.exists():
            raise ConfigurationError(f"配置文件不存在: {self._path}")

        try:
            with open(self._path, "r", encoding="utf-8") as f:
                self._data = yaml.safe_load(f) or {}
            self._mtime = self._path.stat().st_mtime
            self._loaded = True
        except yaml.YAMLError as e:
            raise ConfigurationError(f"YAML 解析失败: {e}") from e

        # 环境变量覆盖: STOCK_TRADER_CONFIG 指向另一个配置文件
        env_config = os.environ.get("STOCK_TRADER_CONFIG")
        if env_config:
            env_path = Path(env_config)
            if env_path.exists():
                try:
                    with open(env_path, "r", encoding="utf-8") as f:
                        env_data = yaml.safe_load(f) or {}
                    self._deep_merge(self._data, env_data)
                except Exception:
                    pass  # 环境变量覆盖失败不影响主配置

    def reload_if_changed(self) -> bool:
        """检测文件 mtime 变化，自动重载。返回是否发生了重载。"""
        if not self._path.exists():
            return False
        current_mtime = self._path.stat().st_mtime
        if current_mtime != self._mtime:
            self.load()
            return True
        return False

    # ── 取值 ──────────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        """点号路径取值，如 "datasources.bitget.base_url"

        支持嵌套字典和列表索引。
        """
        if not self._loaded:
            self.load()

        parts = key.split(".")
        node = self._data
        for part in parts:
            if isinstance(node, dict):
                node = node.get(part)
                if node is None:
                    return default
            elif isinstance(node, list):
                try:
                    idx = int(part)
                    node = node[idx]
                except (ValueError, IndexError):
                    return default
            else:
                return default
        return node

    # ── 便捷属性 ──────────────────────────────────────

    @property
    def mode(self) -> str:
        return self.get("mode", "paper")

    @property
    def symbols(self) -> list[str]:
        return self.get("symbols", [])

    @property
    def datasources(self) -> dict:
        return self.get("datasources", {})

    @property
    def news_sources(self) -> dict:
        return self.get("news_sources", {})

    @property
    def strategies(self) -> list[dict]:
        return self.get("strategies", [])

    @property
    def safety(self) -> dict:
        return self.get("safety", {})

    @property
    def api(self) -> dict:
        return self.get("api", {})

    @property
    def deepseek(self) -> dict:
        return self.get("deepseek", {})

    @property
    def cache(self) -> dict:
        return self.get("cache", {})

    @property
    def slippage(self) -> dict:
        return self.get("slippage", {})

    @property
    def cold_start(self) -> dict:
        return self.get("cold_start", {})

    @property
    def optimization(self) -> dict:
        return self.get("optimization", {})

    # ── Bitget 便捷属性 ───────────────────────────────

    @property
    def bitget_base_url(self) -> str:
        return self.get("datasources.bitget.base_url", "https://api.bitget.com")

    @property
    def bitget_rate_limit(self) -> int:
        return self.get("datasources.bitget.rate_limit", 20)

    # ── SearXNG 便捷属性 ──────────────────────────────

    @property
    def searxng_base_url(self) -> str:
        return self.get("news_sources.searxng.base_url", "http://localhost:8080")

    @property
    def searxng_timeout(self) -> int:
        return self.get("news_sources.searxng.timeout", 10)

    @property
    def searxng_max_results(self) -> int:
        return self.get("news_sources.searxng.max_results", 15)

    # ── 工具方法 ──────────────────────────────────────

    def to_dict(self) -> dict:
        """返回完整配置字典"""
        if not self._loaded:
            self.load()
        return dict(self._data)

    @staticmethod
    def _deep_merge(base: dict, override: dict) -> None:
        """递归合并 override 到 base"""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                Config._deep_merge(base[key], value)
            else:
                base[key] = value


# ── 单例 ──────────────────────────────────────────────

_config: Optional[Config] = None


def get_config(path: str = "config.yaml") -> Config:
    """获取全局配置实例（延迟初始化）"""
    global _config
    if _config is None:
        _config = Config(path)
        _config.load()
    return _config


def hot_reload() -> bool:
    """热重载配置"""
    cfg = get_config()
    return cfg.reload_if_changed()
