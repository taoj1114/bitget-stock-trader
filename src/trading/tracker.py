"""交易记录器 — SQLite 持久化所有交易（开仓/平仓/止损/止盈）

用于绩效分析和策略评估。
"""

import sqlite3
import threading
from datetime import datetime, timezone
from typing import Optional


class Tracker:
    """交易记录 SQLite 存储。线程安全（写操作加锁）。"""

    def __init__(self, db_path: str = "data/trades.db", mode: str = "real"):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._mode = mode
        self._create_table()

    def _create_table(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT NOT NULL,
                symbol      TEXT NOT NULL,
                side        TEXT NOT NULL,
                type        TEXT NOT NULL,
                price       REAL NOT NULL,
                quantity    REAL NOT NULL,
                pnl         REAL,
                pnl_pct     REAL,
                reason      TEXT DEFAULT '',
                strategy_id TEXT DEFAULT '',
                spread      REAL DEFAULT 0.0,
                funding_cost REAL DEFAULT 0.0,
                holding_hours REAL DEFAULT 0.0,
                market_regime TEXT DEFAULT '',
                signal_score REAL DEFAULT 0.0,
                position_id  TEXT DEFAULT '',
                mode        TEXT DEFAULT 'real'
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol, timestamp)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy_id, timestamp)
        """)
        self._conn.commit()

    # ── 写入 ─────────────────────────────────────────

    def record_open(self, position, signal, spread: float = 0.0) -> None:
        """记录开仓。"""
        with self._lock:
            self._conn.execute("""
                INSERT INTO trades
                (timestamp, symbol, side, type, price, quantity, reason,
                 strategy_id, spread, position_id, signal_score, market_regime, mode)
                VALUES (?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                datetime.now(timezone.utc).isoformat(),
                position.symbol, position.side,
                position.entry_price, position.quantity,
                signal.reason, signal.strategy_id, spread,
                position.id, signal.confidence or 0,
                '', self._mode,
            ))
            self._conn.commit()

    def record_close(self, position, exit_price: float, pnl: float,
                     reason: str = "", strategy_id: str = "",
                     funding_cost: float = 0.0) -> None:
        """记录平仓。"""
        holding_hours = 0
        if position.opened_at:
            try:
                now = datetime.now(timezone.utc)
                opened = position.opened_at
                if opened.tzinfo is None:
                    opened = opened.replace(tzinfo=timezone.utc)
                holding_hours = (now - opened).total_seconds() / 3600
            except Exception:
                pass

        pnl_pct = (pnl / (position.entry_price * position.quantity)) * 100 if position.entry_price and position.quantity else 0

        with self._lock:
            self._conn.execute("""
                INSERT INTO trades
                (timestamp, symbol, side, type, price, quantity, pnl, pnl_pct,
                 reason, strategy_id, funding_cost, holding_hours, position_id, mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                datetime.now(timezone.utc).isoformat(),
                position.symbol, position.side,
                reason, exit_price, position.quantity,
                round(pnl, 2), round(pnl_pct, 2),
                reason, strategy_id or position.strategy_id,
                funding_cost, round(holding_hours, 2),
                position.id, self._mode,
            ))
            self._conn.commit()

    # ── 查询 ─────────────────────────────────────────

    def get_strategy_trades(self, strategy_id: str, limit: int = 100) -> list:
        rows = self._conn.execute(
            """SELECT * FROM trades WHERE strategy_id=? ORDER BY timestamp DESC LIMIT ?""",
            (strategy_id, limit),
        ).fetchall()
        return [_row_to_dict(r, self._col_names()) for r in rows]

    def get_open_trades(self, limit: int = 1000) -> list:
        rows = self._conn.execute(
            "SELECT * FROM trades WHERE type='OPEN' ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_dict(r, self._col_names()) for r in rows]

    def get_closed_trades(self, limit: int = 100) -> list:
        rows = self._conn.execute(
            "SELECT * FROM trades WHERE type!='OPEN' ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_dict(r, self._col_names()) for r in rows]

    def get_daily_pnl(self, date_str: str | None = None) -> float:
        if date_str is None:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = self._conn.execute(
            "SELECT SUM(pnl) FROM trades WHERE type!='OPEN' AND timestamp LIKE ?",
            (f"{date_str}%",),
        ).fetchone()
        return row[0] or 0.0

    def get_consecutive_losses(self) -> int:
        """最近连续亏损笔数。"""
        rows = self._conn.execute(
            "SELECT pnl FROM trades WHERE type!='OPEN' AND pnl IS NOT NULL ORDER BY timestamp DESC LIMIT 20"
        ).fetchall()
        count = 0
        for (pnl,) in rows:
            if pnl < 0:
                count += 1
            else:
                break
        return count

    def _col_names(self) -> list[str]:
        return [
            "id", "timestamp", "symbol", "side", "type", "price", "quantity",
            "pnl", "pnl_pct", "reason", "strategy_id", "spread", "funding_cost",
            "holding_hours", "market_regime", "signal_score", "position_id",
        ]

    def close(self):
        self._conn.close()


def _row_to_dict(row, cols) -> dict:
    return {c: row[i] for i, c in enumerate(cols)}
