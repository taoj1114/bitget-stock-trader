"""AI 原生决策器 — 无规则策略，纯 AI 驱动

流程: Flash快速判断 → BUY/SELL通过 / HOLD跳过 → Pro深度分析 → 最终决策
数据: 500x1H + 4H/1D + 大盘 + 新闻 + OI/费率
"""

import asyncio, json, logging, os, re, time
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
    account_status: str = ""       # 账户状态提示 (防防守过度)
    session: str = "regular"  # pre_market / regular / post_market / weekend / holiday
    lessons: list[str] = field(default_factory=list)  # 复盘经验
    rules: list[str] = field(default_factory=list)    # 硬规则 (禁止项)
    history: list[dict] = field(default_factory=list)  # 品种记忆: 该股近期AI判断
    position_ctx: dict | None = None  # 持仓上下文 (管仓时)


class AINativeDecisionMaker:
    """AI 原生决策器。模型/API 可配置 (config.yaml deepseek 段)。"""

    # AI 熔断器: 连续失败超阈值 → 暂停调用 (防API宕机时雪崩重试)
    _fail_streak = 0
    _fail_paused_until = 0.0
    FAIL_THRESHOLD = 5     # 连续5次失败
    FAIL_PAUSE_SECONDS = 300  # 暂停5分钟

    def __init__(self, model: str = "deepseek-v4-flash",
                 base_url: str = "https://api.deepseek.com",
                 api_key: str = ""):
        self._client: Optional[httpx.AsyncClient] = None
        self._api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self._base_url = base_url
        self._model = model

    def _circuit_open(self) -> bool:
        """熔断检查: 连续失败暂停期内 → True (跳过调用)。"""
        if AINativeDecisionMaker._fail_streak >= AINativeDecisionMaker.FAIL_THRESHOLD:
            if time.time() > AINativeDecisionMaker._fail_paused_until:
                # 暂停到期, 重置计数
                AINativeDecisionMaker._fail_streak = 0
                logger.info("AI熔断暂停到期, 恢复调用")
            else:
                return True
        return False

    def _record_success(self):
        AINativeDecisionMaker._fail_streak = 0

    def _record_failure(self):
        AINativeDecisionMaker._fail_streak += 1
        if AINativeDecisionMaker._fail_streak == AINativeDecisionMaker.FAIL_THRESHOLD:
            AINativeDecisionMaker._fail_paused_until = \
                time.time() + AINativeDecisionMaker.FAIL_PAUSE_SECONDS
            logger.warning("AI连续失败 %d 次 → 熔断暂停 %d 秒",
                          AINativeDecisionMaker.FAIL_THRESHOLD,
                          AINativeDecisionMaker.FAIL_PAUSE_SECONDS)

    async def _call(self, system: str, prompt: str, model: str,
                    temp: float, max_tokens: int, json_mode: bool = False,
                    retries: int = 0) -> str:
        """调用模型。retries>0 时空返回/异常时重试。熔断保护防雪崩。"""
        if not self._api_key:
            logger.warning("API key 未配置")
            return ""
        if self._circuit_open():
            logger.warning("AI熔断中, 跳过调用 (暂停至 %ds)",
                          int(AINativeDecisionMaker._fail_paused_until - time.time()))
            return ""
        if not self._client:
            # 智能超时: 连接10s(网络问题快速失败) + 读60s(容忍慢响应)
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(60.0, connect=10.0))
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
                    # 4xx 是请求问题, 重试无意义 → 直接失败
                    if 400 <= resp.status_code < 500:
                        self._record_failure()
                        return ""
                else:
                    choices = data.get("choices", [])
                    if choices:
                        content = choices[0].get("message", {}).get("content", "")
                        if content:
                            self._record_success()
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
        self._record_failure()
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
               f"MACD={inp.ind_5m.get('macd_cross','')} "
               f"VWAP={inp.ind_5m.get('vwap',0):.2f} 量比={inp.ind_5m.get('volume_ratio',1):.1f} BB={inp.ind_5m.get('bb_position',0.5):.2f} [日内]\n" if inp.ind_5m else "")
            + (inp.trend_shape or "")
            + f"1H RSI={inp.ind_1h.get('rsi',50):.0f} ADX={inp.ind_1h.get('adx',0):.0f} {inp.ind_1h.get('regime','')} MACD={inp.ind_1h.get('macd_cross','')}\n"
            + f"大盘 {bench_str}\n"
            f"OI={inp.open_interest:.0f} 费率={inp.funding_rate*100:.4f}%\n"
            f"新闻 {inp.news_summary or '无'}\n"
            + (inp.orderbook or "")
            + hist_str
            + rules_str
            + lesson_str
            + (inp.account_status or "")
            + "\n输出JSON:\n"
            '{"action":"BUY/SELL/HOLD","stop_loss":x,"take_profit":x,"reason":"..."}\n'
            "决策前先在内心快速推理(不输出思考过程, 只输出JSON):\n"
            "  ① 行情性质: 当前是'反转'还是'顺势'? 反转行情是主要机会来源!\n"
            "  ② 位置: 是否已透支(RSI极端/BB贴边/远离MA30)? 透支处正是反转温床\n"
            "  ③ 反转信号: 走势是否衰竭(单边→横盘/反向)? 最后一根K线长影线?\n"
            "     (长上影+RSI高=见顶信号→做空; 长下影+RSI低=见底信号→做多)\n"
            "  ④ 确认: 放量突破关键位/MACD交叉/1H regime切换? 确认后才入场\n"
            "  ⑤ 风险: 止损放结构位外侧+ATR缓冲? 盈亏比是否≥1?\n"
            "HOLD是合法的——但HOLD不是默认答案。\n"
            "反转机会出现时必须果断——犹豫观望同样是错误。\n"
            "但注意: 每轮交易手续费约0.12%;\n"
            "═══ 核心策略: 反转行情 (主要机会!) ═══\n"
            "反转信号识别(满足越多越可靠):\n"
            "  R1 极端位置: RSI>75超买或RSI<25超卖; BB位置>0.9或<0.1\n"
            "  R2 走势衰竭: 加速上涨后动量衰减(前段大涨+后段停滞/反向), 或连续单边后出现反向K线\n"
            "  R3 影线信号: 长上影(高位抛压)→见顶; 长下影(低位承接)→见底\n"
            "  R4 量价背离: 创新高但量比<0.5(缩量新高=买盘枯竭), 或暴跌后放量止跌\n"
            "  R5 突破确认: 放量(量比>1.2)反向突破近2小时关键位 + MACD交叉\n"
            "做多反转(抄底超卖): RSI<30 + 长下影/放量止跌 + 突破前低失败回升\n"
            "  → 放量突破5m MA10/VWAP + 1H不再创新低 确认入场\n"
            "做空反转(摸顶超买): RSI>70 + 长上影/放量滞涨 + 跌破前高失败回落\n"
            "  → 放量跌破5m MA10/VWAP + 1H不再创新高 确认入场\n"
            "反转入场止损: 关键位(近2小时高/低点)外侧+0.5×ATR缓冲, 距离近风险可控\n"
            "反转获利: 目标回到均值(MA20/前密集区), 快进快出, 反转波段常很快\n"
            "⚠️ 反转前提: 必须在'极端/衰竭'位置反转才有价值——强势单边中途的逆势是接刀!\n"
            "═══ 辅助策略: 顺势 (仅趋势明确时) ═══\n"
            "  顺势做多: 1H+5m同向上 + ADX>20 + 回踩MA10/VWAP企稳(量缩)→买\n"
            "  顺势做空: 1H+5m同向下 + ADX>20 + 反弹到VWAP/MA10受阻→卖\n"
            "  顺势追涨/追跌: 放量突破关键位吃加速段 (前置否决: 当日>15%或RSI>85/<15禁追)\n"
            "  大盘共振: SPY/QQQ强+个股跟涨→顺势多; 大盘弱+个股跟跌→顺势空\n"
            "═══ 通用 ═══\n"
            "  优先判断反转机会(极端位置+衰竭+确认), 无反转机会才考虑顺势;\n"
            "  顺势入场必须趋势明确(ADX>20)且未透支;\n"
            "  当日已涨/跌>15%视为极端行情——只做反转不追势;\n"
            "历史结果仅供参考——行情会反转, 必须以当前数据为准, 绝不因旧判断而固执。\n"
            "时段策略: regular(盘中)正常交易; pre_market盘前/post_market盘后/closed深夜\n"
            "          也可以正常开仓——只需注意流动性价差较大时止损放宽一档;\n"
            "          weekend周末休市必HOLD。\n"
            "日内交易原则(快进快出为核心, 盈亏数学是根本!):\n"
            "  0. 盈亏数学(必须遵守): 手续费每轮0.24%(开+平), 反转行情胜率天然40-55%,\n"
            "     → 止盈必须 ≥2×止损 (盈亏比≥2), 否则扣费后必亏!\n"
            "     正确结构: 止损≈1% + 止盈≈2%-2.5% (盈亏比2:1+, 胜率40%即可盈利)\n"
            "  1. 止损: 约1% (关键位外侧+0.5×ATR缓冲, 且≥1×5m ATR防噪音扫损)\n"
            "     - 止损距离控制0.8%-1.2%, 既躲开噪音又不过宽\n"
            "  2. 止盈: 2%-2.5% (反转目标回均值/前密集区/2h波动高点)\n"
            "     - 反转波段通常有1.5-3%空间, 不要止盈在0.8%-1%——那是给手续费打工\n"
            "  3. 持仓时间: 30分钟-3小时, 到点不到目标也离场(时间止损, 避免隔夜)\n"
            "  4. 浮盈≥0.5%后止损移到保本, ≥1%后移到浮盈一半(移动止损锁利)\n"
            "  5. 不追已大幅透支的行情; 入场时若当日已涨/跌超5%, 只做反转不追高\n"
            "  6. 盈利保护优先: 浮盈达1.5%可落袋一半, 剩余移保本博2%+——不让到手的利润回吐\n"
        )
        raw = await self._call(
            system="你是美股日内交易分析师。决策前在内心完成推理, 只输出JSON结果。",
            prompt=prompt, model=self._model,
            temp=0.3, max_tokens=4000, json_mode=False,
            retries=1,  # 开仓重试1次 (30s超时上限, 防全池扫描积压)
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

        # 复盘经验 (管仓也应参考历史教训)
        lesson_str = ""
        if inp.lessons:
            lesson_str = "\n历史经验(复盘总结, 仅供参考):\n" + "\n".join(
                f"  • {l}" for l in inp.lessons) + "\n"

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
               f"VWAP={inp.ind_5m.get('vwap',0):.2f} 量比={inp.ind_5m.get('volume_ratio',1):.1f} BB={inp.ind_5m.get('bb_position',0.5):.2f} "
               f"MACD={inp.ind_5m.get('macd_cross','')} [日内]\n" if inp.ind_5m else "")
            + (inp.trend_shape or "")
            + f"1H RSI={inp.ind_1h.get('rsi',50):.0f} ADX={inp.ind_1h.get('adx',0):.0f} {inp.ind_1h.get('regime','')} MACD={inp.ind_1h.get('macd_cross','')}\n"
            + f"大盘 {bench_str}\n"
            + f"新闻 {inp.news_summary or '无'}\n"
            + (inp.orderbook or "")
            + hist_str
            + lesson_str
            + "\n日内持仓管理原则(快进快出):\n"
            "0. 多空差异: 做多持仓(浮盈保护靠移动止损); 做空持仓额外注意:\n"
            "   - 尾盘(15:00-16:00 ET)空头仓位主动收紧保护——美股尾盘常拉升\n"
            "   - 暴跌后出现放量长下影/RSI<25反弹 → 空头及时止盈(超卖反弹快)\n"
            "1. 止盈目标: 2%-2.5% (反转波段空间; 0.8-1%太薄扣费后净赚无几, 至少等到1.5%以上)\n"
            "2. 时间止损: 持仓超3小时未到目标 → 收紧止损至保本并准备离场(日内不留隔夜)\n"
            "3. 移动止损保护: 浮盈≥0.5%后止损移到保本; 浮盈≥1%后止损移到浮盈一半处锁利\n"
            "4. 手续费意识: 每轮0.24%成本(开+平), 单笔净利至少要>0.5%才有意义\n"
            "5. 回撤保护: 浮盈从最高点回吐超50% → 立即离场, 不让盈利变亏损\n"
            "6. 盈利落袋: 浮盈达1.5%可平一半锁利, 剩余移保本博2%+目标\n"
            "7. 反转识别(区分回调vs反转):\n"
            "   - 走势形状辅助判断: 近3根K线连续长上影/长下影=反转迹象; 单边加速后突然反向=反转\n"
            "   - 回调(继续持有): 价格回踩MA10附近企稳, 5m RSI回落到40-60, 量缩, 1H趋势未变\n"
            "   - 反转确认(立即平仓): 满足≥2条才确认, 单条不算:\n"
            "     (a)价格跌破5m MA30 且 1H regime 由trend_up转range/down (或反之)\n"
            "     (b)5m MACD死叉/金叉 + 1H RSI从超买>75回落破60 (或超卖<25反弹破40)\n"
            "     (c)放量(量比>1.5)跌破VWAP — 趋势支撑位失守\n"
            "     (d)BB位置从>0.8直接跌破0.5 (价格穿中轨向下)\n"
            "   反转确认后: 果断平仓离场, 不等SL触发, 不反手做单(反转初期波动剧烈风险高)\n"
            "8. 不因小幅波动恐慌离场: 单根5m影线/单次RSI超买≠反转, 需满足上述≥2条\n\n"
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
            '{"lessons": ["经验1","经验2","经验3"]}\n'
            "lessons: 3-5条可操作经验, 每条≤60字中文\n"
            "注意: 只输出经验教训(参考性质), 不要输出任何'禁止'类规则——\n"
            "      交易决策完全由主AI自由判断, 复盘经验只是提供视角参考。\n"
            "      经验要具体描述'什么情况下容易发生什么', 而非下禁令。\n"
            "      如'盘前流动性较差时追涨容易被扫' 而非 '盘前禁止开仓'。\n"
        )
        raw = await self._call(
            system="你是交易复盘员，只输出JSON对象。",
            prompt=prompt, model=self._model,
            temp=0.3, max_tokens=2000, json_mode=False,
        )
        parsed = self._parse_json(raw)
        if isinstance(parsed, dict):
            lessons = [str(x)[:80] for x in parsed.get("lessons", [])][:6]
            # AI 独揽决策: 不生成硬规则 (只保留经验参考)
            rules = []
            if lessons:
                logger.info("AI 复盘: %d 条经验 (硬规则已停用)", len(lessons))
                for l in lessons:
                    logger.info("  • %s", l)
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

        # ── 代码级安全防线 (LLM可能不遵守prompt, 这里硬拦截/修正) ──
        sl_dist = abs(float(sl) - entry) / entry * 100
        tp_dist = abs(float(tp) - entry) / entry * 100
        # 1. 盈亏比<2 → 自动修正TP到至少2:1 (反转策略胜率40-55%, 必须盈亏比≥2才盈利)
        #    原因: AI方向对但TP设太近(0.8-1%)扣费后必亏——反转波段有1.5-3%空间
        if tp_dist < sl_dist * 2:
            fixed_tp = entry + (entry - float(sl)) * 2 if is_buy else entry - (float(sl) - entry) * 2
            logger.info("修正 %s: 盈亏比 %.2f <2 → TP $%.2f→$%.2f (自动修正至2:1)",
                       inp.symbol, tp_dist / max(sl_dist, 1e-9), float(tp), fixed_tp)
            tp = fixed_tp
            tp_dist = abs(float(tp) - entry) / entry * 100
        # 2. 极端波动(>15%)禁止追涨/追跌 — 让AI自主判断正常波动(不再一刀切8%)
        if abs(inp.change_pct) > 15.0:
            logger.warning("拒绝 %s: 当日 %+.1f%% 极端波动, 禁止追涨/追跌 (等回踩/反抽)",
                          inp.symbol, inp.change_pct)
            return None
        # 3. 止损距离上限 (单笔风险控制, 防宽止损爆仓)
        if sl_dist > 3.0:
            logger.warning("拒绝 %s: 止损距离 %.2f%% 超限 (>3%%)",
                          inp.symbol, sl_dist)
            return None
        # 4. 紧止损修正: ≤0.6% 必被日内噪音扫掉 (5m ATR常0.5-0.7%)
        #    自动放宽到 1.2% (结构位+ATR经验值), 防止'止损被扫后价格回原方向'
        if sl_dist <= 0.6:
            fixed_sl = entry * (1 - 0.012) if is_buy else entry * (1 + 0.012)
            logger.info("修正 %s: 紧止损 %.2f%% <0.5%% (必被噪音扫) → SL $%.2f→$%.2f (1.2%%)",
                       inp.symbol, sl_dist, float(sl), fixed_sl)
            sl = fixed_sl
            sl_dist = abs(float(sl) - entry) / entry * 100

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
