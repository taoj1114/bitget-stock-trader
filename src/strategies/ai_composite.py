"""AI 综合决策策略 — 动态因子 + DeepSeek v4-pro

与静态快照不同，本策略使用 FeaturePipeline 计算 9 组时序因子，
让 AI 看到数据的**变化方向和加速度**，而非单一数值。

因子组:
    price_momentum / price_deviation / bb_features / atr_features
    volume_features / rsi_trajectory / macd_trajectory
    sentiment_trajectory / fundamental_momentum
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

import httpx

from src.core.interfaces import SignalStrategy
from src.core.types import AiCompositeParams, AnalysisContext, Signal

logger = logging.getLogger(__name__)


class AICompositeStrategy(SignalStrategy):
    """AI 综合决策策略 — 动态因子版"""

    _ID = "ai_composite"
    _NAME = "AI 综合决策"

    DEFAULT_BASE_URL = "https://opencode.ai/zen/go/v1"
    DEFAULT_MODEL = "deepseek-v4-pro"
    FLASH_MODEL = "deepseek-v4-flash"
    TEMPERATURE = 0.3
    FLASH_TEMPERATURE = 0.1
    MAX_TOKENS = 1000
    FLASH_MAX_TOKENS = 300
    REQUEST_TIMEOUT = 30.0
    FLASH_TIMEOUT = 15.0

    VALID_ACTIONS = {"STRONG_BUY", "BUY", "HOLD", "SELL", "STRONG_SELL"}

    def __init__(self, api_key: str = "", base_url: str = DEFAULT_BASE_URL,
                 model: str = DEFAULT_MODEL) -> None:
        self._api_key = api_key or os.environ.get("OPENCODE_API_KEY", "")
        self._base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self._model = model or self.DEFAULT_MODEL
        self._params = AiCompositeParams(model=self._model, min_confidence=0.2)
        self._pipeline = None

    def set_pipeline(self, pipeline):
        """注入 FeaturePipeline（避免循环依赖）。"""
        self._pipeline = pipeline

    # ═══ 输入数据校验 ══════════════════════

    @staticmethod
    def _sanitize_input(ctx: AnalysisContext) -> Optional[AnalysisContext]:
        if not ctx.quote or ctx.quote.mark_price <= 0:
            return None
        if not ctx.klines or len(ctx.klines) < 5:
            return None
        ind = ctx.indicators
        if ind and ind.ma10 == 0 and ind.rsi14 == 50.0:
            pass
        if not ctx.market_regime or not ctx.market_regime.regime:
            from src.core.types import MarketRegime
            ctx.market_regime = MarketRegime(regime="range_bound", volatility="normal")
        return ctx

    # ═══ SignalStrategy 接口 ═══════════════════

    @property
    def id(self) -> str:
        return self._ID

    @property
    def name(self) -> str:
        return self._NAME

    @property
    def params(self) -> AiCompositeParams:
        return self._params

    @params.setter
    def params(self, p: AiCompositeParams) -> None:
        self._params = p
        if p.model:
            self._model = p.model

    async def evaluate(self, ctx: AnalysisContext,
                       ind_4h=None, ind_1d=None,
                       bench_quotes: dict = None) -> Optional[Signal]:
        if not self._api_key:
            return None

        ctx = self._sanitize_input(ctx)
        if ctx is None:
            return None

        # 扩展数据已禁用（避免信号矛盾）
        extended = None

        factors = None
        if self._pipeline:
            try:
                factors = self._pipeline.compute_one(ctx.symbol)
            except Exception:
                pass

        if not factors or factors.get("_kline_end_ts", 0) == 0:
            prompt = self._build_static_prompt(ctx)
            raw = await self._call_api(prompt)
            if not raw:
                return None
            result = self._parse_response(raw)
            return self._response_to_signal(result, ctx) if result else None

        # Stage 1: Flash 并行
        news_digest, fund_digest = await asyncio.gather(
            self._summarize_news(ctx),
            self._assess_fundamentals(ctx),
        )

        # Stage 2: Pro 决策
        prompt = self._build_dynamic_prompt(ctx, factors, news_digest, fund_digest,
                                            extended=extended,
                                            ind_4h=ind_4h, ind_1d=ind_1d,
                                            bench_quotes=bench_quotes)
        raw = await self._call_api(prompt)
        if not raw:
            return None

        result = self._parse_response(raw)
        return self._response_to_signal(result, ctx) if result else None

    # ═══ Flash 数据提取 ═══════════════

    async def _summarize_news(self, ctx: AnalysisContext) -> str:
        if not ctx.news:
            return "无新闻数据"
        news_text = "\n".join(
            f"- [{item.source}] {item.title}"
            for item in ctx.news[:10]
        )
        prompt = (
            "你是一个金融数据提取器。分析以下新闻标题，用 2-3 句中文总结：\n"
            "1) 整体情绪方向（积极/消极/中性）和变化趋势\n"
            "2) 对股价最可能影响的关键事件（如有）\n"
            "3) 需要注意的风险因素\n\n"
            f"新闻列表:\n{news_text}\n\n"
            "只输出中文摘要，不要 JSON，不要多余文字。"
        )
        result = await self._call_flash(prompt)
        return result.strip() if result else "新闻摘要不可用"

    async def _assess_fundamentals(self, ctx: AnalysisContext) -> str:
        fund = ctx.fundamentals
        if not fund:
            return "无基本面数据"
        prompt = (
            "你是一个财务数据分析器。根据以下指标，用 1-2 句中文总结基本面状况：\n"
            f"营收同比: {fund.revenue_yoy or 'N/A'}%\n"
            f"净利润同比: {fund.net_profit_yoy or 'N/A'}%\n"
            f"ROE: {fund.roe or 'N/A'}%\n"
            f"毛利率: {fund.gross_margin or 'N/A'}%\n"
            f"净利率: {fund.net_margin or 'N/A'}%\n"
            f"资产负债率: {fund.debt_ratio or 'N/A'}%\n"
            "判断：增长质量如何？是否有恶化迹象？\n"
            "只输出中文评估，不要 JSON。"
        )
        result = await self._call_flash(prompt)
        return result.strip() if result else "基本面评估不可用"

    async def _call_flash(self, prompt: str) -> str:
        return await self._call_model(prompt, model=self.FLASH_MODEL,
                                      temperature=self.FLASH_TEMPERATURE,
                                      max_tokens=self.FLASH_MAX_TOKENS,
                                      timeout=self.FLASH_TIMEOUT)

    async def _call_model(self, prompt: str, model: str, temperature: float,
                          max_tokens: int, timeout: float,
                          json_mode: bool = False) -> str:
        url = f"{self._base_url}/chat/completions"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self._api_key}"}
        system = "你是美股交易分析师。只输出 JSON。" if json_mode else "你是金融分析助手。请简洁准确地回复。"
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        for attempt in range(3 if json_mode else 2):
            if attempt > 0:
                await asyncio.sleep(2 if json_mode else 1)
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(url, headers=headers, json=payload)
                    if resp.status_code != 200:
                        continue
                    data = resp.json()
                    choices = data.get("choices", [])
                    if not choices:
                        continue
                    content = choices[0].get("message", {}).get("content", "")
                    if content:
                        return content
            except Exception:
                continue
        return ""

    # ═══ 动态 Prompt ═════════════════════════════

    @staticmethod
    def _fmt(val, default=0, suffix="") -> str:
        try:
            v = float(val)
            if v != v:
                return f"{default}{suffix}"
            if abs(v) > 1e6:
                return f"{v:.0f}{suffix} ⚠️异常"
            if isinstance(val, float) and abs(v) < 0.01 and v != 0:
                return f"{v:.4f}{suffix}"
            return f"{v:.2f}{suffix}"
        except (TypeError, ValueError):
            return f"{default}{suffix}"

    def _build_dynamic_prompt(self, ctx: AnalysisContext, factors: dict,
                              news_digest: str = "", fund_digest: str = "",
                              extended=None,
                              ind_4h=None, ind_1d=None,
                              bench_quotes: dict = None) -> str:
        q = ctx.quote
        change_pct = q.change_pct * 100
        mom = factors

        momentum_lines = [
            f"当前价: ${q.mark_price} | 24h涨跌: {change_pct:+.2f}%",
            f"1h动量: {mom.get('price_momentum_1h', 0):+.2f}% | "
            f"4h: {mom.get('price_momentum_4h', 0):+.2f}% | "
            f"1日: {mom.get('price_momentum_1d', 0):+.2f}% | "
            f"3日: {mom.get('price_momentum_3d', 0):+.2f}%",
        ]
        accel = mom.get("price_momentum_accel", 0)
        if accel > 0.5:
            momentum_lines.append("动量趋势: 加速上行 ↑↑")
        elif accel > 0:
            momentum_lines.append("动量趋势: 稳步上行 ↑")
        elif accel < -0.5:
            momentum_lines.append("动量趋势: 加速下行 ↓↓")
        elif accel < 0:
            momentum_lines.append("动量趋势: 稳步下行 ↓")

        dev_lines = [
            f"偏离MA10: {mom.get('price_dev_ma10_pct', 0):+.1f}% | "
            f"偏离MA30: {mom.get('price_dev_ma30_pct', 0):+.1f}%",
            f"均线排列: {mom.get('price_ma_alignment', '未知')}",
        ]

        rsi_now = mom.get("rsi_14", 0)
        rsi_d1 = mom.get("rsi_delta_1d", 0)
        rsi_d3 = mom.get("rsi_delta_3d", 0)
        rsi_lines = [
            f"RSI(14): {rsi_now} | 1日Δ: {rsi_d1:+.1f} | 3日Δ: {rsi_d3:+.1f}",
            f"轨迹: {mom.get('rsi_trajectory', 'N/A')} | 区域: {mom.get('rsi_zone', 'N/A')}",
        ]

        macd_lines = [
            f"MACD: {mom.get('macd_line', 0)} (信号: {mom.get('macd_signal', 0)})",
            f"柱状图: {mom.get('macd_hist', 0)} (Δ: {mom.get('macd_hist_change', 0):+.4f})",
            f"状态: {mom.get('macd_status', 'N/A')}",
        ]

        bb_lines = [
            f"价格在布林带 {mom.get('bb_position', 0):.0%} 位置 | "
            f"带宽: {mom.get('bb_width_pct', 0)}% | 趋势: {mom.get('bb_width_trend', 'N/A')}",
        ]

        vol_lines = [
            f"量比: {mom.get('vol_ratio', 1)}x均量 | "
            f"近5根量变化: {mom.get('vol_trend_5bar', 0):+.1f}% | "
            f"量价配合: {'✅' if mom.get('vol_price_confirmation') else '⚠️'}",
        ]

        atr_lines = [
            f"ATR(14): {mom.get('atr_14', 0)} ({mom.get('atr_pct', 0)}%价格) | "
            f"5日变化: {mom.get('atr_change_5d', 0):+.1f}%",
        ]

        if news_digest and news_digest not in ("无新闻数据", "新闻摘要不可用"):
            sent_lines = [f"📰 Flash 新闻摘要: {news_digest}"]
        else:
            sent_lines = [
                f"正面占比: {mom.get('sentiment_pos_ratio', 0):.0%} | "
                f"负面: {mom.get('sentiment_neg_ratio', 0):.0%}",
                f"日环比: {mom.get('sentiment_delta_1d', 0):+.0%} | "
                f"轨迹: {mom.get('sentiment_trajectory', '无数据')}",
            ]

        if fund_digest and fund_digest not in ("无基本面数据", "基本面评估不可用"):
            fund_lines = [f"📋 Flash 基本面评估: {fund_digest}"]
        else:
            fund_lines = [
                f"营收增速: {mom.get('fund_revenue_yoy', 0)}% | "
                f"增速变化: {mom.get('fund_rev_momentum', 0):+.1f}%",
                f"ROE: {mom.get('fund_roe', 0)}% ({mom.get('fund_quality', 'N/A')}) | "
                f"动量: {mom.get('fund_momentum_signal', '无数据')}",
            ]

        prompt = (
            f"你是一位美股量化交易员，不是分析师。你的职责是**交易**，不是观望。\n"
            f"分析 {ctx.symbol} 的以下多维动态数据，不仅看当前值，更要看**变化方向**。\n\n"
            f"## 📈 价格动量\n" + "\n".join(momentum_lines) + "\n\n"
            f"## 📊 技术指标轨迹\n"
            f"### 均线\n" + "\n".join(dev_lines) + "\n"
            f"### RSI 轨迹\n" + "\n".join(rsi_lines) + "\n"
            f"### MACD 轨迹\n" + "\n".join(macd_lines) + "\n"
            f"### 布林带\n" + "\n".join(bb_lines) + "\n"
            f"### 成交量\n" + "\n".join(vol_lines) + "\n"
            f"### 波动率\n" + "\n".join(atr_lines) + "\n\n"
            f"## 📰 新闻情绪轨迹\n" + "\n".join(sent_lines) + "\n\n"
            f"## 📋 基本面动量\n" + "\n".join(fund_lines) + "\n\n"
            "请基于数据的**趋势变化方向**综合判断，输出 JSON:\n"
            "{\n"
            '    "trend_judgment": "accelerating_up/up/sideways/down/accelerating_down",\n'
            '    "confidence": 0.0-1.0,\n'
            '    "action": "STRONG_BUY/BUY/HOLD/SELL/STRONG_SELL",\n'
            '    "reason": "中文理由（必须引用具体数据变化）",\n'
            '    "key_levels": {"support": x, "resistance": x},\n'
            '    "stop_loss": x, "take_profit": x,\n'
            '    "risk_warning": "风险提示"\\n'
            "}\\n\\n"
            "🌍 市场背景：\\n"
        )

        # 大盘基准
        if bench_quotes:
            for name, bq in bench_quotes.items():
                prompt += f"  {name} ${bq.mark_price:.2f} ({bq.change_pct*100:+.1f}%)\\n"

        # OI + 费率
        if q.open_interest > 0:
            prompt += f"  持仓量: {q.open_interest:.0f} | 24h量: {q.volume_24h:.0f}\\n"
        if hasattr(q, 'funding_rate') and q.funding_rate:
            prompt += f"  资金费率: {q.funding_rate*100:.4f}% ('+'=多头付空头, '-'=空头付多头)\\n"
        prompt += "\\n"

        prompt += (
            "📊 多周期趋势确认：\\n"
        )

        if ind_4h:
            prompt += "  4H | MA10={:.1f} MA30={:.1f} RSI={:.0f} MACD={:.3f}\\n".format(
                ind_4h.ma10, ind_4h.ma30, ind_4h.rsi14, ind_4h.macd)
        if ind_1d:
            prompt += "  日线| MA10={:.1f} MA30={:.1f} RSI={:.0f} MACD={:.3f}\\n".format(
                ind_1d.ma10, ind_1d.ma30, ind_1d.rsi14, ind_1d.macd)
        prompt += "  1H/4H/日线方向一致→高概率趋势 | 不一致→震荡\\n"

        prompt += (
            "\\n"
            "⚡ 交易纪律（你必须遵守）：\\n"
            "1. 趋势明朗时必须行动：accelerating_up → STRONG_BUY, accelerating_down → STRONG_SELL\n"
            "2. HOLD 仅用于真正不确定的情况（震荡/矛盾信号），不要作为默认选项\n"
            "3. strong trend (ADX>30) + 同向动量 → confidence 0.7-0.9，不要低于 0.6\n"
            "4. 顺势做空不需要完美的基本面恶化，技术面趋势下行就足够\n"
            "5. 宁可小亏止损，不可错过明确的趋势机会\n"
            "6. 你的回答中 BUY/SELL 必须多于 HOLD，不要害怕交易\n"
            "7. confidence 0.3-0.9：有明确趋势给 0.6+，不确定给 0.3-0.5，但必须给出方向\n"
        )

        # 扩展数据已禁用

        return prompt

    # ═══ 静态 Prompt（降级） ═══════════════

    def _build_static_prompt(self, ctx: AnalysisContext) -> str:
        q = ctx.quote
        ind = ctx.indicators
        change_pct = q.change_pct * 100
        lines = [
            f"分析 {ctx.symbol} 的当前数据：",
            f"价格: ${q.mark_price} (24h {change_pct:+.2f}%)",
            f"MA10: {ind.ma10} MA30: {ind.ma30}",
            f"RSI(14): {ind.rsi14:.1f}",
            f"MACD: {ind.macd} 信号: {ind.macd_signal}",
            f"布林带: 上{ind.bb_upper} 中{ind.bb_middle} 下{ind.bb_lower}",
            f"ATR(14): {ind.atr14} 成交量: {ind.volume_ratio}x均量",
            f"市场状态: {ctx.market_regime.regime} ADX: {ctx.market_regime.adx}",
            "⚠️ 历史数据不足，无时序趋势信息",
            "",
            "请输出 JSON:",
            '{"trend_judgment":"...","confidence":0.8,"action":"...","reason":"...",'
            '"key_levels":{"support":0,"resistance":0},"risk_warning":"..."}',
        ]
        return "\n".join(lines)

    # ═══ API 调用 ═══════════════

    async def _call_api(self, prompt: str) -> str:
        return await self._call_model(prompt, model=self._model,
                                      temperature=self.TEMPERATURE,
                                      max_tokens=self.MAX_TOKENS,
                                      timeout=self.REQUEST_TIMEOUT,
                                      json_mode=True)

    def _parse_response(self, raw: str) -> Optional[dict]:
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        if fenced:
            try:
                return json.loads(fenced.group(1))
            except (json.JSONDecodeError, TypeError):
                pass
        blob = re.search(r"\{.*", raw, re.DOTALL)
        if blob:
            partial = blob.group(0)
            try:
                return json.JSONDecoder(strict=False).raw_decode(partial)[0]
            except Exception:
                pass
        return None

    def _validate_response(self, result: dict) -> Optional[dict]:
        if not isinstance(result, dict):
            return None
        if not result:
            return None
        required = {"action", "confidence", "reason"}
        missing = required - set(result.keys())
        if missing:
            return None
        action = str(result.get("action", "")).upper().strip()
        if action not in self.VALID_ACTIONS:
            return None
        confidence = self._to_float(result.get("confidence"))
        if confidence is None:
            return None
        confidence = max(0.0, min(1.0, confidence))
        result["action"] = action
        result["confidence"] = confidence
        return result

    @staticmethod
    def _to_float(val) -> Optional[float]:
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    def _response_to_signal(self, result: dict, ctx: AnalysisContext) -> Optional[Signal]:
        result = self._validate_response(result)
        if result is None:
            return None

        action = result.get("action", "HOLD")
        confidence = result.get("confidence", 0)

        if action == "HOLD" or confidence < self._params.min_confidence:
            return None

        entry = ctx.quote.mark_price
        is_buy = action in ("BUY", "STRONG_BUY")

        # 只用 AI 给的 stop_loss/take_profit，不给就拒单
        ai_sl = result.get("stop_loss", 0)
        ai_tp = result.get("take_profit", 0)

        if not ai_sl or not ai_tp:
            logger.warning("AI 未提供 SL/TP: action=%s sl=%s tp=%s raw=%s",
                          action, ai_sl, ai_tp, str(result)[:200])
            return None

        if is_buy:
            if ai_sl >= entry or ai_tp <= entry:
                logger.warning("AI SL/TP 方向错误 (BUY: SL=%s TP=%s)", ai_sl, ai_tp)
                return None
        else:
            if ai_sl <= entry or ai_tp >= entry:
                logger.warning("AI SL/TP 方向错误 (SELL: SL=%s TP=%s)", ai_sl, ai_tp)
                return None

        return Signal(
            strategy_id=self.id, symbol=ctx.symbol,
            action=action, confidence=confidence,
            entry_price=entry, stop_loss=sl,
            take_profits=[tp],
            reason=result.get("reason", "")[:500],
        )
