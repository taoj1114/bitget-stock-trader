"""K线持久化存储 — SQLite 单周期存储 + 多周期合成

只存 1H 原始数据。4H/1D/1W 通过 pandas resample 按需合成。
"""

import sqlite3
import time


from src.core.sanitize import validate_kline


class KlineStore:
    """SQLite 持久化 K线存储。只存 1H 原始数据。"""

    def __init__(self, db_path: str = "data/klines.db"):
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_table()

    def _create_table(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS klines (
                symbol     TEXT NOT NULL,
                interval   TEXT NOT NULL DEFAULT '1H',
                timestamp  INTEGER NOT NULL,
                open       REAL NOT NULL,
                high       REAL NOT NULL,
                low        REAL NOT NULL,
                close      REAL NOT NULL,
                volume     REAL NOT NULL,
                turnover   REAL NOT NULL DEFAULT 0.0,
                UNIQUE(symbol, interval, timestamp)
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_klines_ts
            ON klines(symbol, interval, timestamp)
        """)
        self._conn.commit()

    # ── 写入 ──────────────────────────────────────────

    def upsert_batch(self, symbol: str, klines: list[dict], interval: str = "1H") -> int:
        """INSERT OR IGNORE 批量写入。返回新增条数。

        每条 kline dict 需含: timestamp, open, high, low, close, volume[, turnover]
        写入前校验：拒绝无效 K 线。
        """
        inserted = 0
        with self._conn:
            for k in klines:
                clean = validate_kline(k)
                if clean is None:
                    continue
                cursor = self._conn.execute(
                    """INSERT OR IGNORE INTO klines
                       (symbol, interval, timestamp, open, high, low, close, volume, turnover)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        symbol, interval,
                        clean["timestamp"],
                        clean["open"], clean["high"], clean["low"], clean["close"],
                        clean["volume"], clean.get("turnover", 0.0),
                    ),
                )
                if cursor.rowcount > 0:
                    inserted += 1
        return inserted

    def prune(self, keep_days: int = 30) -> int:
        """清理过期 K线, 只保留最近 N 天。返回删除行数。"""
        cutoff = int(time.time() * 1000) - keep_days * 86400 * 1000
        with self._conn:
            cur = self._conn.execute(
                "DELETE FROM klines WHERE timestamp < ?", (cutoff,))
            return cur.rowcount

    # ── 读取 ──────────────────────────────────────────

    def get(self, symbol: str, interval: str = "1H", limit: int = 200) -> list[dict]:
        """取 K线，时间升序。

        Args:
            symbol: 品种名（如 AAPL）
            interval: 周期。1H 直接读，其他周期从 1H 合成。
            limit: 最大条数
        """
        if interval == "1H":
            return self._get_1h(symbol, limit)
        else:
            return self._get_aggregated(symbol, interval, limit)

    def _get_1h(self, symbol: str, limit: int) -> list[dict]:
        rows = self._conn.execute(
            """SELECT timestamp, open, high, low, close, volume, turnover
               FROM klines
               WHERE symbol=? AND interval='1H'
               ORDER BY timestamp ASC
               LIMIT ?""",
            (symbol, limit * 2),  # overshoot, then tail
        ).fetchall()

        # Take last `limit` rows
        if len(rows) > limit:
            rows = rows[-limit:]

        return [
            {
                "timestamp": r[0], "open": r[1], "high": r[2],
                "low": r[3], "close": r[4], "volume": r[5], "turnover": r[6],
            }
            for r in rows
        ]

    def _get_aggregated(self, symbol: str, interval: str, limit: int) -> list[dict]:
        """从 1H 合成目标周期。"""
        from src.storage.kline_aggregator import KlineAggregator

        rows_1h = self.get(symbol, "1H", limit=limit * 50)  # enough raw bars
        if not rows_1h:
            return []

        agg = KlineAggregator()
        result = agg.aggregate(rows_1h, interval)
        return result[-limit:] if len(result) > limit else result

    def get_range(self, symbol: str, start_ts: int, end_ts: int) -> list[dict]:
        """按时间范围取 1H K线（用于因子计算）。"""
        rows = self._conn.execute(
            """SELECT timestamp, open, high, low, close, volume, turnover
               FROM klines
               WHERE symbol=? AND interval='1H'
                 AND timestamp BETWEEN ? AND ?
               ORDER BY timestamp ASC""",
            (symbol, start_ts, end_ts),
        ).fetchall()
        return [
            {
                "timestamp": r[0], "open": r[1], "high": r[2],
                "low": r[3], "close": r[4], "volume": r[5], "turnover": r[6],
            }
            for r in rows
        ]

    def get_latest_ts(self, symbol: str) -> int:
        """最新 K线时间戳（用于增量判断）。"""
        row = self._conn.execute(
            "SELECT MAX(timestamp) FROM klines WHERE symbol=? AND interval='1H'",
            (symbol,),
        ).fetchone()
        return row[0] or 0

    def symbol_count(self) -> int:
        """存储中的品种数量。"""
        row = self._conn.execute(
            "SELECT COUNT(DISTINCT symbol) FROM klines"
        ).fetchone()
        return row[0] or 0

    def close(self):
        self._conn.close()
