"""持久化缓存 — SQLite K线存储

============================================================
TODO[Phase1]: 实现 SQLite K线持久化缓存
============================================================

目的:
    - 避免重复拉取历史K线
    - 支持增量更新（仅补齐最新数据）
    - 离线时使用缓存数据

表结构:
    CREATE TABLE kline_cache (
        symbol TEXT,
        interval TEXT,
        timestamp INTEGER,
        open REAL, high REAL, low REAL, close REAL,
        volume REAL, turnover REAL,
        PRIMARY KEY (symbol, interval, timestamp)
    );

接口:
    class KlineCache:
        def get_klines(self, symbol, interval, limit) -> list[Kline]
        def save_klines(self, symbol, interval, klines: list[Kline])
        def get_latest_timestamp(self, symbol, interval) -> int

参考:
    - data/kline_cache/ 目录已创建
"""

# TODO[Phase1]: 实现 KlineCache 类
