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
from src.strategies.ai_native import AINativeDecisionMaker, AIInput, get_us_session

logger = logging.getLogger("autotrader")


def _bb_pos(ind, price: float) -> float:
    """布林带位置 0-1: 0=下轨 0.5=中轨 1=上轨。"""
    try:
        spread = ind.bb_upper - ind.bb_lower
        if spread <= 0:
            return 0.5
        return round(max(0.0, min(1.0, (price - ind.bb_lower) / spread)), 2)
    except Exception:
        return 0.5


def _holding_hours(pos) -> float:
    """持仓时长(小时)。"""
    try:
        if not getattr(pos, "opened_at", None):
            return 0.0
        from datetime import datetime, timezone
        opened = pos.opened_at
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        return round((datetime.now(timezone.utc) - opened).total_seconds() / 3600, 1)
    except Exception:
        return 0.0


def _kline_dicts(klines) -> list[dict]:
    """Kline dataclass → dict (供落库)。"""
    return [k.__dict__ if hasattr(k, "__dict__") else k for k in klines]

FULL_SCAN_INTERVAL = 600
QUOTE_INTERVAL = 30
SYMBOL_REFRESH_INTERVAL = 7200  # 品种池刷新: 2小时
LOSS_COOLDOWN_HOURS = 24         # 亏损平仓后冷却: 24小时不进入品种池
TOP_N_SYMBOLS = 25
BENCHMARK_SYMBOLS = ["SPY", "QQQ", "SOXX"]


class AutoTrader:

    def __init__(self):
        config = get_config()
        self._symbols = config.symbols
        self._market = BitgetMarketSource()
        self._symbol_source = BitgetSymbolSource()

        self._kline_store = KlineStore()
        self._last_kline_prune = 0.0  # K线清理计时
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

        # AI 决策器: 从 config.yaml ai_provider 段读取 (可自选供应商/模型)
        ds_cfg = config.ai_provider_cfg()
        provider = config.get("ai_provider", "deepseek")
        # API key 优先级: 配置值 > 供应商专属环境变量 > DEEPSEEK_API_KEY
        import os as _os
        api_key = ds_cfg.get("api_key", "") or _os.environ.get(
            f"{provider.upper()}_API_KEY", "") or _os.environ.get("DEEPSEEK_API_KEY", "")
        self._ai_decider = AINativeDecisionMaker(
            model=ds_cfg.get("model", "deepseek-v4-flash"),
            base_url=ds_cfg.get("base_url", "https://api.deepseek.com"),
            api_key=api_key,
        )
        logger.info("AI 供应商: %s | 模型: %s", provider, self._ai_decider._model)
        from src.strategies.ai_memory import AIMemory
        self._memory = AIMemory()
        self._lessons = self._memory.get_lessons(5)
        self._rules = self._memory.get_rules(10)
        self._last_review = 0.0  # 复盘计时
        self._tech = TechnicalAnalyzer()
        self._regime_detector = MarketRegimeDetector()

        from src.datasources.news.yahoo import YahooNewsSource
        from src.datasources.news.searxng import SearXNGNewsSource
        from src.datasources.news.finnhub import FinnhubNewsSource
        from src.datasources.news.google import GoogleNewsSource
        from src.datasources.news.registry import NewsRegistry
        self._news_registry = NewsRegistry(
            primary_name="google",  # Google News RSS: 免费无限, 精准
            fallback_name=config.news_sources.get("fallback", "searxng"),
            fallback2_name="yahoo",
        )
        self._news_registry.register(GoogleNewsSource())
        self._news_registry.register(YahooNewsSource())
        self._news_registry.register(SearXNGNewsSource(base_url=config.searxng_base_url, timeout=config.searxng_timeout))
        # Finnhub: config token 或环境变量
        import os as _os
        finnhub_token = config.news_sources.get("finnhub", {}).get("token", "") or _os.environ.get("FINNHUB_API_KEY", "")
        if finnhub_token:
            self._news_registry.register(FinnhubNewsSource(api_key=finnhub_token))
            logger.info("Finnhub 新闻源已注册")

        self._last_prices: dict[str, float] = {}
        self._last_full_scan = 0.0
        self._last_symbol_refresh = 0.0
        # 亏损冷却: symbol → 冷却截止时间戳 (重启后从 ai_memory 恢复)
        self._cooldown: dict[str, float] = self._load_cooldowns()

    def _load_cooldowns(self) -> dict[str, float]:
        """从 ai_memory 恢复亏损冷却 (最近24h内亏损平仓的品种)。"""
        import datetime as _dt
        cooldown = {}
        try:
            decisions = self._memory.recent_decisions(500)
            now_ts = time.time()
            for d in decisions:
                if d.get("outcome") == "loss" and d.get("close_pnl", 0) < 0:
                    try:
                        t = _dt.datetime.strptime(d["time"], "%Y-%m-%d %H:%M:%S")
                        t = t.replace(tzinfo=_dt.timezone.utc)
                        close_ts = t.timestamp()
                    except Exception:
                        continue
                    expiry = close_ts + LOSS_COOLDOWN_HOURS * 3600
                    sym = d["symbol"]
                    if expiry > now_ts and expiry > cooldown.get(sym, 0):
                        cooldown[sym] = expiry
            if cooldown:
                logger.info("恢复亏损冷却: %s", {k: _dt.datetime.fromtimestamp(v, _dt.timezone.utc).strftime("%m-%d %H:%M") for k, v in cooldown.items()})
        except Exception as e:
            logger.warning("恢复冷却失败: %s", e)
        return cooldown

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

        # 每日清理过期K线 (保留30天)
        if now - self._last_kline_prune >= 86400:
            try:
                deleted = self._kline_store.prune(keep_days=30)
                self._last_kline_prune = now
                if deleted:
                    logger.info("K线清理: 删除 %d 条过期数据", deleted)
            except Exception as e:
                logger.warning("K线清理失败: %s", e)

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
                    k_15m = await self._market.get_klines(pos.symbol, "15m", 500)
                    q = await self._market.get_quote(pos.symbol)
                    if not q or len(k_1h) < 30: continue
                    # K线落库 (实时数据追加)
                    try:
                        self._kline_store.upsert_batch(pos.symbol, _kline_dicts(k_1h), "1H")
                        self._kline_store.upsert_batch(pos.symbol, _kline_dicts(k_15m), "15m")
                    except Exception: pass
                    ind = self._tech.calculate(k_1h)
                    reg = self._regime_detector.detect(k_1h)
                    k_4h = [Kline(**r) for r in agg.aggregate(k_1h, "4H")]
                    k_1d = [Kline(**r) for r in agg.aggregate(k_1h, "1D")]
                    ind4 = self._tech.calculate(k_4h) if len(k_4h)>=5 else None
                    ind1d = self._tech.calculate(k_1d) if len(k_1d)>=3 else None
                    ind15 = None
                    if len(k_15m) >= 30:
                        i15 = self._tech.calculate(k_15m)
                        r15 = self._regime_detector.detect(k_15m)
                        ind15 = dict(rsi=i15.rsi14, ma10=i15.ma10, ma30=i15.ma30,
                                     macd=i15.macd, atr=i15.atr14,
                                     adx=r15.adx, regime=r15.regime,
                                     bb_position=_bb_pos(i15, q.mark_price),
                                     volume_ratio=i15.volume_ratio,
                                     vwap=i15.vwap)
                    # 管仓: 仅异常波动注入新闻
                    news_items = []
                    news_titles = []
                    if abs(q.change_pct * 100) >= 5.0:
                        try:
                            news_items = await self._news_registry.fetch_news(pos.symbol, max_results=5)
                        except Exception: pass
                        news_titles = [item.title for item in news_items[:5]]
                    ai_inp = AIInput(
                        symbol=pos.symbol, mark_price=q.mark_price, change_pct=q.change_pct*100,
                        klines_1h=k_1h, klines_4h=k_4h, klines_1d=k_1d,
                        ind_1h=dict(rsi=ind.rsi14, ma10=ind.ma10, ma30=ind.ma30,
                                   macd=ind.macd, atr=ind.atr14, adx=reg.adx,
                                   regime=reg.regime,
                                   bb_position=_bb_pos(ind, q.mark_price),
                                   volume_ratio=ind.volume_ratio,
                                   vwap=ind.vwap),
                        ind_4h=dict(rsi=ind4.rsi14) if ind4 else None,
                        ind_1d=dict(rsi=ind1d.rsi14) if ind1d else None,
                        ind_15m=ind15,
                        news=news_titles, news_summary="; ".join(news_titles[:3]),
                        bench=bench, open_interest=q.open_interest,
                        funding_rate=getattr(q, 'funding_rate', 0) or 0,
                        volume_24h=q.volume_24h,
                        session=get_us_session(),
                        lessons=self._lessons,
                        history=self._memory.get_symbol_history(pos.symbol, 3),
                        position_ctx={
                            "side": pos.side, "entry": pos.entry_price,
                            "hours": _holding_hours(pos),
                            "pnl": round(getattr(pos, 'unrealized_pnl', 0) or 0, 2),
                            "sl": pos.stop_loss or 0,
                            "tp": (pos.take_profit_levels[0] if pos.take_profit_levels else 0) or 0,
                        })
                    # 管仓专用决策器: 日内持仓管理, 动态止盈止损
                    sig = await self._ai_decider.manage_position(ai_inp)
                    if not sig:
                        continue
                    if sig.action == "CLOSE":
                        # AI 管仓判断平仓离场 (E: 用AI离场原因做close_reason)
                        close_reason = f"MANAGE:{sig.reason[:40]}" if sig.reason else "MANAGE_CLOSE"
                        logger.info("%s %s → 管仓AI平仓: %s",
                                   pos.symbol, pos.side, sig.reason[:60])
                        close_pnl = getattr(pos, 'unrealized_pnl', 0) or 0
                        await self._executor.close_position(pos.id, "AI_MANAGE_CLOSE")
                        # 亏损平仓 → 冷却24h, 不进品种池
                        if close_pnl < 0:
                            self._cooldown[pos.symbol] = time.time() + LOSS_COOLDOWN_HOURS * 3600
                            logger.info("%s 亏损平仓 $%.2f → 冷却 %dh", pos.symbol, close_pnl, LOSS_COOLDOWN_HOURS)
                        try:
                            self._memory.close_decision(
                                pos.symbol, q.mark_price, close_pnl,
                                close_reason=close_reason,
                                holding_hours=_holding_hours(pos))
                        except Exception: pass
                        continue

                    # C: 周末/深夜闭市 → 只检查不调整 SL/TP
                    session = get_us_session()
                    if session in ("weekend", "closed"):
                        logger.info("%s %s | %s → 管仓检查, 不调整SL/TP",
                                   pos.symbol, pos.side, session)
                        continue

                    # HOLD + 动态更新止盈止损 (A: 仅差异>0.5%才重挂)
                    if hasattr(self._executor, '_trader'):
                        cur_sl = pos.stop_loss or 0
                        cur_tp = (pos.take_profit_levels[0] if pos.take_profit_levels else 0) or 0
                        new_sl = sig.stop_loss
                        new_tp = sig.take_profits[0] if sig.take_profits else 0
                        if new_sl <= 0 or new_tp <= 0:
                            continue
                        sl_changed = cur_sl <= 0 or abs(new_sl - cur_sl) / max(cur_sl, 1e-9) > 0.002
                        tp_changed = cur_tp <= 0 or abs(new_tp - cur_tp) / max(cur_tp, 1e-9) > 0.002
                        if not sl_changed and not tp_changed:
                            continue  # 无实质变化, 不重挂
                        hold = "long" if pos.side == "LONG" else "short"
                        tpsl = "sell" if hold == "long" else "buy"
                        try:
                            if sl_changed:
                                await self._executor._trader.place_stop_order(
                                    pos.symbol, hold, tpsl, new_sl, pos.quantity, "pos_loss")
                            if tp_changed:
                                await self._executor._trader.place_stop_order(
                                    pos.symbol, hold, tpsl, new_tp, pos.quantity, "pos_profit")
                            logger.info("%s SL→$%.2f TP→$%.2f | %s",
                                       pos.symbol, new_sl, new_tp, sig.reason[:50])
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
                k_15m = await self._market.get_klines(symbol, "15m", 500)
                q = await self._market.get_quote(symbol)
                if not q or len(k_1h) < 30: continue
                # K线落库 (实时数据追加)
                try:
                    self._kline_store.upsert_batch(symbol, _kline_dicts(k_1h), "1H")
                    self._kline_store.upsert_batch(symbol, _kline_dicts(k_15m), "15m")
                except Exception: pass
                ind_1h = self._tech.calculate(k_1h)
                regime = self._regime_detector.detect(k_1h)
                k_4h = [Kline(**r) for r in agg.aggregate(k_1h, "4H")]
                k_1d = [Kline(**r) for r in agg.aggregate(k_1h, "1D")]
                ind_4h_raw = self._tech.calculate(k_4h) if len(k_4h) >= 5 else None
                ind_1d_raw = self._tech.calculate(k_1d) if len(k_1d) >= 3 else None
                ind_15m_raw = None
                if len(k_15m) >= 30:
                    i15 = self._tech.calculate(k_15m)
                    r15 = self._regime_detector.detect(k_15m)
                    ind_15m_raw = dict(rsi=i15.rsi14, ma10=i15.ma10, ma30=i15.ma30,
                                       macd=i15.macd, atr=i15.atr14,
                                       adx=r15.adx, regime=r15.regime,
                                       bb_position=_bb_pos(i15, q.mark_price),
                                       volume_ratio=i15.volume_ratio,
                                       vwap=i15.vwap)

                # 新闻降级: 仅异常波动(±5%)时注入, 否则空 (避免噪声)
                news_items = []
                news_titles = []
                if abs(q.change_pct * 100) >= 5.0:
                    try:
                        news_items = await self._news_registry.fetch_news(symbol, max_results=5)
                    except Exception: pass
                    news_titles = [item.title for item in news_items[:5]]
                if news_titles:
                    logger.info("%s 异常波动 %+.1f%% → 注入新闻 %d 条",
                               symbol, q.change_pct * 100, len(news_titles))

                ai_inp = AIInput(
                    symbol=symbol, mark_price=q.mark_price, change_pct=q.change_pct*100,
                    klines_1h=k_1h, klines_4h=k_4h, klines_1d=k_1d,
                    ind_1h=dict(rsi=ind_1h.rsi14, ma10=ind_1h.ma10, ma30=ind_1h.ma30,
                               macd=ind_1h.macd, atr=ind_1h.atr14,
                               adx=regime.adx, regime=regime.regime,
                               bb_position=_bb_pos(ind_1h, q.mark_price),
                               volume_ratio=ind_1h.volume_ratio,
                               vwap=ind_1h.vwap),
                    ind_4h=dict(rsi=ind_4h_raw.rsi14) if ind_4h_raw else None,
                    ind_1d=dict(rsi=ind_1d_raw.rsi14) if ind_1d_raw else None,
                    ind_15m=ind_15m_raw,
                    news=news_titles, news_summary="; ".join(news_titles[:3]),
                    bench=bench, open_interest=q.open_interest,
                    funding_rate=getattr(q, 'funding_rate', 0) or 0,
                    volume_24h=q.volume_24h,
                    session=get_us_session(),
                    lessons=self._lessons,
                    rules=self._rules,
                    history=self._memory.get_symbol_history(symbol, 3))
                sig = await self._ai_decider.decide(ai_inp)
                if sig:
                    logger.info("%s: AI=%s SL=$%.2f TP=$%.2f",
                               symbol, sig.action, sig.stop_loss, sig.take_profits[0])
                    result = await self._executor.execute_signal(sig)
                    if result.status == "FILLED":
                        logger.info("  → 开仓: qty=%.1f", result.fill_quantity)
                    self._memory.log_decision(
                        symbol, sig.action, sig.reason, get_us_session(),
                        q.mark_price, ind_1h.rsi14, regime.adx, regime.regime,
                        entry=q.mark_price,
                        sl_price=sig.stop_loss,
                        tp_price=sig.take_profits[0] if sig.take_profits else 0)
                else:
                    logger.info("%s: AI=HOLD", symbol)
                    # 记录 HOLD (保证复盘有数据; 统计时只算有结果的)
                    self._memory.log_decision(
                        symbol, "HOLD", "AI判断无机会", get_us_session(),
                        q.mark_price, ind_1h.rsi14, regime.adx, regime.regime)
            except Exception as e:
                logger.error("AI扫描 %s 失败: %s", symbol, e)

        self._executor._save_state()
        self._last_prices.update(quotes)
        positions = await self._executor.get_positions()
        balance = await self._executor.get_balance()
        equity = await self._executor.get_equity()
        logger.info("扫描完成 | 持仓 %d | 净值 $%.0f (余额 $%.0f) | PnL $%.0f",
                   len(positions), equity, balance.current_balance, balance.total_pnl)

        # ── AI 自我复盘 (每12小时, 无条件更新计时器) ──
        if time.time() - self._last_review >= 12 * 3600:
            self._last_review = time.time()  # 先更新, 防止每轮空跑
            try:
                lessons, rules = await self._ai_decider.review_and_learn(self._memory)
                if lessons:
                    self._memory.set_lessons(lessons)
                    self._lessons = lessons
                if rules:
                    self._memory.set_rules(rules)
                    self._rules = rules
                if lessons or rules:
                    logger.info("AI 已自我更新 %d 条经验, %d 条硬规则",
                               len(lessons), len(rules))
            except Exception as e:
                logger.warning("AI 复盘失败: %s", e)

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
        """从全市场美股合约动态刷新品种池（按热度取前25）。"""
        try:
            contracts = await self._symbol_source.get_stock_symbols()
            if len(contracts) < 10:
                logger.warning("全市场合约不足 (%d)，保持原池", len(contracts))
                return

            # 批量拉行情，按成交量+涨跌排序
            rich = {}
            for c in contracts[:80]:  # 全市场美股合约
                try:
                    q = await self._market.get_quote(c.symbol)
                    if q and q.mark_price > 0 and (q.volume_24h or 0) > 0:
                        rich[c.symbol] = {
                            "volume_24h": q.volume_24h,
                            "change_pct": abs(q.change_pct),
                            "turnover_24h": q.turnover_24h,
                        }
                except Exception: pass

            if len(rich) >= 15:
                ranked = self._rank_symbols(list(rich.keys()), rich)
                # 过滤亏损冷却中的品种 (24h内亏损平仓)
                now = time.time()
                active = [s for s in ranked if self._cooldown.get(s, 0) <= now]
                if len(active) < TOP_N_SYMBOLS // 2 and self._cooldown:
                    logger.info("冷却品种过多(%d), 放宽过滤", len([s for s in ranked if self._cooldown.get(s, 0) > now]))
                    active = ranked
                new_pool = active[:TOP_N_SYMBOLS]
                # 保留现有持仓品种（避免池子刷新把持仓踢掉）
                positions = await self._executor.get_positions()
                held = {p.symbol for p in positions}
                for h in held:
                    if h not in new_pool:
                        new_pool.append(h)
                self._symbols = new_pool
                cooling_now = [s for s in self._cooldown if self._cooldown[s] > now and s not in held]
                if cooling_now:
                    logger.info("品种池刷新 | 冷却中跳过: %s", cooling_now[:8])
                logger.info("品种池动态刷新: %d 个 (来自全市场 %d)", len(new_pool), len(rich))
        except Exception as e:
            logger.error("刷新品种池失败: %s", e)

    def _filter_by_price_change(self, quotes):
        triggered = []
        for sym, price in quotes.items():
            last = self._last_prices.get(sym)
            if last is None:
                triggered.append(sym)
            elif abs(price / last - 1) >= 0.002:
                triggered.append(sym)
        return triggered


def _setup_logging():
    """日志: 控制台 + 文件轮转 (10MB × 5, 自动覆盖最旧)。"""
    import logging.handlers
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # 控制台
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    # 文件轮转 (logs/auto_trader.log, 10MB, 5个备份)
    try:
        log_dir = os.path.join(os.path.dirname(__file__), "..", "data", "logs")
        os.makedirs(log_dir, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            os.path.join(log_dir, "auto_trader.log"),
            maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except Exception as e:
        print(f"日志文件初始化失败(不影响运行): {e}")


async def main():
    _setup_logging()
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
