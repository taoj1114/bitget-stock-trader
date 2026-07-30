#!/usr/bin/env python3
"""实时价格驱动自动交易引擎

架构:
    Bitget 定时轮询(30s) → 价格变动>阈值 → 宽松因子计算 → 策略评估 → 执行

模式:
    --once     单次扫描（cron 用）
    --live     持续运行（默认 30s 间隔）
    --interval 自定义扫描间隔（秒）

宽松指标:
    降低置信度门槛(0.5→0.4)、减少确认条件、加速反应
"""

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config.loader import get_config
from src.datasources.bitget.market import BitgetMarketSource
from src.datasources.bitget.symbols import BitgetSymbolSource
from src.analyzers.technical import TechnicalAnalyzer
from src.analyzers.market_regime import MarketRegimeDetector
from src.strategies.rsi_bounce import RsiBounceStrategy
from src.strategies.momentum_chase import MomentumChaseStrategy
from src.strategies.volume_price import VolumePriceStrategy
from src.strategies.price_action import (
    PriceActionVCPStrategy,
    PriceActionSupportStrategy,
    PriceActionFakeyStrategy,
)
from src.strategies.ai_composite import AICompositeStrategy
from src.strategies.convergence import ConvergenceDetector
from src.signals.aggregator import SignalAggregator
from src.trading.paper_executor import PaperExecutor
from src.trading.safety import SafetySystem
from src.trading.slippage import SlippageModel
from src.storage.kline_store import KlineStore
from src.storage.fund_store import FundStore
from src.storage.news_sentiment_store import NewsSentimentStore
from src.features.pipeline import FeaturePipeline
from src.optimization.population import ParamPopulation
from src.core.types import AnalysisContext, Signal

logger = logging.getLogger("autotrader")


# ── 宽松模式配置 ─────────────────────────────────

# ── 宽松模式配置（初筛广撒网，AI 做把关）──

LOOSE_CONFIDENCE = 0.3       # 初筛置信度（AI门槛 0.6）
LOOSE_COOLDOWN_BARS = 8      # 快速冷却（不阻塞下一次触发）
PRICE_CHANGE_THRESHOLD = 0.002  # 0.2% 变动即触发
FULL_SCAN_INTERVAL = 600     # 10 分钟全扫
QUOTE_INTERVAL = 30          # 秒
SYMBOL_REFRESH_INTERVAL = 14400
TOP_N_SYMBOLS = 25


class AutoTrader:
    """实时价格驱动交易引擎。"""

    def __init__(self):
        config = get_config()
        self._symbols = config.symbols
        self._market = BitgetMarketSource()
        self._symbol_source = BitgetSymbolSource()

        # 存储
        self._kline_store = KlineStore()
        self._fund_store = FundStore()
        self._news_store = NewsSentimentStore()

        # 因子管线
        self._pipeline = FeaturePipeline(self._kline_store, self._fund_store, self._news_store)

        # 风控 + 滑点 + 执行器
        safety = SafetySystem(config.safety)
        slippage = SlippageModel()
        self._executor = PaperExecutor(initial_capital=10000, safety=safety, slippage=slippage)

        # 策略 — 规则策略用参数种群，AI 用单实例
        self._ai_strategy = AICompositeStrategy(api_key=config.deepseek.get("api_key", ""))
        self._ai_strategy.set_pipeline(self._pipeline)

        # 参数种群: 6 种策略（去重后）各 2-3 组参数并行竞赛
        # 初筛广撒网 → AI 做最终把关
        from src.optimization.population import ParamPopulation
        self._populations = {
            "momentum_chase": ParamPopulation("momentum_chase", population_size=2),
            "rsi_bounce": ParamPopulation("rsi_bounce", population_size=2),
            "volume_price": ParamPopulation("volume_price", population_size=2),
            "price_action_vcp": ParamPopulation("price_action", population_size=2),
            "price_action_support": ParamPopulation("price_action", population_size=2),
            "price_action_fakey": ParamPopulation("price_action", population_size=2),
        }
        self._pop_perf: dict[str, list[dict]] = {k: [] for k in self._populations}

        # 进化计数器
        self._scan_count = 0
        self._evo_interval = 30

        # 信号处理
        self._convergence = ConvergenceDetector()
        self._aggregator = SignalAggregator()
        self._tech = TechnicalAnalyzer()
        self._regime_detector = MarketRegimeDetector()

        # 新闻源注册
        from src.datasources.news.yahoo import YahooNewsSource
        from src.datasources.news.searxng import SearXNGNewsSource
        from src.datasources.news.finnhub import FinnhubNewsSource
        from src.datasources.news.registry import NewsRegistry
        self._news_registry = NewsRegistry(
            primary_name=config.news_sources.get("primary", "yahoo"),
            fallback_name=config.news_sources.get("fallback", "searxng"),
        )
        self._news_registry.register(YahooNewsSource())
        self._news_registry.register(SearXNGNewsSource(
            base_url=config.searxng_base_url, timeout=config.searxng_timeout))
        finnhub_token = config.news_sources.get("finnhub", {}).get("token", "")
        if finnhub_token:
            self._news_registry.register(FinnhubNewsSource(api_key=finnhub_token))

        # 价格记忆（用于变动检测）
        self._last_prices: dict[str, float] = {}
        self._last_full_scan = 0.0
        self._last_symbol_refresh = 0.0    # 热度排名刷新计时器

    # ═══ 主循环 ═══════════════════════════════════

    async def run_once(self):
        """单次扫描：获取行情 → 检查持仓 → 分析 → 执行。"""
        logger.info("=== AutoTrader 单次扫描 ===")

        # 1. 获取所有品种报价（含成交量用于排序）
        quotes = await self._fetch_all_quotes()
        rich_quotes = await self._fetch_rich_quotes()
        if not quotes:
            logger.warning("无法获取报价，跳过")
            return

        # 2. 检查现有持仓止盈止损
        tick_results = await self._executor.tick(quotes)
        for r in tick_results:
            logger.info("触发: %s", r.reason)

        # 2.5 定期刷新热度排名
        now = time.time()
        if now - self._last_symbol_refresh >= SYMBOL_REFRESH_INTERVAL:
            await self._refresh_symbols()
            self._last_symbol_refresh = now

        # 3. 筛选 + 按热度排序
        if now - self._last_full_scan >= FULL_SCAN_INTERVAL:
            candidates = list(self._symbols)
            self._last_full_scan = now
            logger.info("全品种扫描 (%d)", len(candidates))
        else:
            candidates = self._filter_by_price_change(quotes)
            if not candidates:
                logger.debug("无品种触发变动阈值")
                return

        # 按热度排序：成交量大的、波动大的排前面
        symbols_to_scan = self._rank_symbols(candidates, rich_quotes)

        # AI 按需分配：有规则信号的品种才调 AI，最多 5 次/轮
        ai_budget = 5
        logger.info("扫描 %d 品种 | AI 预算 %d 次", len(symbols_to_scan), ai_budget)

        # 4. 宽松模式
        self._apply_loose_mode()

        # 5. 逐品种分析
        for symbol in symbols_to_scan:
            price = quotes.get(symbol, 0)
            if price <= 0:
                continue

            try:
                used = await self._scan_symbol(symbol, price, quotes, ai_budget > 0)
                if used:
                    ai_budget -= 1
            except Exception as e:
                logger.error("扫描 %s 失败: %s", symbol, e)

        # 6. 保存状态
        self._executor._save_state()
        self._last_prices.update(quotes)

        # 7. 输出摘要
        positions = await self._executor.get_positions()
        balance = await self._executor.get_balance()
        logger.info(
            "扫描完成 | 持仓 %d | 余额 $%.0f | PnL $%.0f | 胜率 %d/%d",
            len(positions), balance.current_balance, balance.total_pnl,
            balance.win_count, balance.total_trades,
        )

    async def run_live(self, interval: int = QUOTE_INTERVAL):
        """持续运行模式。"""
        logger.info("AutoTrader 启动 | 间隔 %ds | 品种 %d", interval, len(self._symbols))
        while True:
            await self.run_once()
            await asyncio.sleep(interval)

    # ═══ 内部方法 ═══════════════════════════════════

    async def _fetch_all_quotes(self) -> dict[str, float]:
        """获取所有品种的 mark_price。"""
        quotes = {}
        for sym in self._symbols:
            try:
                q = await self._market.get_quote(sym)
                if q and q.mark_price > 0:
                    quotes[sym] = q.mark_price
            except Exception:
                pass
        return quotes

    async def _fetch_rich_quotes(self) -> dict[str, dict]:
        """获取所有品种的完整报价（含成交量、涨跌幅），用于优先级排序。"""
        rich = {}
        for sym in self._symbols:
            try:
                q = await self._market.get_quote(sym)
                if q and q.mark_price > 0:
                    rich[sym] = {
                        "mark_price": q.mark_price,
                        "volume_24h": q.volume_24h or 0,
                        "change_pct": q.change_pct or 0,
                        "turnover_24h": q.turnover_24h or 0,
                    }
            except Exception:
                pass
        return rich

    def _rank_symbols(self, symbols: list[str], rich_quotes: dict[str, dict]) -> list[str]:
        """按交易热度排序：成交量(40%) + 涨跌幅度(30%) + 成交额(30%)。

        热度高的品种优先被 AI 分析。
        """
        if not rich_quotes:
            return symbols

        # 归一化各指标到 0-1
        volumes = [rich_quotes.get(s, {}).get("volume_24h", 0) for s in symbols]
        turnovers = [rich_quotes.get(s, {}).get("turnover_24h", 0) for s in symbols]
        changes = [abs(rich_quotes.get(s, {}).get("change_pct", 0)) for s in symbols]

        max_vol = max(volumes) if volumes else 1
        max_to = max(turnovers) if turnovers else 1
        max_chg = max(changes) if changes else 1

        scores = {}
        for i, sym in enumerate(symbols):
            vol_score = volumes[i] / max_vol if max_vol > 0 else 0
            to_score = turnovers[i] / max_to if max_to > 0 else 0
            chg_score = changes[i] / max_chg if max_chg > 0 else 0
            scores[sym] = vol_score * 0.4 + chg_score * 0.3 + to_score * 0.3

        return sorted(symbols, key=lambda s: scores.get(s, 0), reverse=True)

    async def _refresh_symbols(self):
        """从现有品种中刷新热度排名（排序，不完全替换）。"""
        logger.info("刷新热度排名...")
        try:
            # 保持原品种，获取最新行情用于重新排序
            rich = {}
            for sym in self._symbols[:20]:  # 快速采样
                try:
                    q = await self._market.get_quote(sym)
                    if q:
                        rich[sym] = {
                            "volume_24h": q.volume_24h,
                            "change_pct": abs(q.change_pct),
                            "turnover_24h": q.turnover_24h,
                        }
                except Exception:
                    pass

            if len(rich) >= 5:
                self._symbols = self._rank_symbols(list(rich.keys()), rich)
                logger.info("热度排名刷新: %d 个品种", len(self._symbols))
        except Exception as e:
            logger.error("刷新热度排名失败: %s", e)

    def _filter_by_price_change(self, quotes: dict[str, float]) -> list[str]:
        """筛选价格变动超过阈值的品种。"""
        triggered = []
        for sym, price in quotes.items():
            last = self._last_prices.get(sym)
            if last is None:
                triggered.append(sym)  # 新品种，首次扫描
            elif abs(price / last - 1) >= PRICE_CHANGE_THRESHOLD:
                triggered.append(sym)
        return triggered

    def _apply_loose_mode(self):
        """宽松模式：降低策略阈值，加速反应。"""
        # trend_break 种群缩短冷却
        if "trend_break" in self._populations:
            for params in self._populations["trend_break"].population:
                params["cooldown_bars"] = min(params.get("cooldown_bars", 48), LOOSE_COOLDOWN_BARS)
        if self._ai_strategy.params:
            self._ai_strategy.params.min_confidence = LOOSE_CONFIDENCE

    async def _scan_symbol(self, symbol: str, price: float, quotes: dict,
                           use_ai: bool = False) -> bool:
        """规则策略先跑 → 有信号才调 AI 验证 → AI 确认后才执行。

        旧流程: 规则 + AI 各自跑 → 聚合
        新流程: 规则生成候选 → AI 验证确认/拒绝/调整 → 执行
        """
        klines = await self._market.get_klines(symbol, "1H", 100)
        if len(klines) < 30:
            return

        rows = [
            {"timestamp": k.timestamp, "open": k.open, "high": k.high,
             "low": k.low, "close": k.close, "volume": k.volume, "turnover": k.turnover}
            for k in klines
        ]
        self._kline_store.upsert_batch(symbol, rows)

        quote = await self._market.get_quote(symbol)
        if not quote:
            return

        ind = self._tech.calculate(klines)
        regime = self._regime_detector.detect(klines)

        # ── Step 1: 规则策略先跑（快速，无需新闻/AI）──
        ctx = AnalysisContext(
            symbol=symbol, quote=quote, klines=klines,
            indicators=ind, fundamentals=None, news=[],
            market_regime=regime,
        )

        rule_signals: list[Signal] = []
        for sid, pop in self._populations.items():
            best_sig = None
            best_conf = 0
            best_params = {}
            for params in pop.population:
                strategy = pop.create_strategy(params)
                if not strategy:
                    continue
                try:
                    sig = await strategy.evaluate(ctx)
                    if sig and sig.confidence > best_conf:
                        best_sig = sig
                        best_conf = sig.confidence
                        best_params = dict(params)
                except Exception:
                    continue
            if best_sig:
                best_sig.generating_params = best_params  # 记录参数
                rule_signals.append(best_sig)
                self._pop_perf[sid].append({"params": best_params, "signal": best_conf})

        if not rule_signals:
            return False  # 无规则信号 → 跳过 AI

        # ── Step 2: 有信号 → 拉新闻 + 调 AI 验证 ──
        if not use_ai:
            # 无 AI → 直接用规则信号
            best = max(rule_signals, key=lambda s: s.confidence)
            logger.info("%s: %s conf=%.2f (仅规则)", symbol, best.action, best.confidence)
            exec_result = await self._executor.execute_signal(best)
            if exec_result.status == "FILLED":
                logger.info("  → 开仓: %s @ $%.2f qty=%.1f", best.action, exec_result.fill_price, exec_result.fill_quantity)
            return False

        # 拉新闻
        news_items = []
        try:
            news_items = await self._news_registry.fetch_news(symbol, max_results=10, merge_all=False)
        except Exception:
            pass

        # 构造 AI 验证上下文（含规则信号）
        best_rule = max(rule_signals, key=lambda s: s.confidence)
        ctx_with_news = AnalysisContext(
            symbol=symbol, quote=quote, klines=klines,
            indicators=ind, fundamentals=None, news=news_items,
            market_regime=regime,
        )

        # AI 验证
        try:
            ai_sig = await self._ai_strategy.evaluate(ctx_with_news)
        except Exception:
            ai_sig = None

        # ── Step 3: AI 确认/调整 → 执行 ──
        if ai_sig:
            logger.info("%s: 规则=%s(%.2f) → AI=%s(%.2f)", symbol,
                       best_rule.action, best_rule.confidence,
                       ai_sig.action, ai_sig.confidence)
            exec_result = await self._executor.execute_signal(ai_sig)
        else:
            logger.info("%s: 规则=%s(%.2f) → AI 否决", symbol,
                       best_rule.action, best_rule.confidence)
            return True  # AI 被调用了（否决也是用了）

        if exec_result.status == "FILLED":
            logger.info("  → 开仓: %s @ $%.2f qty=%.1f", ai_sig.action, exec_result.fill_price, exec_result.fill_quantity)
        else:
            logger.info("  → 被拦截: %s", exec_result.reason)

        # ── 进化检查 ──
        self._scan_count += 1
        if self._scan_count >= self._evo_interval:
            await self._evolve_populations()
            self._scan_count = 0

        return True  # AI 被调用了

    async def _evolve_populations(self):
        """触发种群进化：从真实交易数据计算每参数组的绩效 → AI 建议 → 下一代。"""
        import json as _json

        # 汇总所有已平仓交易的盈亏（按 strategy_id + params 分组）
        all_closed_trades = self._executor._tracker.get_closed_trades(limit=1000)
        if len(all_closed_trades) < 5:
            return

        # 按 (strategy_id, params_key) 分组
        groups: dict[tuple[str, str], dict] = {}
        for t in all_closed_trades:
            sid = t.get("strategy_id", "")
            if sid not in self._populations:
                continue
            # 从仓位找参数（position_id → params）
            pos_id = t.get("position_id", "")
            params = {}
            if pos_id in self._executor._positions:
                params = self._executor._positions[pos_id].generating_params

            key = (sid, _json.dumps(params, sort_keys=True) if params else "_default")
            if key not in groups:
                groups[key] = {"strategy_id": sid, "params": params,
                               "pnl": 0, "wins": 0, "losses": 0, "trades": 0}
            g = groups[key]
            pnl = t.get("pnl", 0)
            g["pnl"] += pnl
            g["trades"] += 1
            if pnl > 0: g["wins"] += 1
            elif pnl < 0: g["losses"] += 1

        # 按 strategy_id 重新分组
        from collections import defaultdict
        by_sid: dict[str, list[dict]] = defaultdict(list)
        for key, g in groups.items():
            sid = key[0]
            if g["trades"] == 0: continue
            win_rate = g["wins"] / g["trades"]
            pnl_score = max(0, g["pnl"] + 500) / 500 * 60
            wr_score = win_rate * 30
            vol_score = min(10, g["trades"])
            by_sid[sid].append({
                "params": g["params"], "score": pnl_score + wr_score + vol_score,
                "win_rate": win_rate, "pnl": g["pnl"], "trades": g["trades"],
            })

        for sid, pop in self._populations.items():
            scored = sorted(by_sid.get(sid, []), key=lambda x: x["score"], reverse=True)
            if len(scored) < 3: continue

            logger.info("种群进化: %s (%d组参数, %d笔交易)", sid, len(scored),
                        sum(s["trades"] for s in scored))
            ai_variants = []
            try:
                from src.optimization.population import ParamPopulation as PP
                temp_pop = PP(sid, pop.size)
                temp_pop.performance = scored
                temp_pop.generation = pop.generation
                ai_variants = await self._call_ai_for_params(temp_pop.build_ai_prompt())
            except Exception: pass

            pop.evolve(scored, ai_variants or None)
            logger.info("  → 第%d代: %d组新参数", pop.generation, len(pop.population))

    async def _call_ai_for_params(self, prompt: str) -> list[dict]:
        """调用 AI 生成新参数（复用 AI 策略的 API）。"""
        api_key = get_config().deepseek.get("api_key", "")
        if not api_key:
            return []
        try:
            import httpx, json, re
            url = "https://opencode.ai/zen/go/v1/chat/completions"
            payload = {
                "model": "deepseek-v4-flash",
                "messages": [
                    {"role": "system", "content": "你是量化策略优化专家。只输出 JSON 数组。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.3, "max_tokens": 500,
            }
            headers = {"Authorization": f"Bearer {api_key}"}
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    match = re.search(r"\[.*\]", content, re.DOTALL)
                    if match:
                        return json.loads(match.group(0))[:2]
        except Exception:
            pass
        return []


# ── 入口 ───────────────────────────────────────────

async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    args = sys.argv[1:]
    mode = "once"
    interval = QUOTE_INTERVAL

    for i, arg in enumerate(args):
        if arg == "--live":
            mode = "live"
        elif arg == "--interval" and i + 1 < len(args):
            interval = int(args[i + 1])
        elif arg == "--once":
            mode = "once"

    trader = AutoTrader()

    if mode == "live":
        await trader.run_live(interval)
    else:
        await trader.run_once()


if __name__ == "__main__":
    asyncio.run(main())
