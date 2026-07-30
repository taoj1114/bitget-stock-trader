"""参数版本管理 — 追踪参数变更，支持自动回滚"""

import json
import sqlite3
import time
from typing import Any, Optional

from src.core.sanitize import safe_json_dumps


class VersionManager:
    """策略参数版本追踪 + 自动回滚。"""

    def __init__(self, db_path: str = "data/param_versions.db"):
        self._conn = sqlite3.connect(db_path)
        self._create_table()

    def _create_table(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS param_versions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id TEXT NOT NULL,
                params      TEXT NOT NULL,
                performance REAL,
                saved_at    INTEGER NOT NULL,
                active      INTEGER DEFAULT 0
            )
        """)
        self._conn.commit()

    def save(self, strategy_id: str, params: Any, performance: float = 0) -> int:
        """保存参数快照，返回版本 ID。"""
        params_dict = {}
        if hasattr(params, "__dataclass_fields__"):
            from dataclasses import asdict
            params_dict = asdict(params)
        elif isinstance(params, dict):
            params_dict = params

        self._conn.execute(
            "UPDATE param_versions SET active=0 WHERE strategy_id=? AND active=1",
            (strategy_id,),
        )
        cursor = self._conn.execute(
            "INSERT INTO param_versions (strategy_id, params, performance, saved_at, active) VALUES (?, ?, ?, ?, 1)",
            (strategy_id, safe_json_dumps(params_dict), performance, int(time.time())),
        )
        self._conn.commit()
        return cursor.lastrowid

    def get_best(self, strategy_id: str) -> Optional[dict]:
        """获取历史最优参数。"""
        row = self._conn.execute(
            "SELECT params FROM param_versions WHERE strategy_id=? AND performance IS NOT NULL ORDER BY performance DESC LIMIT 1",
            (strategy_id,),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def rollback_to_best(self, strategy_id: str) -> Optional[dict]:
        """回滚到历史最优参数。"""
        best = self.get_best(strategy_id)
        if best:
            self._conn.execute(
                "UPDATE param_versions SET active=0 WHERE strategy_id=?",
                (strategy_id,),
            )
            self.save(strategy_id, best, 0)
        return best

    def close(self):
        self._conn.close()
