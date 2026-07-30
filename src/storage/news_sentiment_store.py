"""新闻情绪历史存储 — SQLite 每日情绪快照

每天每个品种一条记录，记录正/负/中性新闻数量和平均情绪分。
"""

import sqlite3
from datetime import date
from typing import Optional

from src.core.sanitize import clean_float


class NewsSentimentStore:
    """每日新闻情绪快照存储。"""

    def __init__(self, db_path: str = "data/news_sentiment.db"):
        self._conn = sqlite3.connect(db_path)
        self._create_table()

    def _create_table(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS news_sentiment (
                symbol              TEXT NOT NULL,
                date                TEXT NOT NULL,
                positive_count      INTEGER DEFAULT 0,
                negative_count      INTEGER DEFAULT 0,
                neutral_count       INTEGER DEFAULT 0,
                total_count         INTEGER DEFAULT 0,
                avg_sentiment_score REAL,
                positive_ratio      REAL,
                negative_ratio      REAL,
                fetched_at          INTEGER DEFAULT (strftime('%s','now')),
                UNIQUE(symbol, date)
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_news_symbol_date
            ON news_sentiment(symbol, date DESC)
        """)
        self._conn.commit()

    def snapshot(self, symbol: str, sentiment: dict) -> None:
        """写入当日情绪快照。清洗 NaN/Inf。"""
        today = date.today().isoformat()
        total = max(0, int(sentiment.get("count", 0)))
        pos_ratio = clean_float(sentiment.get("positive"), default=0, allow_negative=False)
        neg_ratio = clean_float(sentiment.get("negative"), default=0, allow_negative=False)
        overall = clean_float(sentiment.get("overall"))

        pos_cnt = max(0, int(pos_ratio * total)) if total else 0
        neg_cnt = max(0, int(neg_ratio * total)) if total else 0
        neu_cnt = max(0, total - pos_cnt - neg_cnt)

        with self._conn:
            self._conn.execute("""
                INSERT OR REPLACE INTO news_sentiment
                (symbol, date, positive_count, negative_count, neutral_count,
                 total_count, avg_sentiment_score, positive_ratio, negative_ratio)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                symbol, today,
                pos_cnt, neg_cnt, neu_cnt,
                total, overall,
                pos_ratio, neg_ratio,
            ))

    def get_history(self, symbol: str, days: int = 7) -> list[dict]:
        """最近 N 天情绪数据（按日期降序）。"""
        rows = self._conn.execute(
            """SELECT date, positive_count, negative_count, neutral_count,
                      total_count, avg_sentiment_score, positive_ratio, negative_ratio
               FROM news_sentiment
               WHERE symbol=?
               ORDER BY date DESC
               LIMIT ?""",
            (symbol, days),
        ).fetchall()

        cols = [
            "date", "positive_count", "negative_count", "neutral_count",
            "total_count", "avg_sentiment_score", "positive_ratio", "negative_ratio",
        ]
        return [dict(zip(cols, r)) for r in rows]

    def get_today(self, symbol: str) -> Optional[dict]:
        """今日情绪快照。"""
        today = date.today().isoformat()
        rows = self._conn.execute(
            """SELECT date, positive_count, negative_count, neutral_count,
                      total_count, avg_sentiment_score, positive_ratio, negative_ratio
               FROM news_sentiment
               WHERE symbol=? AND date=?""",
            (symbol, today),
        ).fetchall()
        if rows:
            cols = [
                "date", "positive_count", "negative_count", "neutral_count",
                "total_count", "avg_sentiment_score", "positive_ratio", "negative_ratio",
            ]
            return dict(zip(cols, rows[0]))
        return None

    def close(self):
        self._conn.close()
