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

logger = logging.getLogger(__name__)

MEMORY_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "ai_memory.json")

MAX_DECISIONS = 500   # 保留最近决策
MAX_LESSONS = 20      # 经验条数上限
MAX_RULES = 10        # 硬规则上限


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class AIMemory:
    """AI 决策记忆 + 经验 + 硬规则。"""

    def __init__(self, path: str = MEMORY_PATH):
        self._path = path
        self._data = self._load()

    def _load(self) -> dict:
        try:
            with open(self._path) as f:
                d = json.load(f)
            d.setdefault("decisions", [])
            d.setdefault("lessons", [])
            d.setdefault("rules", [])
            d.setdefault("last_review", "")
            return d
        except Exception:
            return {"decisions": [], "lessons": [], "rules": [], "last_review": ""}

    def _save(self) -> None:
        try:
            with open(self._path, "w") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=1)
        except Exception as e:
            logger.warning("保存记忆失败: %s", e)

    # ═══ 决策日志 ═══════════════════════════

    def log_decision(self, symbol: str, action: str, reason: str,
                     session: str, mark_price: float, rsi_1h: float,
                     adx: float, regime: str, entry: float = 0.0,
                     sl_price: float = 0.0, tp_price: float = 0.0) -> None:
        """记录一次 AI 决策。

        HOLD 与开仓分库: HOLD 每天数千条, 若与开仓混存会把
        真实交易样本挤出 MAX_DECISIONS, 导致平仓无法回填、
        复盘无样本可学。→ 真实开仓永远保留, HOLD 只留最近记录。
        """
        sl_pct = None
        tp_pct = None
        if entry > 0:
            if sl_price > 0:
                sl_pct = round(abs(sl_price - entry) / entry * 100, 1)
            if tp_price > 0:
                tp_pct = round(abs(tp_price - entry) / entry * 100, 1)
        d = {
            "time": _now(), "symbol": symbol, "action": action,
            "reason": reason[:300], "session": session,
            "mark_price": round(mark_price, 2),
            "rsi_1h": round(rsi_1h, 1), "adx": round(adx, 1),
            "regime": regime, "entry": round(entry, 2),
            "sl_pct": sl_pct, "tp_pct": tp_pct,
            "close_pnl": None, "close_price": None, "outcome": None,
            "close_reason": None, "holding_hours": None,
        }
        if action in ("BUY", "SELL", "STRONG_BUY", "STRONG_SELL"):
            # 开仓决策: 真实交易样本, 永不溢出
            self._data["decisions"].append(d)
            if len(self._data["decisions"]) > MAX_DECISIONS:
                self._data["decisions"] = self._data["decisions"][-MAX_DECISIONS:]
        else:
            # HOLD: 只保留最近 200 条 (审计用, 不参与复盘)
            self._data.setdefault("holds", []).append(d)
            if len(self._data["holds"]) > 200:
                self._data["holds"] = self._data["holds"][-200:]
        self._save()

    def recent_holds(self, limit: int = 50) -> list[dict]:
        """最近 HOLD 决策 (审计用, 不注入复盘)。"""
        return list(self._data.get("holds", []))[-limit:]

    def count_closed(self) -> int:
        """已平仓(有结果)的决策数 — 复盘触发基准。"""
        return sum(1 for d in self._data["decisions"] if d.get("outcome") is not None)

    def get_review_base(self) -> int:
        """持久化的复盘基准 (上次复盘时的已平仓数, 重启不重置)。"""
        return int(self._data.get("review_base", self.count_closed()))

    def set_review_base(self, count: int) -> None:
        """记录复盘基准 (已平仓数), 持久化到磁盘。"""
        self._data["review_base"] = int(count)
        self._save()

    def close_decision(self, symbol: str, close_price: float, pnl: float,
                       close_reason: str = "", holding_hours: float = 0,
                       max_pnl_pct: float = 0.0) -> None:
        """平仓时回填最近一次未平仓决策的结果。

        max_pnl_pct: 持仓期间最大浮盈% (T+N 评估: 判定方向是否正确 follow-through)
          - direction_ok=True: 方向对但没吃到 (止损紧/离场早)
          - direction_ok=False: 方向错 (failed breakout)
        """
        for d in reversed(self._data["decisions"]):
            if d["symbol"] == symbol and d["close_pnl"] is None:
                d["close_pnl"] = round(pnl, 2)
                d["close_price"] = round(close_price, 2)
                d["outcome"] = "win" if pnl > 0 else ("loss" if pnl < 0 else "flat")
                d["close_reason"] = close_reason
                d["holding_hours"] = round(holding_hours, 1)
                # T+N 方向评估: 持仓期间最大浮盈≥0.5% = 方向曾正确
                d["max_pnl_pct"] = round(max_pnl_pct, 2)
                d["direction_ok"] = max_pnl_pct >= 0.5
                break
        self._save()

    # ═══ 硬规则 (失败模式) ═════════════════

    def get_rules(self, limit: int = 10) -> list[str]:
        """取硬规则, 注入 prompt (带'禁止'强约束)。"""
        return self._data["rules"][-limit:]

    def set_rules(self, rules: list[str]) -> None:
        self._data["rules"] = rules[-MAX_RULES:]
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

    # ═══ 研究委员会 (AlphaEvo 思路: 确定性分析师, 不依赖LLM) ═══

    def committee_diagnosis(self) -> list[str]:
        """确定性委员会诊断: 技术/风险/过拟合/方向正确率。

        返回诊断结论列表 (供复盘 prompt 注入, 让 AI 基于事实改进而非猜测)。
        """
        decisions = [d for d in self._data["decisions"] if d["outcome"] is not None]
        if len(decisions) < 3:
            return []
        findings = []

        # 分析师1: 技术分析师 (胜率/盈亏比/样本量)
        wins = [d for d in decisions if d["outcome"] == "win"]
        losses = [d for d in decisions if d["outcome"] == "loss"]
        wr = len(wins) / len(decisions) * 100 if decisions else 0
        avg_win = sum(d.get("close_pnl", 0) for d in wins) / len(wins) if wins else 0
        avg_loss = sum(d.get("close_pnl", 0) for d in losses) / len(losses) if losses else 0
        payoff = abs(avg_win / avg_loss) if avg_loss else 0
        if len(decisions) < 8:
            findings.append(f"技术: 样本仅{len(decisions)}笔, 结论置信度低 (≥8笔才可靠)")
        elif wr < 40 and payoff < 1.0:
            findings.append(f"技术: 胜率{wr:.0f}%+盈亏比{payoff:.2f}双低 → 入场和离场都需改进")
        elif wr < 40:
            findings.append(f"技术: 胜率{wr:.0f}%偏低, 但盈亏比{payoff:.2f}可接受 → 侧重入场质量")
        elif payoff < 1.0:
            findings.append(f"技术: 盈亏比{payoff:.2f}<1 (赢{avg_win:+.2f} 亏{avg_loss:+.2f}) → 侧重止盈止损结构")
        else:
            findings.append(f"技术: 胜率{wr:.0f}%+盈亏比{payoff:.2f}健康, 保持当前结构")

        # 分析师2: 风险分析师 (最大连亏/单笔最大亏)
        max_streak = 0
        cur = 0
        for d in decisions:
            if d["outcome"] == "loss":
                cur += 1
                max_streak = max(max_streak, cur)
            else:
                cur = 0
        max_loss = min((d.get("close_pnl", 0) for d in decisions), default=0)
        if max_streak >= 4:
            findings.append(f"风险: 最大连亏{max_streak}笔 → 注意单日连续亏损时的仓位/暂停")
        if max_loss < -0.1:
            findings.append(f"风险: 单笔最大亏${max_loss:.2f} → 检查止损距离是否过大")

        # 分析师3: 方向正确率 (T+N: direction_ok 判定)
        d_ok = [d for d in decisions if d.get("direction_ok") is True]
        d_bad = [d for d in decisions if d.get("direction_ok") is False]
        if d_ok or d_bad:
            ok_rate = len(d_ok) / (len(d_ok) + len(d_bad)) * 100
            if ok_rate < 40 and len(d_ok) + len(d_bad) >= 5:
                findings.append(f"方向: 方向正确率仅{ok_rate:.0f}% → 入场时机/方向判断是主问题")
            elif ok_rate >= 60 and len(d_ok) + len(d_bad) >= 5:
                findings.append(f"方向: 方向正确率{ok_rate:.0f}% 但亏损 → 止损距离/持仓管理是主问题")

        # 分析师4: 过拟合检查 (规则/经验是否过度泛化)
        if len(decisions) >= 5:
            session_wins = {}
            for d in decisions:
                s = d.get("session", "?")
                session_wins.setdefault(s, [0, 0])
                session_wins[s][0] += 1
                session_wins[s][1] += 1 if d["outcome"] == "win" else 0
            for s, (n, w) in session_wins.items():
                if n >= 3 and w / n < 0.2:
                    findings.append(f"过拟合: 时段[{s}]胜率{w}/{n}极低 → 该时段应重点审查或禁开")
        return findings
