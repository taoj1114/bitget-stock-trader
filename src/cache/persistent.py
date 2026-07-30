"""持久化缓存 — SQLite K线存储

目的:
    - 避免重复拉取历史K线
    - 支持增量更新（仅补齐最新数据）
    - 离线时使用缓存数据

用法:
    cache = KlineCache("data/kline_cache.db")
    existing = cache.get_klines("AAPL", "1h", limit=50)
    cache.save_klines("AAPL", "1h", new_klines)
    latest_ts = cache.get_latest_ts("AAPL", "1h")
"""

import json
import os
import sqlite3
from pathlib import Path
from typing import Optional


class KlineCache:
    """SQLite K线持久化缓存"""

    def __init__(self, db_path: str = "data/kline_cache.db"):
        # 确保目录存在
        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    # ── 连接管理 ───────────────────────────────────────

    def _ensure_conn(self) -> sqlite3.Connection:
        """确保连接存在并初始化表"""
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._init_table()
        return self._conn

    def _init_table(self) -> None:
        """创建表"""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS kline_cache (
                symbol TEXT NOT NULL,
                interval TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                turnover REAL,
                PRIMARY KEY (symbol, interval, timestamp)
            )
        """)
        # 索引加速查询
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_kline_lookup
            ON kline_cache(symbol, interval, timestamp)
        """)
        self._conn.commit()

    # ── 查询 ──────────────────────────────────────────

    def get_klines(
        self, symbol: str, interval: str = "1h", limit: int = 100
    ) -> list[dict]:
        """获取K线数据，返回时间升序的字典列表。

        Args:
            symbol: 交易对 (AAPL, NVDA...)
            interval: K线周期 (1m, 5m, 15m, 30m, 1h, 4h, 1D...)
            limit: 返回条数

        Returns:
            list[dict]: 每条包含 timestamp, open, high, low, close, volume, turnover
        """
        conn = self._ensure_conn()
        # 子查询先按时间倒序取 limit 条，外查询再升序排列
        rows = conn.execute(
            """
            SELECT timestamp, open, high, low, close, volume, turnover
            FROM (
                SELECT * FROM kline_cache
                WHERE symbol = ? AND interval = ?
                ORDER BY timestamp DESC
                LIMIT ?
            )
            ORDER BY timestamp ASC
            """,
            (symbol, interval, limit),
        ).fetchall()

        return [
            {
                "timestamp": r[0],
                "open": r[1],
                "high": r[2],
                "low": r[3],
                "close": r[4],
                "volume": r[5],
                "turnover": r[6],
            }
            for r in rows
        ]

    def save_klines(
        self, symbol: str, interval: str, klines: list[dict]
    ) -> int:
        """批量写入K线（INSERT OR IGNORE 跳过重复）。

        Args:
            symbol: 交易对
            interval: K线周期
            klines: K线字典列表，每项需含 timestamp, open, high, low, close
                    可选的 volume, turnover

        Returns:
            int: 实际插入的条数
        """
        if not klines:
            return 0

        conn = self._ensure_conn()
        inserted = 0
        for k in klines:
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO kline_cache
                        (symbol, interval, timestamp, open, high, low, close, volume, turnover)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        symbol,
                        interval,
                        int(k["timestamp"]),
                        float(k.get("open", 0)),
                        float(k.get("high", 0)),
                        float(k.get("low", 0)),
                        float(k.get("close", 0)),
                        float(k.get("volume", 0)),
                        float(k.get("turnover", 0)),
                    ),
                )
                if conn.total_changes > 0:
                    inserted += 1
            except (ValueError, KeyError, TypeError):
                continue

        conn.commit()
        return inserted

    def get_latest_ts(self, symbol: str, interval: str = "1h") -> int:
        """获取该 symbol+interval 的最新时间戳。

        Returns:
            int: 最新时间戳 (Unix ms)，无数据返回 0
        """
        conn = self._ensure_conn()
        row = conn.execute(
            """
            SELECT MAX(timestamp) FROM kline_cache
            WHERE symbol = ? AND interval = ?
            """,
            (symbol, interval),
        ).fetchone()
        return row[0] if row[0] is not None else 0

    def delete_old(self, symbol: str, interval: str, before_ts: int) -> int:
        """删除指定时间之前的旧数据"""
        conn = self._ensure_conn()
        cursor = conn.execute(
            """
            DELETE FROM kline_cache
            WHERE symbol = ? AND interval = ? AND timestamp < ?
            """,
            (symbol, interval, before_ts),
        )
        conn.commit()
        return cursor.rowcount

    def close(self) -> None:
        """关闭数据库连接"""
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def __del__(self):
        self.close()
