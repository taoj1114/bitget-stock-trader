#!/usr/bin/env python3
"""AI 原生自动交易引擎 — 无规则策略，纯 AI 驱动

架构: Bitget 定时轮询 → AI(规则初筛→Pro深度分析) → 执行
模式: --live 持续运行 / --once 单次扫描
"""

import asyncio, json, logging, os, sys, time
from datetime import datetime, timezone

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
from src.core.types import Kline
from src.strategies.ai_native import AINativeDecisionMaker, AIInput

logger = logging.getLogger("autotrader")

FULL_SCAN_INTERVAL = 600
QUOTE_INTERVAL = 30
SYMBOL_REFRESH_INTERVAL = 14400
TOP_N_SYMBOLS = 25
BENCHMARK_SYMBOLS = ["SPY", "QQQ", "SOXX"]


class AutoTrader:

    def __init__(self):
        config = get_config()
        self._symbols = config.symbols
        self._market = BitgetMarketSource()
        self._symbol_source = BitgetSymbolSource()

        self._kline_store = KlineStore()
        self._fund_store = FundStore()
        self._news_store = NewsSentimentStore()
        self._pipeline = FeaturePipeline(self._kline_store, self._fund_store, self._news_store)

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

        self._ai_decider = AINativeDecisionMaker()
        self._tech = TechnicalAnalyzer()
        self._regime_detector = MarketRegimeDetector()

        from src.datasources.news.yahoo import YahooNewsSource
        from src.datasources.news.searxng import SearXNGNewsSource
        from src.datasources.news.finnhub import FinnhubNewsSource
        from src.datasources.news.registry import NewsRegistry
        self._news_registry = NewsRegistry(
            primary_name=config.news_sources.get("primary", "yahoo"),
            fallback_name=config.news_sources.get("fallback", "searxng"),
        )
        self._news_registry.register(YahooNewsSource())
        self._news_registry.register(SearXNGNewsSource(base_url=config.searxng_base_url, timeout=config.searxng_timeout))
        finnhub_token = config.news_sources.get("finnhub", {}).get("token", "")
        if finnhub_token:
            self._news_registry.register(FinnhubNewsSource(api_key=finnhub_token))

        self._last_prices: dict[str, float] = {}
        self._last_full_scan = 0.0
        self._last_symbol_refresh = 0.0

    # ═══ 主循环 ═══════════════════════════════════

    async def run_once(self):
        logger.info("=== AutoTrader 单次扫描 ===")

        quotes = await self._fetch_all_quotes()
        rich_quotes = await self._fetch_rich_quotes()
        if not quotes:
            return

        await self._executor.tick(quotes)

        now = time.time()
        if now - self._last_symbol_refresh >= SYMBOL_REFRESH_INTERVAL:
            await self._refresh_symbols()
            self._last_symbol_refresh = now

        if now - self._last_full_scan >= FULL_SCAN_INTERVAL:
            candidates = list(self._symbols)
            self._last_full_scan = now
        else:
            candidates = self._filter_by_price_change(quotes)
            if not candidates:
                return

        from src.storage.kline_aggregator import KlineAggregator
        agg = KlineAggregator()

        # 大盘
        bench = {}
        for b in BENCHMARK_SYMBOLS:
            try:
                bq = await self._market.get_quote(b)
                if bq and bq.mark_price > 0:
                    bench[b] = bq.change_pct * 100
            except Exception: pass

        # ── AI 管仓 + 动态止盈止损 ──
        positions = await self._executor.get_positions()
        if positions:
            for pos in positions:
                try:
                    k_1h = await self._market.get_klines(pos.symbol, "1H", 500)
                    q = await self._market.get_quote(pos.symbol)
                    if not q or len(k_1h) < 30: continue
                    ind = self._tech.calculate(k_1h)
                    reg = self._regime_detector.detect(k_1h)
                    k_4h = [Kline(**r) for r in agg.aggregate(k_1h, "4H")]
                    k_1d = [Kline(**r) for r in agg.aggregate(k_1h, "1D")]
                    ind4 = self._tech.calculate(k_4h) if len(k_4h)>=5 else None
                    ind1d = self._tech.calculate(k_1d) if len(k_1d)>=3 else None
                    ai_inp = AIInput(
                        symbol=pos.symbol, mark_price=q.mark_price, change_pct=q.change_pct*100,
                        klines_1h=k_1h, klines_4h=k_4h, klines_1d=k_1d,
                        ind_1h=dict(rsi=ind.rsi14, ma10=ind.ma10, ma30=ind.ma30,
                                   macd=ind.macd, atr=ind.atr14, adx=reg.adx,
                                   regime=reg.regime, bb_position=0.5),
                        ind_4h=dict(rsi=ind4.rsi14) if ind4 else None,
                        ind_1d=dict(rsi=ind1d.rsi14) if ind1d else None,
                        news=[], news_summary="", bench={},
                        open_interest=0, funding_rate=0, volume_24h=0)
                    sig = await self._ai_decider.decide(ai_inp)
                    if not sig:
                        continue
                    # 方向相反 → 平仓
                    if (sig.action in ("SELL","STRONG_SELL") and pos.side=="LONG") or \
                       (sig.action in ("BUY","STRONG_BUY") and pos.side=="SHORT"):
                        logger.info("%s %s → AI 平仓", pos.symbol, pos.side)
                        await self._executor.close_position(pos.id, "AI_CLOSE")
                    # 方向一致 → 更新止盈止损
                    elif (sig.action in ("BUY","STRONG_BUY") and pos.side=="LONG") or \
                         (sig.action in ("SELL","STRONG_SELL") and pos.side=="SHORT"):
                        if hasattr(self._executor, '_trader'):
                            hold = "long" if pos.side == "LONG" else "short"
                            tpsl = "sell" if hold == "long" else "buy"
                            try:
                                await self._executor._trader.place_stop_order(
                                    pos.symbol, hold, tpsl, sig.stop_loss, pos.quantity, "loss_plan")
                                await self._executor._trader.place_stop_order(
                                    pos.symbol, hold, tpsl, sig.take_profits[0], pos.quantity, "profit_plan")
                                logger.info("%s SL→$%.2f TP→$%.2f", pos.symbol, sig.stop_loss, sig.take_profits[0])
                            except Exception as e:
                                logger.warning("SL/TP更新失败 %s: %s", pos.symbol, e)
                except Exception as e:
                    logger.error("管仓 %s 失败: %s", pos.symbol, e)

        # ── AI 原生扫描开仓 ──
        symbols_to_scan = self._rank_symbols(candidates, rich_quotes)[:10]
        logger.info("AI 原生扫描 %d 品种 | 大盘: %s",
                   len(symbols_to_scan),
                   " ".join(f"{k}{v:+.1f}%" for k,v in bench.items()))

        seen = set(p.symbol for p in positions)
        for symbol in symbols_to_scan:
            if symbol in seen: continue
            seen.add(symbol)
            try:
                k_1h = await self._market.get_klines(symbol, "1H", 500)
                q = await self._market.get_quote(symbol)
                if not q or len(k_1h) < 30: continue
                ind_1h = self._tech.calculate(k_1h)
                regime = self._regime_detector.detect(k_1h)
                k_4h = [Kline(**r) for r in agg.aggregate(k_1h, "4H")]
                k_1d = [Kline(**r) for r in agg.aggregate(k_1h, "1D")]
                ind_4h_raw = self._tech.calculate(k_4h) if len(k_4h) >= 5 else None
                ind_1d_raw = self._tech.calculate(k_1d) if len(k_1d) >= 3 else None

                news_items = []
                try: news_items = await self._news_registry.fetch_news(symbol, max_results=5)
                except: pass
                news_titles = [item.title for item in news_items[:5]]

                ai_inp = AIInput(
                    symbol=symbol, mark_price=q.mark_price, change_pct=q.change_pct*100,
                    klines_1h=k_1h, klines_4h=k_4h, klines_1d=k_1d,
                    ind_1h=dict(rsi=ind_1h.rsi14, ma10=ind_1h.ma10, ma30=ind_1h.ma30,
                               macd=ind_1h.macd, atr=ind_1h.atr14,
                               adx=regime.adx, regime=regime.regime, bb_position=0.5),
                    ind_4h=dict(rsi=ind_4h_raw.rsi14) if ind_4h_raw else None,
                    ind_1d=dict(rsi=ind_1d_raw.rsi14) if ind_1d_raw else None,
                    news=news_titles, news_summary="; ".join(news_titles[:3]),
                    bench=bench, open_interest=q.open_interest,
                    funding_rate=getattr(q, 'funding_rate', 0) or 0,
                    volume_24h=q.volume_24h,
                )
                sig = await self._ai_decider.decide(ai_inp)
                if sig:
                    logger.info("%s: 规则通过 → Pro=%s SL=$%.2f TP=$%.2f",
                               symbol, sig.action, sig.stop_loss, sig.take_profits[0])
                    result = await self._executor.execute_signal(sig)
                    if result.status == "FILLED":
                        logger.info("  → 开仓: qty=%.1f", result.fill_quantity)
                else:
                    logger.info("%s: 规则=HOLD", symbol)
            except Exception as e:
                logger.error("AI扫描 %s 失败: %s", symbol, e)

        self._executor._save_state()
        self._last_prices.update(quotes)
        positions = await self._executor.get_positions()
        balance = await self._executor.get_balance()
        equity = await self._executor.get_equity()
        logger.info("扫描完成 | 持仓 %d | 净值 $%.0f (余额 $%.0f) | PnL $%.0f",
                   len(positions), equity, balance.current_balance, balance.total_pnl)

    async def run_live(self, interval: int = QUOTE_INTERVAL):
        logger.info("AutoTrader 启动 | 间隔 %ds | 品种 %d", interval, len(self._symbols))
        while True:
            await self.run_once()
            await asyncio.sleep(interval)

    # ═══ 内部方法 ═══════════════════════════════════

    async def _fetch_all_quotes(self) -> dict[str, float]:
        quotes = {}
        for sym in self._symbols:
            try:
                q = await self._market.get_quote(sym)
                if q and q.mark_price > 0:
                    quotes[sym] = q.mark_price
            except Exception: pass
        return quotes

    async def _fetch_rich_quotes(self) -> dict[str, dict]:
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
            except Exception: pass
        return rich

    def _rank_symbols(self, symbols, rich_quotes):
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
        try:
            rich = {}
            for sym in self._symbols[:20]:
                try:
                    q = await self._market.get_quote(sym)
                    if q:
                        rich[sym] = {"volume_24h": q.volume_24h, "change_pct": abs(q.change_pct), "turnover_24h": q.turnover_24h}
                except Exception: pass
            if len(rich) >= 5:
                self._symbols = self._rank_symbols(list(rich.keys()), rich)
        except Exception as e:
            logger.error("刷新热度排名失败: %s", e)

    def _filter_by_price_change(self, quotes):
        triggered = []
        for sym, price in quotes.items():
            last = self._last_prices.get(sym)
            if last is None:
                triggered.append(sym)
            elif abs(price / last - 1) >= 0.002:
                triggered.append(sym)
        return triggered


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    args = sys.argv[1:]
    mode = "once"
    interval = QUOTE_INTERVAL
    for i, arg in enumerate(args):
        if arg == "--live": mode = "live"
        elif arg == "--interval" and i + 1 < len(args): interval = int(args[i + 1])
        elif arg == "--once": mode = "once"

    trader = AutoTrader()
    if mode == "live":
        await trader.run_live(interval)
    else:
        await trader.run_once()

if __name__ == "__main__":
    asyncio.run(main())
