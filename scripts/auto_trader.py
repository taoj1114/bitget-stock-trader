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
from src.trading.paper_executor import PaperExecutor
from src.trading.safety import SafetySystem
from src.trading.slippage import SlippageModel
from src.storage.kline_store import KlineStore
from src.storage.fund_store import FundStore
from src.storage.news_sentiment_store import NewsSentimentStore
from src.features.pipeline import FeaturePipeline
from src.optimization.population import ParamPopulation
from src.core.types import AnalysisContext, Kline, Signal

logger = logging.getLogger("autotrader")


# ── 宽松模式配置 ─────────────────────────────────

# ── 宽松模式配置（初筛广撒网，AI 做把关）──

FULL_SCAN_INTERVAL = 600     # 10 分钟全扫
QUOTE_INTERVAL = 30          # 秒
SYMBOL_REFRESH_INTERVAL = 14400
TOP_N_SYMBOLS = 25
BENCHMARK_SYMBOLS = ["SPY", "QQQ", "SOXX"]  # 大盘基准，不交易


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

        # 风控 + 滑点 + 执行器 (paper 或 real)
        safety = SafetySystem(config.safety)
        slippage = SlippageModel()
        mode = config.mode
        if mode == "real":
            from src.trading.real_executor import RealExecutor
            self._executor = RealExecutor()
            if not self._executor.ready:
                logger.warning("Bitget API 未配置，降级为纸盘")
                self._executor = PaperExecutor(initial_capital=10000, safety=safety, slippage=slippage)
                mode = "paper"
        else:
            self._executor = PaperExecutor(initial_capital=10000, safety=safety, slippage=slippage)
        logger.info("执行器: %s", mode.upper())

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

        # 5. 第一遍：规则策略扫全品种，收集所有信号
        all_rule_signals: list[tuple[str, Signal, dict]] = []  # (symbol, signal, params)
        for symbol in symbols_to_scan:
            price = quotes.get(symbol, 0)
            if price <= 0:
                continue
            try:
                sigs = await self._scan_symbol_v2(symbol, price, quotes)
                if sigs:
                    all_rule_signals.extend((symbol, s, p) for s, p in sigs)
            except Exception as e:
                logger.error("扫描 %s 失败: %s", symbol, e)

        # 6. AI 管仓：每轮评估所有持仓是否需要调整
        positions = await self._executor.get_positions()
        if positions:
            logger.info("AI 管仓: %d 个持仓", len(positions))
            for pos in positions:
                news = []
                try:
                    news = await self._news_registry.fetch_news(pos.symbol, max_results=5)
                except Exception: pass
                klines = await self._market.get_klines(pos.symbol, "1H", 100)
                quote = await self._market.get_quote(pos.symbol)
                if not quote or len(klines) < 30:
                    continue
                ind = self._tech.calculate(klines)
                regime = self._regime_detector.detect(klines)
                ctx = AnalysisContext(symbol=pos.symbol, quote=quote, klines=klines,
                                      indicators=ind, fundamentals=None, news=news,
                                      market_regime=regime)
                try:
                    ai_sig = await self._ai_strategy.evaluate(ctx)
                    if ai_sig:
                        # AI 建议平仓：多头看到 SELL → 平仓；空头看到 BUY → 平仓
                        if (ai_sig.action in ("SELL","STRONG_SELL") and pos.side == "LONG") or \
                           (ai_sig.action in ("BUY","STRONG_BUY") and pos.side == "SHORT"):
                            logger.info("%s %s → AI 建议平仓", pos.symbol, pos.side)
                            await self._executor.close_position(pos.id, "AI_CLOSE")
                except Exception: pass

        # 7. 有空位 → 按信号质量排序，全部送 AI 验证开新仓
        all_rule_signals.sort(key=lambda x: x[1].confidence, reverse=True)
        seen_symbols: set[str] = set()

        for symbol, signal, params in all_rule_signals:
            if symbol in seen_symbols:
                continue
            seen_symbols.add(symbol)
            await self._validate_with_ai(symbol, signal, params)

            # 进化检查
            self._scan_count += 1
            if self._scan_count >= self._evo_interval:
                await self._evolve_populations()
                self._scan_count = 0

        # 6. 保存状态
        self._executor._save_state()
        self._last_prices.update(quotes)

        # 7. 输出摘要
        positions = await self._executor.get_positions()
        balance = await self._executor.get_balance()
        equity = await self._executor.get_equity()
        logger.info(
            "扫描完成 | 持仓 %d | 净值 $%.0f (余额 $%.0f) | PnL $%.0f | 胜率 %d/%d",
            len(positions), equity, balance.current_balance, balance.total_pnl,
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
            elif abs(price / last - 1) >= 0.002:
                triggered.append(sym)
        return triggered

if __name__ == "__main__":
    asyncio.run(main())
