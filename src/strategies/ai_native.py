"""AI 原生决策器 — 无规则策略，纯 AI 驱动

流程: Flash快速判断 → BUY/SELL通过 / HOLD跳过 → Pro深度分析 → 最终决策
数据: 500x1H + 4H/1D + 大盘 + 新闻 + OI/费率
"""

import asyncio, json, logging, os, re
from dataclasses import dataclass, field
from typing import Optional

import httpx

from src.core.types import Signal

logger = logging.getLogger(__name__)

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
    volume_24h: float


class AINativeDecisionMaker:
    """AI 原生决策器。Flash 过滤 → Pro 决策。"""

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._api_key = os.environ.get("OPENCODE_API_KEY", "")
        self._base_url = "https://opencode.ai/zen/go/v1"

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
        """两层决策：规则初筛 → Pro 深度分析。"""
        # Layer 1: 规则快速过滤（不调 API）
        rsi1h = inp.ind_1h.get('rsi', 50)
        regime = inp.ind_1h.get('regime', '')
        adx = inp.ind_1h.get('adx', 0)
        ma10 = inp.ind_1h.get('ma10', 0)
        ma30 = inp.ind_1h.get('ma30', 0)
        rsi4h = inp.ind_4h.get('rsi', 50) if inp.ind_4h else 50
        rsi1d = inp.ind_1d.get('rsi', 50) if inp.ind_1d else 50

        direction = None

        # 趋势做多条件
        if regime in ('trend_up', 'weak_trend') and adx > 25:
            if 40 <= rsi1h <= 75 and rsi4h < 80 and rsi1d < 80 and ma10 > ma30:
                direction = "BUY"

        # 趋势做空条件
        if regime in ('trend_down',) and adx > 25:
            if 25 <= rsi1h <= 65 and rsi4h > 20 and rsi1d > 20 and ma10 < ma30:
                direction = "SELL"

        # 超卖反弹
        if rsi1h < 35 and rsi4h < 40:
            direction = "BUY"

        # 超买回落
        if rsi1h > 75 and rsi4h > 65:
            direction = "SELL"

        if not direction:
            return None  # 规则过滤

        # Layer 2: Pro 深度分析 + SL/TP
        pro = await self._pro_deep_analyze(inp, flash_direction=direction)
        if not pro:
            return None
        return self._to_signal(inp, pro)

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

        prompt = (
            f"Flash判断 {inp.symbol} 方向为{flash_direction}。你确认并定价位。\n"
            f"${inp.mark_price:.2f} ({inp.change_pct:+.1f}%)\n\n"
            f"1H RSI={inp.ind_1h.get('rsi',50):.0f} MA10={inp.ind_1h.get('ma10',0):.1f} MA30={inp.ind_1h.get('ma30',0):.1f} "
            f"MACD={inp.ind_1h.get('macd',0):.3f} ATR={inp.ind_1h.get('atr',0):.2f} "
            f"ADX={inp.ind_1h.get('adx',0):.0f} {inp.ind_1h.get('regime','')}\n"
            + (f"4H RSI={inp.ind_4h.get('rsi',0):.0f} MA10={inp.ind_4h.get('ma10',0):.1f} MA30={inp.ind_4h.get('ma30',0):.1f}\n" if inp.ind_4h else "")
            + (f"1D RSI={inp.ind_1d.get('rsi',0):.0f} MA10={inp.ind_1d.get('ma10',0):.1f} MA30={inp.ind_1d.get('ma30',0):.1f}\n" if inp.ind_1d else "")
            + f"大盘 {bench_str}\n"
            f"OI={inp.open_interest:.0f} 费率={inp.funding_rate*100:.4f}%\n"
            f"新闻 {inp.news_summary or '无'}\n\n"
            "输出JSON:\n"
            '{"action":"BUY/SELL/HOLD","stop_loss":x,"take_profit":x,"reason":"..."}\n'
            "HOLD是合法的。不确定就HOLD。"
        )
        raw = await self._call(
            system="你是美股交易分析师。输出JSON，不要思考，直接给结果。",
            prompt=prompt, model="deepseek-v4-pro",
            temp=0.3, max_tokens=4000, json_mode=False,
        )
        return self._parse_json(raw)

    # ═══ 辅助 ═══════════════════════

    def _to_signal(self, inp: AIInput, result: dict) -> Optional[Signal]:
        action = result.get("action", "HOLD")
        if action == "HOLD":
            return None
        sl = result.get("stop_loss", 0)
        tp = result.get("take_profit", 0)
        if not sl or not tp:
            return None
        entry = inp.mark_price
        is_buy = action in ("BUY", "STRONG_BUY")

        # 止损距离限制：最大 5%
        max_sl_pct = 0.05
        if is_buy:
            min_sl = entry * (1 - max_sl_pct)
            sl = max(float(sl), min_sl)
        else:
            max_sl = entry * (1 + max_sl_pct)
            sl = min(float(sl), max_sl)

        if is_buy and (sl >= entry or tp <= entry):
            return None
        if not is_buy and (sl <= entry or tp >= entry):
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
