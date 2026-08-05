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
MIN_TURNOVER_24H = 5_000_000  # 品种池流动性下限: 24h成交额 ≥ $5M
# (Bitget美股合约市场总量有限: $1亿只剩SNDK/MU 2只, $5M=20只+白名单9只最均衡;
#  排除VRT$5万/NOKSTOCK$54万等垃圾流动性, 保证买得进卖得出止损准确)
LOSS_COOLDOWN_HOURS = 0          # 亏损冷却已禁用 (用户要求: 亏损后立即重新扫描, 不设冷却)
TOP_N_SYMBOLS = 25
BENCHMARK_SYMBOLS = ["SPY", "QQQ", "SOXX"]
# ── 固定监控池: 三大热门行业 (AI / 存储 / 加密货币) ──
# 用户要求: 只监控热门行业品种, 不动态全市场选股
INDUSTRY_POOL = [
    # AI 算力/云/应用
    "NVDA", "AMD", "AVGO", "ARM", "SMCI", "MRVL", "PLTR",
    "MSFT", "GOOGL", "AMZN", "META", "TSLA", "ORCL", "NBIS",
    "CRCL", "LITE", "COHR", "DELL", "AAPL", "INTC",
    # 存储/半导体
    "SNDK", "MU", "SKHYNIX", "SKHY", "SAMSUNG", "KIOXIA", "WDC",
    "TSM", "ASML", "AXTI", "AAOI",
    # 加密货币
    "COIN", "MSTR", "HOOD", "IREN",
]
HOT_SYMBOLS = INDUSTRY_POOL  # 池内全部视为热门 (扫描切片时按热度排名轮换)

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
        # 固定行业池监控 (用户要求: 只监控AI/存储/加密热门行业)
        self._symbols = [s for s in INDUSTRY_POOL if s not in ETF_BLACKLIST]
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
        self._scan_rotate = 0  # 全池轮换扫描指针 (热度股轮换)
        self._last_scan_start = 0.0  # 扫描节流: 上轮开始时间
        # 亏损冷却: symbol → 冷却截止时间戳 (重启后从 ai_memory 恢复)
        self._cooldown: dict[str, float] = self._load_cooldowns()

        # 托管 SL/TP 平仓 → 回填 AI 记忆 (复盘能看到真实止损)
        if hasattr(self._executor, "_on_position_closed"):
            self._executor._on_position_closed = self._on_exchange_close

    def _on_exchange_close(self, symbol: str, pnl: float, reason: str, close_price: float = 0):
        """交易所托管 SL/TP 触发平仓 → 回填 ai_memory。"""
        try:
            self._memory.close_decision(symbol, close_price, pnl, close_reason=reason)
            logger.info("%s 托管平仓 $%.2f (%s)", symbol, pnl, reason)
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
        """亏损冷却已禁用 — 恒返回空 (保留方法兼容, 不加载任何冷却)。"""
        return {}

    # ═══ 主循环 ═══════════════════════════════════

    async def _reconcile_open_decisions(self):
        """启动对账: 内存中 outcome=None 的决策, 若交易所已无持仓 → 托管平仓丢失, 回填。

        修复: 重启窗口期托管SL/TP平仓后, get_positions 的 stale 检测
        (依赖进程内缓存) 无法发现 → 记忆永远挂着 outcome=None。
        """
        try:
            decisions = self._memory.recent_decisions(200)
            open_ones = [d for d in decisions
                         if d.get("outcome") is None and d.get("entry")]
            if not open_ones:
                return
            # 交易所当前持仓 symbol 集合
            held = {p.symbol for p in (await self._executor.get_positions())}
            for d in open_ones:
                sym = d["symbol"]
                if sym in held:
                    continue  # 仍持仓, 正常
                # 交易所已无此持仓 → 托管平仓 (重启窗口丢失的回填)
                try:
                    q = await self._market.get_quote(sym)
                    cur = q.mark_price if q else d["entry"]
                    entry = d["entry"]
                    # 用涨跌幅估算: 空头 pnl = (entry-cur)*qty, 多头 = (cur-entry)*qty
                    # qty 未知 → 用百分比: pnl% = (cur/entry-1) 或反
                    if d["action"] in ("BUY", "STRONG_BUY"):
                        pnl_pct = (cur / entry - 1) if entry else 0
                    else:
                        pnl_pct = (1 - cur / entry) if entry else 0
                    # 估算绝对 PnL: 用该仓位名义 (约 equity/5 保证金 ×10x)
                    pnl = pnl_pct * 1.0  # 名义 ~$10, 百分比即 ~$0.1/10%
                    self._memory.close_decision(
                        sym, cur, round(pnl, 2),
                        close_reason="EXCHANGE_SLTP(重启对账)",
                        holding_hours=0)
                    logger.info("✅ 对账回填 %s: 交易所已平仓 → pnl≈$%.2f (估算)",
                               sym, pnl)
                except Exception as e:
                    logger.warning("对账 %s 失败: %s", sym, e)
        except Exception as e:
            logger.warning("启动对账失败: %s", e)

    async def _scan_symbol_ai(self, symbol: str, bench: dict):
        """并发扫描单个品种: 采集数据 → 构建AI输入 → AI决策。

        返回 (symbol, sig, ind_1h, regime, q) 或 (symbol, None, ...) — 供 run_once 串行执行。
        """
        try:
            k_1h = await self._market.get_klines(symbol, "1H", 500)
            k_5m = await self._market.get_klines(symbol, "5m", 500)
            q = await self._market.get_quote(symbol)
            if not q or len(k_1h) < 30:
                return None
            # K线落库 (实时数据追加: 5m + 合成15m + 1H)
            try:
                self._kline_store.upsert_batch(symbol, _kline_dicts(k_1h), "1H")
                self._kline_store.upsert_batch(symbol, _kline_dicts(k_5m), "5m")
                self._kline_store.upsert_batch(symbol, _aggregate_15m(k_5m), "15m")
            except Exception:
                pass
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
                except Exception:
                    pass
                news_titles = [item.title for item in news_items[:5]]
            if news_titles:
                logger.info("%s 异常波动 %+.1f%% → 注入新闻 %d 条",
                           symbol, q.change_pct * 100, len(news_titles))

            # 盘口实时压力 (orderbook 前10档)
            ob_str = ""
            try:
                book = await self._market.get_order_book(symbol, limit=10)
                ob_str = _pressure_str(book)
            except Exception:
                pass
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
            return (symbol, sig, ind_1h, regime, q)
        except Exception as e:
            logger.error("AI扫描 %s 失败: %s", symbol, e)
            return None

    async def run_once(self):
        logger.info("=== AutoTrader 单次扫描 ===")

        # 启动对账: 找回重启窗口丢失的托管平仓 (每轮轻量, open_ones 为空时秒退)
        try:
            await self._reconcile_open_decisions()
        except Exception:
            pass

        # 扫描节流: 上轮扫描若因API慢未在间隔内完成, 跳过本轮避免积压
        if time.time() - self._last_scan_start < SCAN_INTERVAL * 0.9:
            logger.warning("上轮扫描未完成(API慢), 本轮跳过避免积压")
            return
        self._last_scan_start = time.time()

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

        # 每轮直接扫描全部池子品种 (不依赖价格变动触发 — AI主动看全池)
        candidates = list(self._symbols)

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
                        # 亏损平仓不设冷却 (用户要求: 立即重新扫描)
                        try:
                            self._memory.close_decision(
                                pos.symbol, q.mark_price, close_pnl,
                                close_reason=close_reason,
                                holding_hours=_holding_hours(pos))
                        except Exception: pass
                        continue

                    # C: 周末/深夜闭市 → 只允许"收紧"方向调整 (防利润回吐), 不允许放宽
                    session = get_us_session()
                    if session in ("weekend", "closed"):
                        # 用 AI 的 HOLD 信号中 SL/TP 判断: 只收紧不平仓
                        if sig.action == "HOLD" and sig.stop_loss > 0:
                            cur_sl = pos.stop_loss or 0
                            new_sl = sig.stop_loss
                            # 多头: 新SL >= 旧SL = 上移=收紧; 空头: 新SL <= 旧SL = 下移=收紧
                            tighten = (new_sl > cur_sl) if pos.side == "LONG" else (new_sl < cur_sl)
                            if cur_sl > 0 and tighten and abs(new_sl - cur_sl) / max(cur_sl, 1e-9) > 0.002:
                                hold = "long" if pos.side == "LONG" else "short"
                                tpsl = "sell" if hold == "long" else "buy"
                                try:
                                    try:
                                        await self._executor._trader.cancel_plan_order(
                                            pos.symbol, hold, "pos_loss")
                                    except Exception:
                                        pass
                                    await self._executor._trader.place_stop_order(
                                        pos.symbol, hold, tpsl, new_sl, pos.quantity, "pos_loss")
                                    logger.info("%s %s | %s → 深夜收紧SL→$%.2f (防回吐)",
                                               pos.symbol, pos.side, session, new_sl)
                                except Exception as e:
                                    logger.warning("深夜收紧SL失败 %s: %s", pos.symbol, e)
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
                                # 先取消旧单, 再挂新单 (防旧SL残留触发)
                                try:
                                    await self._executor._trader.cancel_plan_order(
                                        pos.symbol, hold, "pos_loss")
                                except Exception:
                                    pass
                                await self._executor._trader.place_stop_order(
                                    pos.symbol, hold, tpsl, new_sl, pos.quantity, "pos_loss")
                            if tp_changed:
                                try:
                                    await self._executor._trader.cancel_plan_order(
                                        pos.symbol, hold, "pos_profit")
                                except Exception:
                                    pass
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
            # 固定行业池轮换扫描: 按热度排名 + 指针轮换, 保证全池都被 AI 看过
            ranked = self._rank_symbols(candidates, rich_quotes)
            n_slots = 10
            if len(ranked) > n_slots:
                start = (self._scan_rotate % len(ranked))
                rotated = ranked[start:] + ranked[:start]
                symbols_to_scan = rotated[:n_slots]
            else:
                symbols_to_scan = ranked
            self._scan_rotate = (self._scan_rotate + n_slots) % max(len(ranked), 1)
            logger.info("AI 原生扫描 %d 品种 (固定池轮换, 指针%d) | 大盘: %s",
                       len(symbols_to_scan), self._scan_rotate,
                       " ".join(f"{k}{v:+.1f}%" for k,v in bench.items()))
        else:
            logger.info("本轮仅管仓")

        seen = set(p.symbol for p in positions)
        to_scan = [s for s in symbols_to_scan if s not in seen]

        # 并发AI决策: 每品种独立采集数据+调用AI (总耗时≈最慢单个, 而非串行累加)
        results = await asyncio.gather(
            *(self._scan_symbol_ai(s, bench) for s in to_scan),
            return_exceptions=True)

        # 串行执行开仓 (防并发超持仓上限/同品种重复)
        for r in results:
            if isinstance(r, Exception) or r is None:
                continue
            symbol, sig, ind_1h, regime, q = r
            try:
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
                logger.error("AI执行 %s 失败: %s", symbol, e)

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
        """固定行业池刷新: 只对 INDUSTRY_POOL 做流动性/冷却过滤, 不从全市场选股。"""
        try:
            now = time.time()
            # 池内品种行情 (流动性过滤: 成交额 ≥ MIN_TURNOVER_24H)
            rich = {}
            for h in INDUSTRY_POOL:
                if h in ETF_BLACKLIST:
                    continue
                try:
                    q = await self._market.get_quote(h)
                    if q and q.mark_price > 0 and (q.volume_24h or 0) > 0 \
                            and (q.turnover_24h or 0) >= MIN_TURNOVER_24H:
                        rich[h] = {
                            "volume_24h": q.volume_24h,
                            "change_pct": abs(q.change_pct),
                            "turnover_24h": q.turnover_24h,
                        }
                except Exception:
                    pass

            # 保留现有持仓品种 (不管流动性/冷却)
            positions = await self._executor.get_positions()
            held = {p.symbol for p in positions}

            # 过滤亏损冷却中的品种 (冷却已禁用, 全部保留)
            active = [s for s in rich]
            new_pool = list(active)
            for h in held:
                if h not in new_pool:
                    new_pool.append(h)
            # 保证池非空 (极端情况: 全部流动性不足 → 保留原池)
            if len(new_pool) < 5 and self._symbols:
                logger.warning("固定池过滤后过少(%d), 保留原池", len(new_pool))
                return

            self._symbols = new_pool
            logger.info("品种池刷新: %d 个 (固定行业池 %d, 流动性≥$%.0fM)",
                       len(new_pool), len(INDUSTRY_POOL), MIN_TURNOVER_24H / 1e6)
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
