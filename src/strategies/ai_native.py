"""AI 原生决策器 — 无规则策略，纯 AI 驱动

流程: Flash快速判断 → BUY/SELL通过 / HOLD跳过 → Pro深度分析 → 最终决策
数据: 500x1H + 4H/1D + 大盘 + 新闻 + OI/费率
"""

import asyncio, json, logging, os, re
from datetime import datetime, date
from dataclasses import dataclass, field
from typing import Optional

import httpx

from src.core.types import Signal

logger = logging.getLogger(__name__)


def get_us_session(now=None) -> str:
    """判断当前美股交易时段（美东标准时间，自动处理夏令时）。

    美股标准交易时间 (America/New_York, ET):
      pre_market  盘前  04:00 - 09:30
      regular     盘中  09:30 - 16:00  ← 正常交易
      post_market 盘后  16:00 - 20:00
      closed      深夜  20:00 - 04:00
      weekend     周末  休市
    """
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    now = now or datetime.now(timezone.utc)
    et = now.astimezone(ZoneInfo("America/New_York"))
    if et.weekday() >= 5:
        return "weekend"
    t = et.hour * 60 + et.minute
    if 9 * 60 + 30 <= t < 16 * 60:
        return "regular"
    if 4 * 60 <= t < 9 * 60 + 30:
        return "pre_market"
    if 16 * 60 <= t < 20 * 60:
        return "post_market"
    return "closed"

@dataclass
class AIInput:
    symbol: str
    mark_price: float
    change_pct: float
    klines_1h: list
    klines_4h: list
    klines_1d: list
    ind_1h: dict
    ind_4h: dict | None
    ind_1d: dict | None
    news: list[str]
    news_summary: str
    bench: dict
    open_interest: float
    funding_rate: float
    volume_24h: float = 0.0
    ind_15m: dict | None = None  # 日内主指标
    session: str = "regular"  # pre_market / regular / post_market / weekend / holiday
    lessons: list[str] = field(default_factory=list)  # 复盘经验
    rules: list[str] = field(default_factory=list)    # 硬规则 (禁止项)
    history: list[dict] = field(default_factory=list)  # 品种记忆: 该股近期AI判断
    position_ctx: dict | None = None  # 持仓上下文 (管仓时)


class AINativeDecisionMaker:
    """AI 原生决策器。Flash 过滤 → Pro 决策。"""

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        self._base_url = "https://api.deepseek.com"

    async def _call(self, system: str, prompt: str, model: str,
                    temp: float, max_tokens: int, json_mode: bool = False) -> str:
        if not self._api_key:
            logger.warning("API key 未配置")
            return ""
        if not self._client:
            self._client = httpx.AsyncClient(timeout=45)
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self._api_key}"}
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": temp,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            resp = await self._client.post(f"{self._base_url}/chat/completions", headers=headers, json=payload)
            data = resp.json()
            if resp.status_code != 200:
                logger.warning("API %d: %s", resp.status_code, str(data)[:200])
                return ""
            choices = data.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content", "")
                if not content:
                    logger.warning("API empty content: %s", str(data)[:200])
                return content
        except Exception as e:
            logger.warning("API exception: %s", e)
        return ""

    async def decide(self, inp: AIInput) -> Optional[Signal]:
        """纯 AI 决策：Pro 直接分析全部数据。"""
        pro = await self._pro_deep_analyze(inp, flash_direction="AI")
        if not pro:
            return None
        pro_action = pro.get("action", "HOLD")
        if pro_action == "HOLD":
            return None
        return self._to_signal(inp, pro, pro_action)

    # ═══ Flash ═══════════════════════

    async def _flash_assess(self, inp: AIInput) -> Optional[dict]:
        """Flash 快速判断方向。"""
        bench_str = " ".join(f"{k}{v:+.1f}%" for k, v in inp.bench.items())
        prompt = (
            f"{inp.symbol} ${inp.mark_price:.2f} RSI={inp.ind_1h.get('rsi',50):.0f} "
            f"ADX={inp.ind_1h.get('adx',0):.0f} {inp.ind_1h.get('regime','')} "
            f"大盘{bench_str} "
            f"→ BUY SELL HOLD?"
        )
        raw = await self._call(
            system="仅回复 BUY SELL 或 HOLD。",
            prompt=prompt, model="deepseek-v4-flash",
            temp=0.0, max_tokens=500, json_mode=False,
        )
        raw = raw.strip().upper()
        if raw in ("BUY", "SELL", "HOLD"):
            return {"action": raw}
        return self._parse_json(raw) if "{" in raw else None

    # ═══ Pro ═══════════════════════

    async def _pro_deep_analyze(self, inp: AIInput, flash_direction: str) -> Optional[dict]:
        """Pro 深度分析，定 SL/TP。"""
        bench_str = " ".join(f"{k}{v:+.1f}%" for k, v in inp.bench.items())
        lessons = inp.lessons or []
        lesson_str = ""
        if lessons:
            lesson_str = "\n历史经验(复盘总结):\n" + "\n".join(f"  • {l}" for l in lessons) + "\n"

        rules = inp.rules or []
        rules_str = ""
        if rules:
            rules_str = "\n⛔ 硬规则(必须遵守):\n" + "\n".join(f"  ⛔ {r}" for r in rules) + "\n"

        # 品种记忆: 该股近期 AI 判断 (只注入有结果的, 避免锚定效应)
        hist_str = ""
        if inp.history:
            lines = []
            decided = [h for h in inp.history if h.get("outcome") is not None]
            for h in (decided or [])[-3:]:
                pnl = h.get("close_pnl")
                pnl_str = f" pnl={pnl}" if pnl is not None else ""
                lines.append(
                    f"  {h.get('time','')[:16]} {h.get('action','?')} "
                    f"[{h.get('session','')}] → {h.get('outcome','')}{pnl_str} | {h.get('reason','')[:50]}")
            if lines:
                hist_str = "\n该股历史交易结果(已平仓, 供参考):\n" + "\n".join(lines) + "\n"

        prompt = (
            f"分析 {inp.symbol}，给出交易决策。\n"
            f"时段: {inp.session}\n"
            f"${inp.mark_price:.2f} ({inp.change_pct:+.1f}%)\n"
            + (f"持仓: {inp.position_ctx.get('side','')} 开仓价=${inp.position_ctx.get('entry',0):.2f} "
               f"持仓{inp.position_ctx.get('hours',0):.0f}小时 浮盈=${inp.position_ctx.get('pnl',0):.2f} "
               f"当前SL=${inp.position_ctx.get('sl',0):.2f} TP=${inp.position_ctx.get('tp',0):.2f}\n"
               if inp.position_ctx else "")
            + "\n"
            + (f"15m RSI={inp.ind_15m.get('rsi',50):.0f} MA10={inp.ind_15m.get('ma10',0):.2f} MA30={inp.ind_15m.get('ma30',0):.2f} "
               f"MACD={inp.ind_15m.get('macd',0):.4f} ATR={inp.ind_15m.get('atr',0):.2f} "
               f"ADX={inp.ind_15m.get('adx',0):.0f} {inp.ind_15m.get('regime','')} "
               f"VWAP={inp.ind_15m.get('vwap',0):.2f} 量比={inp.ind_15m.get('volume_ratio',1):.1f} BB={inp.ind_15m.get('bb_position',0.5):.2f} [日内]\n" if inp.ind_15m else "")
            + f"1H RSI={inp.ind_1h.get('rsi',50):.0f} MA10={inp.ind_1h.get('ma10',0):.1f} MA30={inp.ind_1h.get('ma30',0):.1f} "
            f"MACD={inp.ind_1h.get('macd',0):.3f} ATR={inp.ind_1h.get('atr',0):.2f} "
            f"ADX={inp.ind_1h.get('adx',0):.0f} {inp.ind_1h.get('regime','')} "
            f"VWAP={inp.ind_1h.get('vwap',0):.2f} 量比={inp.ind_1h.get('volume_ratio',1):.1f} BB={inp.ind_1h.get('bb_position',0.5):.2f}\n"
            + (f"4H RSI={inp.ind_4h.get('rsi',0):.0f} MA10={inp.ind_4h.get('ma10',0):.1f} MA30={inp.ind_4h.get('ma30',0):.1f}\n" if inp.ind_4h else "")
            + (f"1D RSI={inp.ind_1d.get('rsi',0):.0f} MA10={inp.ind_1d.get('ma10',0):.1f} MA30={inp.ind_1d.get('ma30',0):.1f}\n" if inp.ind_1d else "")
            + f"大盘 {bench_str}\n"
            f"OI={inp.open_interest:.0f} 费率={inp.funding_rate*100:.4f}%\n"
            f"新闻 {inp.news_summary or '无'}\n"
            + hist_str
            + rules_str
            + lesson_str
            + "\n输出JSON:\n"
            '{"action":"BUY/SELL/HOLD","stop_loss":x,"take_profit":x,"reason":"..."}\n'
            "HOLD是合法的。不确定就HOLD。\n"
            "历史结果仅供参考——行情会反转, 必须以当前数据为准, 绝不因旧判断而固执。\n"
            "时段策略: regular(盘中)正常交易; pre_market盘前/post_market盘后/closed深夜流动性差,"
            "除非信号极强否则HOLD; weekend周末休市必HOLD。"
        )
        raw = await self._call(
            system="你是美股交易分析师。输出JSON，不要思考，直接给结果。",
            prompt=prompt, model="deepseek-v4-flash",
            temp=0.3, max_tokens=4000, json_mode=False,
        )
        return self._parse_json(raw)

    # ═══ 自我复盘 ═══════════════════════

    async def review_and_learn(self, memory) -> tuple[list[str], list[str]]:
        """AI 读历史决策+结果 → (经验教训, 硬规则)。"""
        decisions = memory.recent_decisions(30)
        if len(decisions) < 5:
            return [], []
        stats = memory.stats()
        lines = []
        # 只显示有结果的决策 (win/loss/flat), 排除纯 HOLD 噪音
        decided = [d for d in decisions if d.get("outcome") is not None]
        for d in (decided or decisions)[-15:]:
            lines.append(
                f"{d['time'][:16]} {d['symbol']} {d['action']} "
                f"RSI={d['rsi_1h']} ADX={d['adx']} {d['regime']} "
                f"[{d['session']}] → {d['outcome'] or 'open'} pnl={d['close_pnl']} "
                f"SL%={d.get('sl_pct')} TP%={d.get('tp_pct')} "
                f"平因={d.get('close_reason') or '-'} | {d['reason'][:50]}")

        # SL/TP 效果统计 (B: 止损距离 vs 胜率)
        sl_stats = ""
        sl_buckets = {}
        for d in decided:
            sp = d.get("sl_pct")
            if sp is not None:
                bucket = "紧(≤2%)" if sp <= 2 else ("中(2-5%)" if sp <= 5 else "宽(>5%)")
                sl_buckets.setdefault(bucket, [0, 0, 0])
                o = d["outcome"]
                sl_buckets[bucket][0 if o == "win" else 1 if o == "loss" else 2] += 1
        if sl_buckets:
            parts = []
            for b, (w, l, f) in sl_buckets.items():
                total = w + l + f
                if total:
                    parts.append(f"{b}: 胜{w}/负{l}/平{f} ({w/total*100:.0f}%)")
            sl_stats = "\n止损距离效果: " + "; ".join(parts)

        stats_str = (
            f"总{stats['total']} 胜{stats['win']} 负{stats['loss']} "
            f"平{stats['flat']}\n"
            f"按时段: {json.dumps(stats['by_session'], ensure_ascii=False)}\n"
            f"按regime: {json.dumps(stats['by_regime'], ensure_ascii=False)}\n"
            f"按方向: {json.dumps(stats['by_action'], ensure_ascii=False)}"
            + sl_stats
        )
        prompt = (
            "你是交易系统复盘员。以下是最近交易决策与结果：\n\n"
            + "\n".join(lines) + "\n\n"
            + "统计:\n" + stats_str + "\n\n"
            "输出JSON对象:\n"
            '{"lessons": ["经验1","经验2","经验3"], "rules": ["禁止项1","禁止项2"]}\n'
            "lessons: 3-5条可操作经验 (如'RSI>70追多易亏','盘后胜率低'), 每条≤60字中文\n"
            "rules: 1-3条硬规则, 必须是否定句/禁止项, 针对反复出现的亏损模式\n"
            "       (如'ADX<20时禁止开仓','盘后禁止新开仓'), 每条≤50字中文"
        )
        raw = await self._call(
            system="你是交易复盘员，只输出JSON对象。",
            prompt=prompt, model="deepseek-v4-flash",
            temp=0.3, max_tokens=2000, json_mode=False,
        )
        parsed = self._parse_json(raw)
        if isinstance(parsed, dict):
            lessons = [str(x)[:80] for x in parsed.get("lessons", [])][:6]
            rules = [str(x)[:60] for x in parsed.get("rules", [])][:4]
            if lessons:
                logger.info("AI 复盘: %d 条经验, %d 条硬规则", len(lessons), len(rules))
                for l in lessons:
                    logger.info("  • %s", l)
                for r in rules:
                    logger.info("  ⛔ %s", r)
                return lessons, rules
        logger.warning("复盘解析失败: %s", str(raw)[:100])
        return [], []

    # ═══ 辅助 ═══════════════════════

    def _to_signal(self, inp: AIInput, result: dict, direction: str) -> Optional[Signal]:
        action = result.get("action", "HOLD")
        if action == "HOLD":
            return None
        sl = result.get("stop_loss", 0)
        tp = result.get("take_profit", 0)
        if not sl or not tp:
            return None
        entry = inp.mark_price
        is_buy = action in ("BUY", "STRONG_BUY")

        if is_buy and (float(sl) >= entry or float(tp) <= entry):
            return None
        if not is_buy and (float(sl) <= entry or float(tp) >= entry):
            return None
        return Signal(
            strategy_id="ai_native", symbol=inp.symbol,
            action=action,
            confidence=float(result.get("confidence", 0.5)),
            entry_price=entry, stop_loss=float(sl),
            take_profits=[float(tp)],
            reason=result.get("reason", "")[:500],
        )

    @staticmethod
    def _fix_truncated_json(raw: str) -> str:
        raw = raw.strip()
        open_braces = raw.count("{") - raw.count("}")
        open_brackets = raw.count("[") - raw.count("]")
        if "," in raw[-20:]:
            raw = raw.rstrip().rstrip(",")
        if raw and raw[-1] not in ('}', ']', '"'):
            raw += '"'
        raw += "}" * open_braces + "]" * open_brackets
        return raw

    @staticmethod
    def _parse_json(raw: str) -> Optional[dict]:
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            pass
        fixed = AINativeDecisionMaker._fix_truncated_json(raw)
        try:
            return json.loads(fixed)
        except Exception:
            pass
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                return json.JSONDecoder(strict=False).raw_decode(m.group())[0]
            except Exception:
                pass
        return None

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None
