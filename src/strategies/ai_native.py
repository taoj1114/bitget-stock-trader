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
    ind_15m: dict | None = None  # 日内主指标 (管仓用 15m)
    ind_5m: dict | None = None   # 开仓用 5m (更细的日内信号)
    orderbook: str = ""           # 盘口压力文本 (实时微观数据)
    trend_shape: str = ""          # 走势形状分析文本 (动态轨迹)
    session: str = "regular"  # pre_market / regular / post_market / weekend / holiday
    lessons: list[str] = field(default_factory=list)  # 复盘经验
    rules: list[str] = field(default_factory=list)    # 硬规则 (禁止项)
    history: list[dict] = field(default_factory=list)  # 品种记忆: 该股近期AI判断
    position_ctx: dict | None = None  # 持仓上下文 (管仓时)


class AINativeDecisionMaker:
    """AI 原生决策器。模型/API 可配置 (config.yaml deepseek 段)。"""

    def __init__(self, model: str = "deepseek-v4-flash",
                 base_url: str = "https://api.deepseek.com",
                 api_key: str = ""):
        self._client: Optional[httpx.AsyncClient] = None
        self._api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self._base_url = base_url
        self._model = model

    async def _call(self, system: str, prompt: str, model: str,
                    temp: float, max_tokens: int, json_mode: bool = False,
                    retries: int = 0) -> str:
        """调用模型。retries>0 时空返回/异常时重试。"""
        if not self._api_key:
            logger.warning("API key 未配置")
            return ""
        if not self._client:
            self._client = httpx.AsyncClient(timeout=60)
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
        last_err = ""
        for attempt in range(retries + 1):
            try:
                resp = await self._client.post(f"{self._base_url}/chat/completions", headers=headers, json=payload)
                data = resp.json()
                if resp.status_code != 200:
                    last_err = f"API {resp.status_code}: {str(data)[:120]}"
                    logger.warning("API %d: %s", resp.status_code, str(data)[:200])
                else:
                    choices = data.get("choices", [])
                    if choices:
                        content = choices[0].get("message", {}).get("content", "")
                        if content:
                            return content
                        last_err = "empty content"
                        logger.warning("API empty content (attempt %d/%d)", attempt + 1, retries + 1)
                    else:
                        last_err = "no choices"
            except Exception as e:
                last_err = str(e)
                logger.warning("API exception: %s", e)
            if attempt < retries:
                await asyncio.sleep(1.5 * (attempt + 1))
        logger.warning("API 调用最终失败: %s", last_err)
        return ""

    async def decide(self, inp: AIInput) -> Optional[Signal]:
        """纯 AI 决策：直接分析全部数据。"""
        pro = await self._pro_deep_analyze(inp)
        if not pro:
            return None
        pro_action = pro.get("action", "HOLD")
        if pro_action == "HOLD":
            return None
        return self._to_signal(inp, pro, pro_action)

    # ═══ 开仓决策 ═══════════════════════

    async def _pro_deep_analyze(self, inp: AIInput) -> Optional[dict]:
        """AI 深度分析，定方向 + SL/TP。"""
        bench_str = " ".join(f"{k}{v:+.1f}%" for k, v in inp.bench.items())
        lessons = inp.lessons or []
        lesson_str = ""
        if lessons:
            lesson_str = "\n历史经验(复盘总结):\n" + "\n".join(f"  • {l}" for l in lessons) + "\n"

        rules = inp.rules or []
        # 去重: 与 lessons 重叠的硬规则剔除
        if lessons:
            rules = [r for r in rules if not any(r[:10] in l or l[:10] in r for l in lessons)]
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
            + (f"5m RSI={inp.ind_5m.get('rsi',50):.0f} MA10={inp.ind_5m.get('ma10',0):.2f} MA30={inp.ind_5m.get('ma30',0):.2f} "
               f"ATR={inp.ind_5m.get('atr',0):.2f} "
               f"VWAP={inp.ind_5m.get('vwap',0):.2f} 量比={inp.ind_5m.get('volume_ratio',1):.1f} BB={inp.ind_5m.get('bb_position',0.5):.2f} [日内]\n" if inp.ind_5m else "")
            + (inp.trend_shape or "")
            + f"1H RSI={inp.ind_1h.get('rsi',50):.0f} ADX={inp.ind_1h.get('adx',0):.0f} {inp.ind_1h.get('regime','')}\n"
            + f"大盘 {bench_str}\n"
            f"OI={inp.open_interest:.0f} 费率={inp.funding_rate*100:.4f}%\n"
            f"新闻 {inp.news_summary or '无'}\n"
            + (inp.orderbook or "")
            + hist_str
            + rules_str
            + lesson_str
            + "\n输出JSON:\n"
            '{"action":"BUY/SELL/HOLD","stop_loss":x,"take_profit":x,"reason":"..."}\n'
            "决策前先在内心快速推理(不输出思考过程, 只输出JSON):\n"
            "  ① 方向: 5m与1H是否同向? 同向才考虑入场\n"
            "  ② 位置: 价格在MA/VWAP/BB什么位置? 是否透支? 追高还是回踩?\n"
            "  ③ 风险: 止损该放哪(结构位+ATR缓冲)? 盈亏比是否≥1?\n"
            "HOLD是合法的——但HOLD不是默认答案。\n"
            "当趋势、动量、量能、大盘环境共振确认时, 应该果断入场抓住机会。\n"
            "机会出现时犹豫不决、总是观望同样是错误。\n"
            "但注意: 每轮交易手续费约0.12%(开+平), 5m信号必须与1H趋势同向才算有效;\n"
            "5m单独出现而1H反向的信号多为噪音, 不应开仓。宁可少做, 不做没把握的。\n"
            "入场位置铁律(防追高/防被反转吞没):\n"
            "  1. 方向同向只是必要条件, 位置才是关键——趋势确认时往往已透支\n"
            "  2. 顺势追高禁止: 价格已远离MA10/MA30(如5m价格>MA30的1.5×ATR以上), 即使趋势同向也HOLD等回踩\n"
            "  3. 理想入场: 趋势同向 + 价格回踩到MA10/VWAP附近企稳(量缩回踩), 或刚突破颈线放量确认\n"
            "  4. BB位置>0.9(贴上轨)或<0.1(贴下轨)时禁止顺势入场——那是透支区, 反转风险极高\n"
            "  5. 量价背离(新高但量比<0.5)是假突破信号, 禁止入场\n"
            "  6. 反转吞没风险: 5m出现长上影/长下影且RSI极端(>75/<25)时, 即使趋势同向也HOLD\n"
            "反转行情处理(空仓时):\n"
            "  1. 反转初期(1H趋势刚由up转down或反之, 方向刚变)禁止逆势抄底/摸顶——波动最剧烈\n"
            "  2. 让反转走完第一波, 等新趋势确立: 1H regime稳定 + 5m顺新趋势回踩企稳\n"
            "  3. 然后顺新趋势入场(做反转后的第二波), 这是反转行情中最安全的入场点\n"
            "历史结果仅供参考——行情会反转, 必须以当前数据为准, 绝不因旧判断而固执。\n"
            "时段策略: regular(盘中)正常交易; pre_market盘前/post_market盘后/closed深夜流动性差,"
            "除非信号极强否则HOLD; weekend周末休市必HOLD。\n"
            "日内交易原则(快进快出为核心):\n"
            "  1. 目标止盈: 0.5%-1.5% 的小目标, 到目标果断离场, 不贪大(日内吃波段, 不赌趋势)\n"
            "  2. 止损: 设在结构位(前低/前高/MA10/MA30)下方+0.5×ATR缓冲, 距离约0.5%-1%;\n"
            "     止损与止盈接近(盈亏比约1:1~1:1.5), 靠胜率取胜而非单笔大赚\n"
            "  3. 持仓时间: 30分钟-3小时, 到点不到目标也离场(时间止损, 避免隔夜)\n"
            "  4. 反扫止损保护: 止损必须放在结构位+缓冲处, 不放窄幅百分比(日内噪音易扫)\n"
            "  5. 不追已大幅透支的行情; 入场时若当日已涨/跌超5%, 只做回踩不追高\n"
        )
        raw = await self._call(
            system="你是美股日内交易分析师。决策前在内心完成推理, 只输出JSON结果。",
            prompt=prompt, model=self._model,
            temp=0.3, max_tokens=4000, json_mode=False,
            retries=2,  # 开仓低频高价值, 空返回重试2次
        )
        return self._parse_json(raw)

    # ═══ 管仓 (持仓管理) ═══════════════════

    async def manage_position(self, inp: AIInput) -> Optional[Signal]:
        """管仓决策 — 日内持仓管理, 动态止盈止损。

        专注: 最大化日内利润 + 控制回撤
        - 移动止损锁利 (盈利后上移/下移止损保本)
        - 目标位动态调整 (接近TP收紧, 趋势强让利润奔跑)
        - 时间衰减 (日内持仓不宜过久, 避免隔夜风险)
        返回: Signal(action=HOLD/CLOSE/ADJUST, SL/TP) 或 None(HOLD)
        """
        ctx = inp.position_ctx or {}
        bench_str = " ".join(f"{k}{v:+.1f}%" for k, v in inp.bench.items())

        # 品种历史 (管仓记忆, 仅看已平仓结果 + 防锚定)
        hist_str = ""
        if inp.history:
            decided = [h for h in inp.history if h.get("outcome") is not None][-3:]
            if decided:
                lines = [f"  {h.get('time','')[:16]} {h.get('action','?')} → {h.get('outcome','')} "
                         f"pnl={h.get('close_pnl')} SL%={h.get('sl_pct')} | {h.get('reason','')[:40]}"
                         for h in decided]
                hist_str = "\n该股历史交易结果(仅供参考, 勿固执):\n" + "\n".join(lines) + "\n"

        prompt = (
            f"你是日内交易持仓管理AI。管理 {inp.symbol} 的持仓, 目标是日内最大获利 + 最小回撤。\n"
            f"时段: {inp.session} | ${inp.mark_price:.2f} ({inp.change_pct:+.1f}%)\n"
            + ("[周末/深夜闭市: 流动性极差, 仅保守检查, 不做大调整]\n"
               if inp.session in ("weekend", "closed") else "")
            + f"\n"
            f"持仓: {ctx.get('side','?')} 开仓价=${ctx.get('entry',0):.2f} "
            f"持仓{ctx.get('hours',0):.0f}小时 浮盈=${ctx.get('pnl',0):.2f}\n"
            f"当前SL=${ctx.get('sl',0):.2f} 当前TP=${ctx.get('tp',0):.2f}\n\n"
            + (f"5m RSI={inp.ind_5m.get('rsi',50):.0f} MA10={inp.ind_5m.get('ma10',0):.2f} MA30={inp.ind_5m.get('ma30',0):.2f} "
               f"VWAP={inp.ind_5m.get('vwap',0):.2f} 量比={inp.ind_5m.get('volume_ratio',1):.1f} BB={inp.ind_5m.get('bb_position',0.5):.2f} [日内]\n" if inp.ind_5m else "")
            + (inp.trend_shape or "")
            + f"1H RSI={inp.ind_1h.get('rsi',50):.0f} ADX={inp.ind_1h.get('adx',0):.0f} {inp.ind_1h.get('regime','')}\n"
            + f"大盘 {bench_str}\n"
            + f"新闻 {inp.news_summary or '无'}\n"
            + (inp.orderbook or "")
            + hist_str
            + "\n日内持仓管理原则(快进快出):\n"
            "1. 小目标落袋: 浮盈达0.5%-1.5%即果断止盈离场(日内吃波段, 不贪大, 到目标就走)\n"
            "2. 时间止损: 持仓超3小时未到目标 → 收紧止损至保本并准备离场(日内不留隔夜)\n"
            "3. 移动止损保护: 浮盈≥0.5%后止损移到保本; 浮盈≥1%后止损移到浮盈一半处锁利\n"
            "4. 手续费意识: 每轮约0.12%成本, 浮盈>0.5%时落袋已覆盖成本, 不必等更大利润\n"
            "5. 回撤保护: 浮盈从最高点回吐超50% → 立即离场, 不让盈利变亏损\n"
            "6. 反转识别(区分回调vs反转):\n"
            "   - 回调(继续持有): 价格回踩MA10附近企稳, 5m RSI回落到40-60, 量缩, 1H趋势未变\n"
            "   - 反转确认(立即平仓): 满足≥2条才确认, 单条不算:\n"
            "     (a)价格跌破5m MA30 且 1H regime 由trend_up转range/down (或反之)\n"
            "     (b)5m MACD死叉/金叉 + 1H RSI从超买>75回落破60 (或超卖<25反弹破40)\n"
            "     (c)放量(量比>1.5)跌破VWAP — 趋势支撑位失守\n"
            "     (d)BB位置从>0.8直接跌破0.5 (价格穿中轨向下)\n"
            "   反转确认后: 果断平仓离场, 不等SL触发, 不反手做单(反转初期波动剧烈风险高)\n"
            "7. 不因小幅波动恐慌离场: 单根5m影线/单次RSI超买≠反转, 需满足上述≥2条\n\n"
            "输出JSON:\n"
            '{"action":"HOLD/CLOSE","stop_loss":x,"take_profit":x,"reason":"..."}\n'
            "HOLD=继续持有(可调整SL/TP), CLOSE=平仓离场。\n"
            "CLOSE必须满足上述铁律条件之一, 并写明具体原因。\n"
            "stop_loss/take_profit 为调整后的新价位(不调整则填当前值)。\n"
        )
        raw = await self._call(
            system="你是日内交易持仓管理AI。输出JSON。",
            prompt=prompt, model=self._model,
            temp=0.2, max_tokens=2000, json_mode=False,
        )
        result = self._parse_json(raw)
        if not result:
            return None
        action = result.get("action", "HOLD")
        if action == "CLOSE":
            # 平仓信号: 直接返回 CLOSE 信号 (auto_trader 处理)
            return Signal(
                strategy_id="ai_manage", symbol=inp.symbol,
                action="CLOSE",
                confidence=0.8,
                entry_price=inp.mark_price, stop_loss=0,
                take_profits=[], reason=result.get("reason", "")[:300],
            )
        # HOLD + 新 SL/TP (或保持当前)
        cur_sl = ctx.get("sl", 0) or 0
        cur_tp = ctx.get("tp", 0) or 0
        new_sl = float(result.get("stop_loss", cur_sl) or cur_sl)
        new_tp = float(result.get("take_profit", cur_tp) or cur_tp)
        is_long = ctx.get("side", "") == "LONG"

        # 有效性校验
        if new_sl <= 0 or new_tp <= 0:
            return None
        if is_long and (new_sl >= inp.mark_price or new_tp <= inp.mark_price):
            return None
        if not is_long and (new_sl <= inp.mark_price or new_tp >= inp.mark_price):
            return None

        return Signal(
            strategy_id="ai_manage", symbol=inp.symbol,
            action="HOLD",
            confidence=0.8,
            entry_price=inp.mark_price, stop_loss=new_sl,
            take_profits=[new_tp],
            reason=result.get("reason", "")[:300],
        )

    # ═══ 自我复盘 ═══════════════════════

    async def review_and_learn(self, memory) -> tuple[list[str], list[str]]:
        """AI 读历史决策+结果 → (经验教训, 硬规则)。"""
        decisions = memory.recent_decisions(30)
        # 只统计有结果(已平仓)的决策
        decided = [d for d in decisions if d.get("outcome") is not None]
        if len(decided) < 3:
            # 无真实交易样本 → 不生成经验/规则 (避免从HOLD噪音学习)
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
            prompt=prompt, model=self._model,
            temp=0.3, max_tokens=2000, json_mode=False,
        )
        parsed = self._parse_json(raw)
        if isinstance(parsed, dict):
            lessons = [str(x)[:80] for x in parsed.get("lessons", [])][:6]
            rules = [str(x)[:60] for x in parsed.get("rules", [])][:4]
            # 去重: rules 若与 lessons 内容重叠则剔除
            rules = [r for r in rules if not any(r[:10] in l or l[:10] in r for l in lessons)]
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
