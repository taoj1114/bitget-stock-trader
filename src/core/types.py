"""标准化数据模型 — 所有模块间的数据传输对象 (DTO)"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional
from datetime import datetime


# ==================== 基础类型 ====================

@dataclass
class ContractInfo:
    """合约信息"""
    symbol: str                      # 短名: AAPL
    raw_symbol: str                  # 原始名: AAPLUSDT
    symbol_type: str                 # stock / index / etf
    status: str                      # online / offline
    min_leverage: int
    max_leverage: int
    price_precision: int
    qty_precision: int
    min_order_qty: float
    fund_interval: int               # 资金费率结算间隔 (小时)


@dataclass
class Quote:
    """实时报价"""
    symbol: str
    price: float
    open_24h: float
    high_24h: float
    low_24h: float
    change_pct: float                # 小数: 0.0153 = +1.53%
    volume_24h: float
    turnover_24h: float
    index_price: float               # 指数价
    mark_price: float                # 标记价
    funding_rate: float              # 资金费率
    open_interest: float             # 持仓量
    bid: float = 0.0
    ask: float = 0.0
    bid_size: float = 0.0
    ask_size: float = 0.0
    timestamp: int = 0               # Unix ms


@dataclass
class Kline:
    """K线数据"""
    timestamp: int                   # Unix ms
    open: float
    high: float
    low: float
    close: float
    volume: float
    turnover: float                  # 成交额


@dataclass
class OrderBookLevel:
    """订单本档位"""
    price: float
    size: float


@dataclass
class OrderBook:
    """订单本"""
    symbol: str
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]
    timestamp: int


# ==================== 基本面类型 ====================

@dataclass
class FundamentalData:
    """基本面数据"""
    symbol: str
    report_date: str                 # 财报日期
    revenue: Optional[float] = None
    revenue_yoy: Optional[float] = None   # 营收同比 (%)
    net_profit: Optional[float] = None
    net_profit_yoy: Optional[float] = None  # 净利润同比 (%)
    eps: Optional[float] = None
    roe: Optional[float] = None      # ROE (%)
    gross_margin: Optional[float] = None  # 毛利率 (%)
    net_margin: Optional[float] = None    # 净利率 (%)
    debt_ratio: Optional[float] = None    # 资产负债率 (%)


# ==================== 新闻类型 ====================

@dataclass
class NewsItem:
    """新闻条目"""
    title: str
    snippet: str
    url: str
    source: str = "unknown"
    published_at: Optional[str] = None   # SearXNG 可能无时间戳
    sentiment_score: Optional[float] = None  # -1 ~ 1, None=未打分


# ==================== 技术指标类型 ====================

@dataclass
class TechnicalIndicators:
    """技术指标计算结果"""
    symbol: str
    timestamp: int

    # 均线
    ma5: float = 0.0
    ma10: float = 0.0
    ma20: float = 0.0
    ma60: float = 0.0
    ema12: float = 0.0
    ema26: float = 0.0

    # 均线前一周期 (用于金叉/死叉检测)
    ma10_prev: float = 0.0
    ma30_prev: float = 0.0

    # RSI
    rsi14: float = 50.0

    # MACD
    macd: float = 0.0
    macd_signal: float = 0.0
    macd_hist: float = 0.0

    # 布林带
    bb_upper: float = 0.0
    bb_middle: float = 0.0
    bb_lower: float = 0.0

    # ATR
    atr14: float = 0.0

    # 成交量
    volume_ratio: float = 1.0        # 当前/20日均量
    volume_ma20: float = 0.0

    # 其他
    funding_rate: float = 0.0        # 当前资金费率
    change_pct: float = 0.0          # 24h涨跌幅


# ==================== 市场状态类型 ====================

@dataclass
class MarketRegime:
    """市场状态"""
    regime: str                      # trend_up / trend_down / range_bound / weak_trend
    volatility: str                  # high / normal / low
    adx: float = 0.0
    scores: dict[str, float] = field(default_factory=dict)


# ==================== 信号类型 ====================

@dataclass
class Signal:
    """交易信号"""
    strategy_id: str
    symbol: str
    action: str                      # STRONG_BUY / BUY / SELL / STRONG_SELL
    confidence: float                # 0 ~ 1
    entry_price: float
    stop_loss: float
    take_profits: list[float]        # 分级止盈价
    reason: str                      # 信号理由
    timestamp: int = 0               # Unix ms


@dataclass
class EffectiveSignal:
    """去重后的有效信号"""
    signal: Signal
    weight: float = 1.0


@dataclass
class ScoreResult:
    """评分结果"""
    total_score: float               # 0 ~ 100
    action: str                      # STRONG_BUY / BUY / HOLD / SELL / STRONG_SELL
    breakdown: dict[str, float]      # 各维度得分
    details: dict = field(default_factory=dict)


@dataclass
class AnalysisContext:
    """传递给策略的上下文"""
    symbol: str
    quote: Quote
    klines: list[Kline]
    indicators: TechnicalIndicators
    fundamentals: Optional[FundamentalData]
    news: list[NewsItem]
    market_regime: MarketRegime
    current_position: Optional["Position"] = None
    news_sentiment: Optional[dict] = None  # {"positive": x, "negative": x, "neutral": x}


# ==================== 交易类型 ====================

@dataclass
class Position:
    """持仓"""
    id: str
    symbol: str
    side: str                        # LONG / SHORT
    quantity: float
    entry_price: float
    mark_price: float
    stop_loss: float
    take_profit_levels: list
    unrealized_pnl: float = 0.0
    opened_at: datetime = None
    strategy_id: str = ""


@dataclass
class Order:
    """订单"""
    symbol: str
    side: str
    quantity: float
    order_type: str = "MARKET"       # MARKET / LIMIT
    price: float = 0.0
    stop_loss: float = 0.0
    take_profits: list = field(default_factory=list)


@dataclass
class OrderResult:
    """订单执行结果"""
    status: str                      # FILLED / REJECTED / PENDING
    position_id: str = ""
    fill_price: float = 0.0
    fill_quantity: float = 0.0
    reason: str = ""
    timestamp: int = 0


@dataclass
class TradeRecord:
    """交易记录"""
    timestamp: datetime
    symbol: str
    side: str
    type: str                        # OPEN / CLOSE / SL / TP
    price: float
    quantity: float
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    reason: str = ""
    strategy_id: str = ""
    spread: float = 0.0
    funding_cost: float = 0.0
    holding_hours: float = 0.0
    market_regime: str = ""
    signal_score: float = 0.0


@dataclass
class AccountBalance:
    """账户余额"""
    initial_capital: float
    current_balance: float
    total_pnl: float = 0.0
    used_margin: float = 0.0
    total_funding_cost: float = 0.0
    total_trades: int = 0
    win_count: int = 0
    loss_count: int = 0

    @property
    def available_balance(self) -> float:
        """可用余额"""
        return self.current_balance - self.used_margin


# ==================== 策略参数类型 ====================

@dataclass
class StrategyParams:
    """策略参数基类"""
    enabled: bool = True
    weight: float = 1.0


@dataclass
class TrendBreakParams(StrategyParams):
    """趋势突破策略参数"""
    fast_ma: int = 10
    slow_ma: int = 30
    volume_ratio_threshold: float = 1.5
    rsi_upper: float = 70
    rsi_lower: float = 30
    atr_sl_multiplier: float = 2.0
    take_profit_1: float = 0.03
    take_profit_2: float = 0.05
    stop_loss: float = 0.05
    max_leverage: int = 1
    min_score: float = 65
    cooldown_bars: int = 48


@dataclass
class RsiBounceParams(StrategyParams):
    """RSI反弹策略参数"""
    rsi_period: int = 14
    oversold: int = 30
    overbought: int = 70
    macd_divergence: bool = True
    atr_sl_multiplier: float = 1.5


@dataclass
class AiCompositeParams(StrategyParams):
    """AI综合策略参数"""
    model: str = "deepseek-v4-pro"
    min_confidence: float = 0.6


# ==================== 绩效类型 ====================

@dataclass
class PerformanceMetrics:
    """策略绩效指标"""
    strategy_id: str
    trades: int = 0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    total_pnl: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    avg_holding_hours: float = 0.0


@dataclass
class StrategyStatus:
    """策略运行状态"""
    phase: str                       # evaluation / active
    weight: float = 0.0
    trades_done: int = 0
    trades_needed: int = 0
    message: str = ""


# ==================== 安全类型 ====================

@dataclass
class SafetyVerdict:
    """风控判定"""
    passed: bool
    reason: str = ""
    layer: str = ""                  # hard / conditional / circuit_breaker


@dataclass
class SafetyEvent:
    """安全事件日志"""
    timestamp: datetime
    rule: str
    verdict: SafetyVerdict
    context: dict = field(default_factory=dict)
