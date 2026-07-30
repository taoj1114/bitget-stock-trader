"""基本面历史存储 — SQLite 多季度财报数据

支持按 report_date 存储和查询，用于计算基本面动量（营收增速变化等）。
"""

import sqlite3
from typing import Optional

from src.core.sanitize import clean_float


class FundStore:
    """基本面数据持久化，按 report_date 存储多季度数据。"""

    def __init__(self, db_path: str = "data/fundamentals.db"):
        self._conn = sqlite3.connect(db_path)
        self._create_table()

    def _create_table(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS fundamentals (
                symbol       TEXT NOT NULL,
                report_date  TEXT NOT NULL,
                revenue      REAL,
                revenue_yoy  REAL,
                net_profit   REAL,
                net_profit_yoy REAL,
                eps          REAL,
                roe          REAL,
                gross_margin REAL,
                net_margin   REAL,
                debt_ratio   REAL,
                fetched_at   INTEGER DEFAULT (strftime('%s','now')),
                UNIQUE(symbol, report_date)
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_fund_symbol_date
            ON fundamentals(symbol, report_date DESC)
        """)
        self._conn.commit()

    def upsert(self, symbol: str, data: dict) -> None:
        """写入/更新单期基本面数据。清洗 NaN/Inf → NULL。"""
        def c(key, allow_neg=True):
            return clean_float(data.get(key), default=0.0, allow_negative=allow_neg) if data.get(key) is not None else None

        with self._conn:
            self._conn.execute("""
                INSERT OR REPLACE INTO fundamentals
                (symbol, report_date, revenue, revenue_yoy, net_profit,
                 net_profit_yoy, eps, roe, gross_margin, net_margin, debt_ratio)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                symbol,
                str(data.get("report_date", "")),
                c("revenue"), c("revenue_yoy"),
                c("net_profit"), c("net_profit_yoy"),
                c("eps"), c("roe"),
                c("gross_margin"), c("net_margin"),
                c("debt_ratio"),
            ))

    def get_history(self, symbol: str, limit: int = 4) -> list[dict]:
        """获取最近 N 期财报（按 report_date 降序）。"""
        rows = self._conn.execute(
            """SELECT report_date, revenue, revenue_yoy, net_profit,
                      net_profit_yoy, eps, roe, gross_margin, net_margin, debt_ratio
               FROM fundamentals
               WHERE symbol=?
               ORDER BY report_date DESC
               LIMIT ?""",
            (symbol, limit),
        ).fetchall()

        cols = [
            "report_date", "revenue", "revenue_yoy", "net_profit",
            "net_profit_yoy", "eps", "roe", "gross_margin", "net_margin", "debt_ratio",
        ]
        return [dict(zip(cols, r)) for r in rows]

    def get_latest(self, symbol: str) -> Optional[dict]:
        """最新一期财报数据。"""
        history = self.get_history(symbol, limit=1)
        return history[0] if history else None

    def close(self):
        self._conn.close()
