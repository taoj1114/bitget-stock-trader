"""配置管理 — 加载 + 热更新

============================================================
TODO[Phase1]: 实现配置加载
============================================================

配置源:
    1. config.yaml (主配置)
    2. 环境变量覆盖 (STOCK_TRADER_CONFIG)
    3. 热更新: 检测文件 mtime 变更

config.yaml 结构:
    mode: paper
    symbols: [AAPL, NVDA, ...]
    datasources:
        bitget: {base_url, rate_limit}
        eastmoney: {mode}
    news_sources:
        primary: searxng
        fallback: null
        searxng: {base_url, max_results, timeout}
    cache: {memory_ttl, kline_ttl}
    api: {host, port}

接口:
    class Config:
        def __init__(self, path: str = "config.yaml")
        def load(self) -> None
        def reload_if_changed(self) -> bool
        def get(self, key: str, default=None) -> Any  # 点号路径: "datasources.bitget.base_url"
        # 便捷属性: mode, symbols, bitget_base_url, searxng_base_url, ...

单例:
    def get_config() -> Config    # 全局配置实例
    def hot_reload() -> bool      # 热重载
"""

from typing import Any, Optional


class Config:
    """配置管理器"""

    def __init__(self, path: str = "config.yaml"):
        raise NotImplementedError("TODO[Phase1]: 实现 Config.__init__()")

    def load(self) -> None:
        raise NotImplementedError("TODO[Phase1]: 实现 Config.load()")

    def reload_if_changed(self) -> bool:
        raise NotImplementedError("TODO[Phase1]: 实现 Config.reload_if_changed()")

    def get(self, key: str, default: Any = None) -> Any:
        raise NotImplementedError("TODO[Phase1]: 实现 Config.get()")


_config: Optional[Config] = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config


def hot_reload() -> bool:
    return get_config().reload_if_changed()
