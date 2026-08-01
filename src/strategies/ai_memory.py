"""AI 自优化 — 决策日志 + 经验复盘 + 经验注入

流程:
  1. 每次 AI 决策 → 记日志 (symbol, action, reason, session, 指标, 平仓PnL)
  2. 定期触发复盘 → AI 读最近决策+结果 → 输出经验教训 (JSON)
  3. 经验注入下次分析的 prompt

存储: data/ai_memory.json
  {"decisions": [...], "lessons": [...], "last_review": "..."}
"""

import json, logging, os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

MEMORY_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "ai_memory.json")

MAX_DECISIONS = 500   # 保留最近决策
MAX_LESSONS = 20      # 经验条数上限


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class AIMemory:
    """AI 决策记忆 + 经验。"""

    def __init__(self, path: str = MEMORY_PATH):
        self._path = path
        self._data = self._load()

    def _load(self) -> dict:
        try:
            with open(self._path) as f:
                d = json.load(f)
            d.setdefault("decisions", [])
            d.setdefault("lessons", [])
            d.setdefault("last_review", "")
            return d
        except Exception:
            return {"decisions": [], "lessons": [], "last_review": ""}

    def _save(self) -> None:
        try:
            with open(self._path, "w") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=1)
        except Exception as e:
            logger.warning("保存记忆失败: %s", e)

    # ═══ 决策日志 ═══════════════════════════

    def log_decision(self, symbol: str, action: str, reason: str,
                     session: str, mark_price: float, rsi_1h: float,
                     adx: float, regime: str, entry: float = 0.0) -> None:
        """记录一次 AI 决策（开仓或 HOLD）。"""
        d = {
            "time": _now(), "symbol": symbol, "action": action,
            "reason": reason[:300], "session": session,
            "mark_price": round(mark_price, 2),
            "rsi_1h": round(rsi_1h, 1), "adx": round(adx, 1),
            "regime": regime, "entry": round(entry, 2),
            "close_pnl": None, "close_price": None, "outcome": None,
        }
        self._data["decisions"].append(d)
        # 限制数量
        if len(self._data["decisions"]) > MAX_DECISIONS:
            self._data["decisions"] = self._data["decisions"][-MAX_DECISIONS:]
        self._save()

    def close_decision(self, symbol: str, close_price: float, pnl: float) -> None:
        """平仓时回填最近一次未平仓决策的结果。"""
        for d in reversed(self._data["decisions"]):
            if d["symbol"] == symbol and d["close_pnl"] is None:
                d["close_pnl"] = round(pnl, 2)
                d["close_price"] = round(close_price, 2)
                d["outcome"] = "win" if pnl > 0 else ("loss" if pnl < 0 else "flat")
                break
        self._save()

    # ═══ 经验 ═══════════════════════════════

    def get_lessons(self, limit: int = 5) -> list[str]:
        """取最近经验，注入 prompt。"""
        return self._data["lessons"][-limit:]

    def set_lessons(self, lessons: list[str]) -> None:
        """写入 AI 复盘得出的经验。"""
        self._data["lessons"] = lessons[-MAX_LESSONS:]
        self._data["last_review"] = _now()
        self._save()

    def should_review(self, hours: int = 12) -> bool:
        """距离上次复盘是否超过 hours 小时。"""
        if not self._data["last_review"]:
            return True
        try:
            last = datetime.strptime(self._data["last_review"], "%Y-%m-%d %H:%M:%S")
            last = last.replace(tzinfo=timezone.utc)
            delta = datetime.now(timezone.utc) - last
            return delta.total_seconds() >= hours * 3600
        except Exception:
            return True

    def recent_decisions(self, limit: int = 30) -> list[dict]:
        """取最近决策（含结果），供复盘。"""
        return self._data["decisions"][-limit:]

    def get_symbol_history(self, symbol: str, limit: int = 3) -> list[dict]:
        """品种记忆: 该股票最近的 AI 判断记录。"""
        hist = [d for d in self._data["decisions"] if d["symbol"] == symbol]
        return hist[-limit:]

    def stats(self) -> dict:
        """决策统计：胜率、按时段/regime/方向分组。"""
        decisions = [d for d in self._data["decisions"] if d["outcome"] is not None]
        stats = {"total": len(decisions), "win": 0, "loss": 0, "flat": 0,
                 "by_session": {}, "by_regime": {}, "by_action": {}}
        for d in decisions:
            o = d["outcome"]
            stats[o] = stats.get(o, 0) + 1
            stats["by_session"].setdefault(d["session"], [0, 0, 0])  # win/loss/flat
            stats["by_regime"].setdefault(d["regime"], [0, 0, 0])
            stats["by_action"].setdefault(d["action"], [0, 0, 0])
            for group in ("by_session", "by_regime", "by_action"):
                key = d["session"] if group == "by_session" else (
                    d["regime"] if group == "by_regime" else d["action"])
                bucket = stats[group][key]
                bucket[0 if o == "win" else 1 if o == "loss" else 2] += 1
        return stats
