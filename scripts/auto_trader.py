#!/usr/bin/env python3
"""AI 原生自动交易引擎 — 无规则策略，纯 AI 驱动

架构: Bitget 定时轮询 → AI(规则初筛→Pro深度分析) → 执行
模式: --live 持续运行 / --once 单次扫描
"""

import asyncio, logging, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config.loader import get_config
from src.datasources.bitget.market import BitgetMarketSource
from src.datasources.bitget.symbols import BitgetSymbolSource
from src.analyzers.technical import TechnicalAnalyzer
from src.analyzers.market_regime import MarketRegimeDetector
from src.trading.safety import SafetySystem
from src.storage.kline_store import KlineStore
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


def _orderbook_pressure(book) -> dict:
    """盘口压力分析 (实时微观数据)。

    Returns:
        {pressure: 买卖压力比(买量/卖量), spread: 价差, spread_pct: 价差%,
         big_bid: 大买单(价,量), big_ask: 大卖单(价,量)}
    """
    try:
        if not book or not book.bids or not book.asks:
            return {}
        # 前10档买卖量总和
        bid_vol = sum(lvl.size for lvl in book.bids[:10])
        ask_vol = sum(lvl.size for lvl in book.asks[:10])
        pressure = round(bid_vol / ask_vol, 2) if ask_vol > 0 else 1.0
        best_bid = book.bids[0].price
        best_ask = book.asks[0].price
        spread = round(best_ask - best_bid, 4)
        mid = (best_bid + best_ask) / 2
        spread_pct = round(spread / mid * 100, 3) if mid > 0 else 0
        # 大单: 某档挂单量 > 平均3倍
        avg_vol = (bid_vol + ask_vol) / max(len(book.bids[:10]) + len(book.asks[:10]), 1)
        big_bid = None
        big_ask = None
        if avg_vol > 0:
            for lvl in book.bids[:10]:
                if lvl.size > avg_vol * 3:
                    big_bid = (round(lvl.price, 2), round(lvl.size, 2))
                    break
            for lvl in book.asks[:10]:
                if lvl.size > avg_vol * 3:
                    big_ask = (round(lvl.price, 2), round(lvl.size, 2))
                    break
        return {
            "pressure": pressure, "spread": spread, "spread_pct": spread_pct,
            "big_bid": big_bid, "big_ask": big_ask,
        }
    except Exception:
        return {}


def _pressure_str(book) -> str:
    """盘口压力 → prompt 文本。"""
    p = _orderbook_pressure(book)
    if not p:
        return ""
    parts = [f"盘口压力比={p['pressure']} (买量/卖量, >1买压强 <1卖压强)",
             f"价差=${p['spread']:.4f} ({p['spread_pct']:.3f}%)"]
    if p.get("big_bid"):
        parts.append(f"大买单@{p['big_bid'][0]} x{p['big_bid'][1]}")
    if p.get("big_ask"):
        parts.append(f"大卖单@{p['big_ask'][0]} x{p['big_ask'][1]}")
    return " | ".join(parts) + "\n"


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


def _trend_shape(klines, lookback: int = 24) -> str:
    """走势形状分析 — 把最近K线轨迹压缩成文字 (AI 能读懂动态走势)。

    提取: 形态(单边/反转/横盘) + 动量(加速/衰竭) + 最后一根K线特征 + 关键位。
    """
    try:
        if not klines or len(klines) < 6:
            return ""
        ks = list(klines)[-lookback:]
        closes = [float(k.close) for k in ks]
        highs = [float(k.high) for k in ks]
        lows = [float(k.low) for k in ks]
        c0 = closes[0]
        c1 = closes[-1]

        # 1. 整体形态
        total_chg = (c1 / c0 - 1) * 100 if c0 else 0
        # 前段 vs 后段 (判断加速/减速/反转)
        mid = len(ks) // 2
        first_chg = (closes[mid] / c0 - 1) * 100 if c0 else 0
        last_chg = (c1 / closes[mid] - 1) * 100 if closes[mid] else 0
        if abs(total_chg) < 0.3:
            shape = "横盘震荡"
        elif last_chg * total_chg < 0 and abs(last_chg) > 0.3:
            shape = "反转" + ("回落" if total_chg > 0 else "反弹")
        elif abs(last_chg) > abs(first_chg) * 1.3 and abs(first_chg) > 0.1:
            shape = "加速" + ("上涨" if total_chg > 0 else "下跌")
        elif abs(last_chg) < abs(first_chg) * 0.5 and abs(first_chg) > 0.3:
            shape = "动量衰竭" + ("(涨势趋缓)" if total_chg > 0 else "(跌势趋缓)")
        else:
            shape = "单边" + ("上涨" if total_chg > 0 else "下跌")

        # 2. 最近3根K线组合 (最后一段微观走势)
        tail = ks[-3:]
        tail_txt = []
        for k in tail:
            o, c, h, l = float(k.open), float(k.close), float(k.high), float(k.low)
            body = abs(c - o)
            rng = max(h - l, 1e-9)
            if c > o:
                tail_txt.append("阳")
            elif c < o:
                tail_txt.append("阴")
            else:
                tail_txt.append("十")
            # 影线特征
            upper = (h - max(o, c)) / rng
            lower = (min(o, c) - l) / rng
            if upper > 0.6:
                tail_txt[-1] += "长上影"
            elif lower > 0.6:
                tail_txt[-1] += "长下影"
        tail_str = "→".join(tail_txt)

        # 3. 波动区间
        hi, lo = max(highs), min(lows)
        rng_pct = (hi - lo) / c1 * 100 if c1 else 0

        # 4. 关键位 (近2小时高低点, 止损止盈锚点)
        key_hi = round(hi, 2)
        key_lo = round(lo, 2)
        dist_hi = (key_hi / c1 - 1) * 100 if c1 else 0
        dist_lo = (c1 / key_lo - 1) * 100 if key_lo else 0

        return (f"走势: {shape} 区间累计{total_chg:+.1f}% (前段{first_chg:+.1f}%/后段{last_chg:+.1f}%) "
                f"近3根K线[{tail_str}] 波动范围{rng_pct:.1f}% "
                f"关键位: 高{key_hi}(距{abs(dist_hi):.1f}%) 低{key_lo}(距{abs(dist_lo):.1f}%)\n")
    except Exception:
        return ""


def _kline_dicts(klines) -> list[dict]:
    """Kline dataclass → dict (供落库)。"""
    return [k.__dict__ if hasattr(k, "__dict__") else k for k in klines]


def _aggregate_15m(klines_5m: list) -> list[dict]:
    """5m K线 → 15m (3根合成1根, 供落库)。"""
    try:
        import pandas as pd
        rows = _kline_dicts(klines_5m)
        if len(rows) < 3:
            return []
        df = pd.DataFrame(rows)
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("datetime", inplace=True)
        resampled = df.resample("15min").agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last", "volume": "sum", "turnover": "sum",
        }).dropna()
        result = []
        for ts, row in resampled.iterrows():
            result.append({
                "timestamp": int(ts.timestamp() * 1000),
                "open": float(row["open"]), "high": float(row["high"]),
                "low": float(row["low"]), "close": float(row["close"]),
                "volume": float(row["volume"]), "turnover": float(row["turnover"]),
            })
        return result
    except Exception:
        return []

FULL_SCAN_INTERVAL = 600
QUOTE_INTERVAL = 30        # 行情轮询
SCAN_INTERVAL = 300        # 管仓/开仓扫描: 5分钟 (日内止盈止损需要更频繁捕捉)
SYMBOL_REFRESH_INTERVAL = 900  # 品种池刷新: 15分钟 (激进, 快速捕捉题材轮动)
LOSS_COOLDOWN_HOURS = 2          # 亏损平仓后冷却: 2小时不进入品种池
TOP_N_SYMBOLS = 25
BENCHMARK_SYMBOLS = ["SPY", "QQQ", "SOXX"]
# 热门股白名单: 强制保留在品种池 (不受热度排名影响)
HOT_SYMBOLS = ["NVDA", "TSLA", "META", "AMZN", "MSFT", "AAPL", "GOOGL",
               "PLTR", "AMD", "COIN", "MSTR", "SMCI", "AVGO", "NFLX", "ARM"]

# ETF 黑名单: 品种池拒绝 ETF 产品 (yfinance quoteType==ETF 识别, 50个)
ETF_BLACKLIST = {
    "AAPU", "AMZU", "BITO", "BOTZ", "CONL", "DFEN", "DRAM", "EUV", "EWH",
    "EWJ", "EWT", "EWY", "EWZ", "GGLL", "IBB", "INDA", "INTW", "IWM", "KORU",
    "KSTR", "KWEB", "METU", "MSFU", "MSTU", "MUU", "MVLL", "NVDL", "QQQ", "RAM",
    "SGOV", "SKDD", "SMH", "SNXX", "SOXL", "SOXS", "SOXX", "SPY", "SQQQ", "TBT",
    "TMF", "TQQQ", "TSLL", "TZA", "UVXY", "VOO", "XBI", "XLE", "XLK", "XLU", "XLV",
}


class AutoTrader:

    def __init__(self):
        config = get_config()
        self._symbols = [s for s in config.symbols if s not in ETF_BLACKLIST]  # 启动即排除ETF
        self._market = BitgetMarketSource()
        self._symbol_source = BitgetSymbolSource()

        self._kline_store = KlineStore()
        self._last_kline_prune = 0.0  # K线清理计时

        safety = SafetySystem(config.safety)
        mode = config.mode
        if mode != "real":
            logger.warning("配置 mode=%s, 系统仅支持 real (实盘)", mode)
            mode = "real"
        from src.trading.real_executor import RealExecutor
        self._executor = RealExecutor(safety=safety)
        if not self._executor.ready:
            raise RuntimeError(
                "Bitget API 未配置 — 本系统只支持实盘交易, 请配置 API 密钥后重启")
        logger.info("执行器: REAL")

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
        self._last_review = 0.0  # 复盘计时 (兼容保留)
        self._last_review_count = self._memory.get_review_base()  # 复盘基准: 持久化, 重启不重置
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

        # 托管 SL/TP 平仓 → 回填 AI 记忆 (复盘能看到真实止损)
        if hasattr(self._executor, "_on_position_closed"):
            self._executor._on_position_closed = self._on_exchange_close

    def _on_exchange_close(self, symbol: str, pnl: float, reason: str, close_price: float = 0):
        """交易所托管 SL/TP 触发平仓 → 回填 ai_memory。"""
        try:
            self._memory.close_decision(symbol, close_price, pnl, close_reason=reason)
            if pnl < 0:
                self._cooldown[symbol] = time.time() + LOSS_COOLDOWN_HOURS * 3600
                logger.info("%s 托管止损 $%.2f → 冷却 %dh", symbol, pnl, LOSS_COOLDOWN_HOURS)
            else:
                logger.info("%s 托管止盈 $%.2f", symbol, pnl)
        except Exception as e:
            logger.warning("托管平仓记忆回填失败 %s: %s", symbol, e)

    def _account_status_str(self) -> str:
        """账户状态提示 — 平衡防守过度 (仅当亏损较大时提醒)。"""
        try:
            eq = getattr(self._executor, "_equity", 0)
            if isinstance(eq, (int, float)) and eq > 0:
                equity = float(eq)
            else:
                equity = 0.0
            if equity <= 0:
                return ""
            return ("账户状态: 当前净值 $%.2f, 从 $5 初始资金亏损约 %.0f%%\n"
                    "提醒: 防守过度同样会持续亏损——符合全部入场条件(方向+位置+走势+盘口)时不要犹豫, "
                    "但绝不降低标准乱开仓。\n" % (equity, (1 - equity / 5.0) * 100))
        except Exception:
            return ""

    def _load_cooldowns(self) -> dict[str, float]:
        """从 ai_memory 恢复亏损冷却 (最近2h内亏损平仓的品种)。"""
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

        # 每轮先刷新账户净值 (供仓位计算 + 账户状态注入, 防用过期值)
        try:
            await self._executor.get_equity()
        except Exception:
            pass

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
            # 注意: 无价格变动时不提前return — 管仓必须每轮执行

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
                    k_5m = await self._market.get_klines(pos.symbol, "5m", 500)
                    q = await self._market.get_quote(pos.symbol)
                    if not q or len(k_1h) < 30: continue
                    # K线落库 (实时数据追加: 5m + 合成15m + 1H)
                    try:
                        self._kline_store.upsert_batch(pos.symbol, _kline_dicts(k_1h), "1H")
                        self._kline_store.upsert_batch(pos.symbol, _kline_dicts(k_5m), "5m")
                        self._kline_store.upsert_batch(pos.symbol, _aggregate_15m(k_5m), "15m")
                    except Exception: pass
                    ind = self._tech.calculate(k_1h)
                    reg = self._regime_detector.detect(k_1h)
                    ind5 = None
                    if len(k_5m) >= 30:
                        i5 = self._tech.calculate(k_5m)
                        r5 = self._regime_detector.detect(k_5m)
                        ind5 = dict(rsi=i5.rsi14, ma10=i5.ma10, ma30=i5.ma30,
                                    macd=i5.macd, atr=i5.atr14,
                                    adx=r5.adx, regime=r5.regime,
                                    bb_position=_bb_pos(i5, q.mark_price),
                                    volume_ratio=i5.volume_ratio,
                                    vwap=i5.vwap)
                    # 管仓: 仅异常波动注入新闻
                    news_items = []
                    news_titles = []
                    if abs(q.change_pct * 100) >= 5.0:
                        try:
                            news_items = await self._news_registry.fetch_news(pos.symbol, max_results=5)
                        except Exception: pass
                        news_titles = [item.title for item in news_items[:5]]
                    # 盘口实时压力 (orderbook 前10档)
                    ob_str = ""
                    try:
                        book = await self._market.get_order_book(pos.symbol, limit=10)
                        ob_str = _pressure_str(book)
                    except Exception: pass
                    # 走势形状分析 (5m K线轨迹)
                    trend_str = _trend_shape(k_5m)
                    ai_inp = AIInput(
                        symbol=pos.symbol, mark_price=q.mark_price, change_pct=q.change_pct*100,
                        klines_1h=k_1h, klines_4h=[], klines_1d=[],
                        ind_1h=dict(rsi=ind.rsi14, ma10=ind.ma10, ma30=ind.ma30,
                                   macd=ind.macd, atr=ind.atr14, adx=reg.adx,
                                   regime=reg.regime,
                                   bb_position=_bb_pos(ind, q.mark_price),
                                   volume_ratio=ind.volume_ratio,
                                   vwap=ind.vwap),
                        ind_4h=None, ind_1d=None,
                        ind_5m=ind5,
                        orderbook=ob_str,
                        trend_shape=trend_str,
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
                        close_result = await self._executor.close_position(pos.id, "AI_MANAGE_CLOSE")
                        if close_result.status != "CLOSED":
                            logger.warning("%s 平仓失败: %s", pos.symbol, close_result.reason)
                            continue  # 平仓未成交, 不回填决策
                        # 亏损平仓 → 冷却2h, 不进品种池
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

        # ── AI 原生扫描开仓 (仅当有候选品种时, 排除ETF) ──
        symbols_to_scan = []
        if candidates:
            candidates = [s for s in candidates if s not in ETF_BLACKLIST]
            # 扫描优先级: 白名单热门股优先(最多5个) + 热度股补充
            hot_cands = [s for s in candidates if s in HOT_SYMBOLS][:5]
            rest = [s for s in candidates if s not in HOT_SYMBOLS]
            rest_ranked = self._rank_symbols(rest, rich_quotes)
            symbols_to_scan = (hot_cands + rest_ranked)[:10]
            logger.info("AI 原生扫描 %d 品种 | 大盘: %s",
                       len(symbols_to_scan),
                       " ".join(f"{k}{v:+.1f}%" for k,v in bench.items()))
        else:
            logger.info("无价格变动候选, 本轮仅管仓")

        seen = set(p.symbol for p in positions)
        for symbol in symbols_to_scan:
            if symbol in seen: continue
            seen.add(symbol)
            try:
                k_1h = await self._market.get_klines(symbol, "1H", 500)
                k_5m = await self._market.get_klines(symbol, "5m", 500)
                q = await self._market.get_quote(symbol)
                if not q or len(k_1h) < 30: continue
                # K线落库 (实时数据追加: 5m + 合成15m + 1H)
                try:
                    self._kline_store.upsert_batch(symbol, _kline_dicts(k_1h), "1H")
                    self._kline_store.upsert_batch(symbol, _kline_dicts(k_5m), "5m")
                    self._kline_store.upsert_batch(symbol, _aggregate_15m(k_5m), "15m")
                except Exception: pass
                ind_1h = self._tech.calculate(k_1h)
                regime = self._regime_detector.detect(k_1h)
                ind_5m_raw = None
                if len(k_5m) >= 30:
                    i5 = self._tech.calculate(k_5m)
                    r5 = self._regime_detector.detect(k_5m)
                    ind_5m_raw = dict(rsi=i5.rsi14, ma10=i5.ma10, ma30=i5.ma30,
                                      macd=i5.macd, atr=i5.atr14,
                                      adx=r5.adx, regime=r5.regime,
                                      bb_position=_bb_pos(i5, q.mark_price),
                                      volume_ratio=i5.volume_ratio,
                                      vwap=i5.vwap)

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

                # 盘口实时压力 (orderbook 前10档)
                ob_str = ""
                try:
                    book = await self._market.get_order_book(symbol, limit=10)
                    ob_str = _pressure_str(book)
                except Exception: pass
                # 走势形状分析 (5m K线轨迹)
                trend_str = _trend_shape(k_5m)

                ai_inp = AIInput(
                    symbol=symbol, mark_price=q.mark_price, change_pct=q.change_pct*100,
                    klines_1h=k_1h, klines_4h=[], klines_1d=[],
                    ind_1h=dict(rsi=ind_1h.rsi14, ma10=ind_1h.ma10, ma30=ind_1h.ma30,
                               macd=ind_1h.macd, atr=ind_1h.atr14,
                               adx=regime.adx, regime=regime.regime,
                               bb_position=_bb_pos(ind_1h, q.mark_price),
                               volume_ratio=ind_1h.volume_ratio,
                               vwap=ind_1h.vwap),
                    ind_4h=None, ind_1d=None,
                    ind_5m=ind_5m_raw,
                    orderbook=ob_str,
                    trend_shape=trend_str,
                    account_status=self._account_status_str(),
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
                        # 只在真实成交时记录 (被拒单不记录, 防污染决策库)
                        self._memory.log_decision(
                            symbol, sig.action, sig.reason, get_us_session(),
                            q.mark_price, ind_1h.rsi14, regime.adx, regime.regime,
                            entry=q.mark_price,
                            sl_price=sig.stop_loss,
                            tp_price=sig.take_profits[0] if sig.take_profits else 0)
                    else:
                        logger.info("  → 开仓被拒: %s", result.reason)
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

        # ── AI 自我复盘 (按已平仓交易数量触发: 每新增5笔平仓复盘) ──
        closed_now = self._memory.count_closed()
        if closed_now >= 3 and closed_now - self._last_review_count >= 5:
            self._last_review_count = closed_now  # 先更新, 防止每轮空跑
            self._memory.set_review_base(closed_now)  # 持久化, 重启不重置
            try:
                lessons, rules = await self._ai_decider.review_and_learn(self._memory)
                if lessons:
                    self._memory.set_lessons(lessons)
                    self._lessons = lessons
                if rules:
                    self._memory.set_rules(rules)
                    self._rules = rules
                if lessons or rules:
                    logger.info("AI 已自我更新 %d 条经验, %d 条硬规则 (已平仓样本 %d)",
                               len(lessons), len(rules), closed_now)
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

            # 批量拉行情，按成交量+涨跌排序 (排除ETF黑名单)
            rich = {}
            for c in contracts[:80]:  # 全市场美股合约
                if c.symbol in ETF_BLACKLIST:
                    continue  # 拒绝 ETF 产品
                try:
                    q = await self._market.get_quote(c.symbol)
                    if q and q.mark_price > 0 and (q.volume_24h or 0) > 0:
                        rich[c.symbol] = {
                            "volume_24h": q.volume_24h,
                            "change_pct": abs(q.change_pct),
                            "turnover_24h": q.turnover_24h,
                        }
                except Exception: pass

            # 白名单热门股: 即使不在前80也单独拉行情 (保证强制进池)
            for h in HOT_SYMBOLS:
                if h in rich or h in ETF_BLACKLIST:
                    continue
                try:
                    q = await self._market.get_quote(h)
                    if q and q.mark_price > 0 and (q.volume_24h or 0) > 0:
                        rich[h] = {
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
                # 池子 = 热度 top20 + 热门股白名单 (选项B)
                new_pool = active[:TOP_N_SYMBOLS - len(HOT_SYMBOLS)]
                for h in HOT_SYMBOLS:
                    if h in rich and h not in new_pool and self._cooldown.get(h, 0) <= now:
                        new_pool.append(h)
                # 保留现有持仓品种（避免池子刷新把持仓踢掉）
                positions = await self._executor.get_positions()
                held = {p.symbol for p in positions}
                for h in held:
                    if h not in new_pool:
                        new_pool.append(h)
                self._symbols = new_pool
                hot_in = [h for h in HOT_SYMBOLS if h in new_pool]
                if hot_in:
                    logger.info("品种池含白名单热门股: %s", hot_in)
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
    interval = SCAN_INTERVAL
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
