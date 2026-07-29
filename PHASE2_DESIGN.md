# Phase 2 设计文档 — 分析引擎 + 策略系统

> ⚠️ 本阶段只产出设计文档和伪代码，不写实现代码。
> 所有代码文件标记为 `TODO[Phase2]`，具体实现由编码阶段完成。

---

## 一、Phase 2 总览

### 新增模块

| 模块 | 文件 | 职责 |
|------|------|------|
| 技术指标 | `src/analyzers/technical.py` | 从 K线 计算 MA/RSI/MACD/ATR/布林带/成交量 |
| 市场状态 | `src/analyzers/market_regime.py` | ADX + 均线排列 → 趋势/震荡/高波动 |
| 新闻情绪 | `src/analyzers/sentiment.py` | 新闻文本 → 正面/负面/中性 打分 |
| 策略接口 | `src/strategies/base.py` | SignalStrategy 抽象 + AnalysisContext |
| 趋势突破策略 | `src/strategies/trend_break.py` | 金叉 + 放量 + RSI 过滤 |
| RSI 反弹策略 | `src/strategies/rsi_bounce.py` | 超卖 + MACD 底背离 |
| AI 综合策略 | `src/strategies/ai_composite.py` | DeepSeek v4-pro 全局分析 |
| 策略注册器 | `src/strategies/registry.py` | 策略发现 + 冷启动 + 热加载 |
| 策略趋同检测 | `src/strategies/convergence.py` | 信号相关性去重 |
| 信号评分器 | `src/signals/scorer.py` | 多因子评分 |
| 信号聚合器 | `src/signals/aggregator.py` | 多策略信号合并 |
| AI 分析器 | `src/analyzers/ai.py` | DeepSeek API 调用 |

### 数据流

```
Bitget K线 ──→ 技术指标 ──┐
Eastmoney  ──→ 基本面评分 ──┤
SearXNG    ──→ 情绪分析 ───┤
                          ▼
                    AnalysisContext (统一上下文)
                          │
           ┌──────────────┼──────────────┐
           ▼              ▼              ▼
     TrendBreak    RSIBounce    AIComposite
           │              │              │
           └──────────────┼──────────────┘
                          ▼
                    ConvergenceDetector (去重)
                          ▼
                    Scorer (多因子评分)
                          ▼
                    Signal (输出到 Phase 3)
```

---

## 二、技术指标模块

### 接口定义

```python
# src/analyzers/technical.py

class TechnicalAnalyzer:
    """
    纯函数式技术指标计算器。
    不依赖外部状态，输入 Kline[]，输出 TechnicalIndicators。
    """

    def calculate(self, klines: list[Kline]) -> TechnicalIndicators:
        """
        从 K线列表计算所有技术指标。

        输入:
            klines: 至少 60 根 K 线（按时间升序）

        输出:
            TechnicalIndicators {
                ma5, ma10, ma20, ma60,
                ema12, ema26,
                rsi14,
                macd, macd_signal, macd_hist,
                bb_upper, bb_middle, bb_lower,
                atr14,
                volume_ratio, volume_ma20,
                change_pct,
                ma10_prev, ma30_prev,  # 前一周期值（金叉检测）
            }
        """
```

### 指标计算公式

```
MA(close, N) = sum(close[-N:]) / N

EMA(close, N):
    multiplier = 2 / (N + 1)
    ema[0] = close[0]
    ema[i] = (close[i] - ema[i-1]) * multiplier + ema[i-1]

RSI(close, 14):
    gains = [max(close[i] - close[i-1], 0) for i in range(1, len(close))]
    losses = [max(close[i-1] - close[i], 0) for i in range(1, len(close))]
    avg_gain = sma(gains, 14)
    avg_loss = sma(losses, 14)
    rs = avg_gain / avg_loss if avg_loss > 0 else 100
    rsi = 100 - (100 / (1 + rs))

MACD(close, 12, 26, 9):
    macd_line = EMA(close, 12) - EMA(close, 26)
    signal_line = EMA(macd_line, 9)
    histogram = macd_line - signal_line

布林带(close, 20, 2):
    middle = SMA(close, 20)
    std = stddev(close[-20:])
    upper = middle + 2 * std
    lower = middle - 2 * std

ATR(high, low, close, 14):
    tr = [max(high[i]-low[i], abs(high[i]-close[i-1]), abs(low[i]-close[i-1]))
          for i in range(1, len(close))]
    atr = EMA(tr, 14)

成交量比:
    volume_ma20 = SMA(volume, 20)
    volume_ratio = volume[-1] / volume_ma20 if volume_ma20 > 0 else 1.0
```

### 边界处理

```python
# 特殊情况
- K线数量不足 60 根 → 抛出 InsufficientDataError
- 成交量全为 0 → volume_ratio = 1.0
- 价格不变 → RSI = 50, MACD = 0
- ATR 无法计算（连续相同价格）→ 返回 0

# 金叉/死叉检测
ma10_prev = ma10 的前一根 K 线值
ma30_prev = ma30 的前一根 K 线值
golden_cross = ma10 > ma30 and ma10_prev <= ma30_prev
death_cross = ma10 < ma30 and ma10_prev >= ma30_prev
```

---

## 三、市场状态识别器

```python
# src/analyzers/market_regime.py

class MarketRegimeDetector:
    """
    识别当前市场状态。
    使用 ADX + 均线排列 + ATR 分位 综合判断。
    """

    LOOKBACK = 50  # 分析窗口

    async def detect(
        self,
        klines: list[Kline],
        indicators: TechnicalIndicators,
    ) -> MarketRegime:
        """
        输出:
            MarketRegime {
                regime: "trend_up" | "trend_down" | "range_bound" | "weak_trend"
                volatility: "high" | "normal" | "low"
                adx: float
                scores: {
                    "trend_up": 0.0~1.0,
                    "trend_down": 0.0~1.0,
                    "range_bound": 0.0~1.0,
                }
            }
        """
```

### 判断逻辑

```python
def _calc_adx(high, low, close, period=14):
    """
    ADX (Average Directional Index)
    > 25: 趋势行情
    < 20: 震荡行情
    20-25: 弱趋势
    """
    # +DM = high[i] - high[i-1]  (当 high[i] - high[i-1] > low[i-1] - low[i])
    # -DM = low[i-1] - low[i]    (当 low[i-1] - low[i] > high[i] - high[i-1])
    # TR = max(high-low, abs(high-prev_close), abs(low-prev_close))
    # +DI = EMA(+DM, period) / EMA(TR, period) * 100
    # -DI = EMA(-DM, period) / EMA(TR, period) * 100
    # DX = abs(+DI - -DI) / (+DI + -DI) * 100
    # ADX = EMA(DX, period)
    pass


def _detect_regime(adx, ma_short, ma_long, atr_percentile):
    """
    ┌─────────────────┬──────────┬──────────────────────────────────────┐
    │   条件           │  状态     │  策略权重调整                        │
    ├─────────────────┼──────────┼──────────────────────────────────────┤
    │ ADX>25 + 短>长   │ trend_up │  趋势策略 ×1.5, 反转策略 ×0.5       │
    │ ADX>25 + 短<长   │ trend_down│ 空头信号权重↑                      │
    │ ADX<20           │ range_bound│ 反转策略 ×1.5, 趋势策略 ×0.5      │
    │ 20<ADX<25        │ weak_trend│  不作调整                          │
    │ ATR>85%分位      │ volatility=high │ 杠杆/仓位 ×0.7, 止损×1.2    │
    │ ATR<15%分位      │ volatility=low  │ 杠杆/仓位 ×1.0               │
    └─────────────────┴──────────┴──────────────────────────────────────┘
    """
    pass
```

---

## 四、策略系统

### 策略基类

```python
# src/strategies/base.py

class SignalStrategy(ABC):
    """所有策略的基类。新增策略只需继承此类。"""

    @property
    @abstractmethod
    def id(self) -> str:
        """策略唯一标识（用于注册和日志）"""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """策略可读名称"""
        ...

    @property
    def params(self) -> StrategyParams:
        """当前参数。支持通过配置热更新。"""
        return self._params

    @params.setter
    def params(self, p: StrategyParams):
        """修改参数（热更新用）"""
        self._params = p

    @abstractmethod
    async def evaluate(self, ctx: AnalysisContext) -> Optional[Signal]:
        """
        评估并返回交易信号。

        Args:
            ctx: 包含所有可用数据
                - symbol, quote, klines, indicators
                - fundamentals, news
                - market_regime, current_position

        Returns:
            Signal | None
                Signal.action: "STRONG_BUY" | "BUY" | "SELL" | "STRONG_SELL"
                Signal.confidence: 0.0 ~ 1.0
                Signal.reason: 人类可读的理由
        """
        ...
```

### AnalysisContext 结构

```python
@dataclass
class AnalysisContext:
    """传递给每个策略的上下文。策略只依赖这个对象。"""

    # 基础信息
    symbol: str

    # 市场数据
    quote: Quote                       # 当前实时报价
    klines: list[Kline]                # K线数据（用于技术分析）
    indicators: TechnicalIndicators     # 预计算的技术指标

    # 基本面 + 新闻
    fundamentals: Optional[FundamentalData]
    news: list[NewsItem]

    # 市场状态
    market_regime: MarketRegime         # 由 MarketRegimeDetector 提供

    # 当前持仓（如果有）
    current_position: Optional[Position]

    # 新闻情绪（预计算）
    news_sentiment: Optional[dict] = None  # {"positive": 0.x, "negative": 0.x, "neutral": 0.x}

    # 辅助方法
    @property
    def is_long(self) -> bool:
        return self.current_position is not None and self.current_position.side == "LONG"

    @property
    def is_short(self) -> bool:
        return self.current_position is not None and self.current_position.side == "SHORT"

    @property
    def has_position(self) -> bool:
        return self.current_position is not None
```

### 策略 A: 趋势突破策略

```python
# src/strategies/trend_break.py

class TrendBreakStrategy(SignalStrategy):
    """
    趋势突破策略。

    信号条件:
        买入: MA10 上穿 MA30 + 成交量放大 1.5x + RSI 不过热(<70)
        卖出: MA10 下穿 MA30 + 成交量放大 1.5x

    评分加权:
        bull_flags = 0
        + 金叉 (MA10 > MA30 && MA10_prev <= MA30_prev)
        + 趋势确认 (MA30 > MA30_prev)
        + 放量 (volume_ratio > threshold)
        + RSI 不过热 (RSI < 70)
        + 市场状态支持 (trend_up 或 weak_trend)

    bull_flags >= 3 → BUY, >= 4 → STRONG_BUY
    """

    id = "trend_break"
    name = "趋势突破"
    params: TrendBreakParams

    async def evaluate(self, ctx: AnalysisContext) -> Optional[Signal]:
        p = self.params
        ind = ctx.indicators

        # ==== 多头检查 ====
        bull_flags = 0

        # 金叉
        if ind.ma10 > ind.ma30 and ind.ma10_prev <= ind.ma30_prev:
            bull_flags += 1

        # 趋势确认
        if ind.ma30 > ind.ma30_prev:
            bull_flags += 1

        # 放量
        if ind.volume_ratio > p.volume_ratio_threshold:
            bull_flags += 1

        # RSI 不过热
        if ind.rsi14 < p.rsi_upper:
            bull_flags += 1

        # 市场状态调节
        if ctx.market_regime.regime not in ("trend_up", "weak_trend"):
            bull_flags -= 1  # 非趋势市场降权

        # ==== 冷却期检查 ====
        if self._in_cooldown(ctx.symbol, "BUY", ctx.klines, p.cooldown_bars):
            bull_flags = 0  # 冷却期内不生成信号

        # ==== 已有持仓检查 ====
        if ctx.has_position and ctx.current_position.side == "LONG":
            bull_flags = 0  # 已有多头不重复开

        # ==== 输出信号 ====
        if bull_flags >= 3:
            entry = ctx.quote.mark_price
            atr = ind.atr14
            return Signal(
                strategy_id=self.id,
                symbol=ctx.symbol,
                action="STRONG_BUY" if bull_flags >= 4 else "BUY",
                confidence=bull_flags / 4,
                entry_price=entry,
                stop_loss=entry - (atr * p.atr_sl_multiplier),
                take_profits=[
                    entry * (1 + p.take_profit_1),
                    entry * (1 + p.take_profit_2),
                ],
                reason=self._build_reason(bull_flags, ind),
            )

        # ==== 空头 (死叉) ====
        bear_flags = 0
        if ind.ma10 < ind.ma30 and ind.ma10_prev >= ind.ma30_prev:
            bear_flags += 1
        if ind.volume_ratio > p.volume_ratio_threshold:
            bear_flags += 1
        if ctx.market_regime.regime in ("trend_down", "weak_trend"):
            bear_flags += 1

        if bear_flags >= 2:
            entry = ctx.quote.mark_price
            return Signal(
                strategy_id=self.id,
                symbol=ctx.symbol,
                action="SELL",
                confidence=bear_flags / 3,
                entry_price=entry,
                stop_loss=entry * (1 + p.take_profit_1),  # 做空止损在上方
                take_profits=[entry * (1 - p.take_profit_1)],
                reason=f"死叉: MA{ind.ma10:.1f}<{ind.ma30:.1f} 放量{ind.volume_ratio:.1f}x",
            )

        return None
```

### 策略 B: RSI 反弹策略

```python
# src/strategies/rsi_bounce.py

class RsiBounceStrategy(SignalStrategy):
    """
    RSI 超卖反弹策略。

    信号条件:
        买入: RSI(14) < 30 (超卖) + MACD 底背离 + 接近支撑位
        卖出: RSI(14) > 70 (超买) + MACD 顶背离

    特点:
        - 逆势策略，在震荡市效果最佳
        - 需要 MACD 背离确认，避免"下跌接飞刀"
        - 支撑位 = 最近 20 根 K 线最低点
    """

    id = "rsi_bounce"
    name = "RSI 超卖反弹"
    params: RsiBounceParams

    async def evaluate(self, ctx: AnalysisContext) -> Optional[Signal]:
        p = self.params
        ind = ctx.indicators

        # 震荡市权重提升，趋势市降权
        if ctx.market_regime.regime == "trend_up" and ind.rsi14 < p.oversold:
            # 上升趋势中的超卖 = 回调买入机会
            pass  # 继续评估
        elif ctx.market_regime.regime == "trend_down" and ind.rsi14 > p.overbought:
            return None  # 下降趋势中的超买 = 继续跌，不做空
        elif ctx.market_regime.regime == "range_bound":
            pass  # 震荡市是最佳环境
        else:
            pass

        # ==== 超卖买入 ====
        if ind.rsi14 < p.oversold:
            # MACD 底背离检测
            macd_divergence = self._detect_bullish_divergence(ctx.klines, ind)
            # 接近支撑位
            near_support = self._near_support(ctx.klines, ind.atr14)

            score = 0
            if ind.rsi14 < p.oversold:
                score += 1
            if macd_divergence:
                score += 1
            if near_support:
                score += 1

            if score >= 2:
                return Signal(
                    strategy_id=self.id,
                    symbol=ctx.symbol,
                    action="BUY",
                    confidence=score / 3,
                    entry_price=ctx.quote.mark_price,
                    stop_loss=ctx.quote.mark_price - (ind.atr14 * p.atr_sl_multiplier),
                    take_profits=[ctx.quote.mark_price * 1.03],
                    reason=f"RSI({ind.rsi14:.0f})超卖{' MACD底背离' if macd_divergence else ''}{' 近支撑' if near_support else ''}",
                )

        # ==== 超买卖出 ====
        if ind.rsi14 > p.overbought:
            bear_divergence = self._detect_bearish_divergence(ctx.klines, ind)
            near_resistance = self._near_resistance(ctx.klines, ind.atr14)

            score = 0
            if ind.rsi14 > p.overbought:
                score += 1
            if bear_divergence:
                score += 1
            if near_resistance:
                score += 1

            if score >= 2:
                return Signal(
                    strategy_id=self.id,
                    symbol=ctx.symbol,
                    action="SELL",
                    confidence=score / 3,
                    entry_price=ctx.quote.mark_price,
                    stop_loss=ctx.quote.mark_price * 1.03,
                    take_profits=[ctx.quote.mark_price * 0.97],
                    reason=f"RSI({ind.rsi14:.0f})超买{' MACD顶背离' if bear_divergence else ''}",
                )

        return None

    def _detect_bullish_divergence(self, klines, ind) -> bool:
        """
        MACD 底背离:
            价格创新低 (close < prev_low)
            但 MACD 没有创新低 (macd > prev_macd)
        """
        # 取最近两根 K 线的低点和 MACD 值比较
        if len(klines) < 5:
            return False
        price_lower = klines[-1].low < klines[-3].low
        macd_higher = ind.macd > self._prev_macd
        return price_lower and macd_higher
```

### 策略 C: AI 综合策略

```python
# src/strategies/ai_composite.py

class AICompositeStrategy(SignalStrategy):
    """
    AI 综合决策策略。

    流程:
        1. 构建包含技术面 + 基本面 + 新闻的 prompt
        2. 调用 DeepSeek v4-pro 分析
        3. 解析 AI 输出为结构化信号

    Prompt 结构:
        ```
        你是一位美股交易分析师。分析以下 {symbol} 的数据。

        ## 技术指标 (1h K线)
        - 当前价: {price} (24h涨跌: {change_pct}%)
        - MA10: {ma10} MA30: {ma30} 状态: {金叉/死叉}
        - RSI(14): {rsi14} ({超买/超卖/正常})
        - MACD: {macd} 信号: {macd_signal} ({金叉/死叉})
        - 布林带: 上轨{bb_upper} 中轨{bb_middle} 下轨{bb_lower}
        - ATR(14): {atr14}
        - 成交量: {volume_ratio}x 20日均量

        ## 市场状态
        - {trend_up / range_bound / trend_down}
        - 波动率: {high / normal / low}

        ## 基本面
        - 最近财报: {report_date}
        - 营收同比: {revenue_yoy}%
        - ROE: {roe}%
        - 净利润同比: {net_profit_yoy}%

        ## 新闻情绪
        {news_sentiment}

        ## 已有持仓
        {position_info}

        请分析并输出 JSON:
        {
            "trend_judgment": "up/down/sideways",
            "confidence": 0.0-1.0,
            "action": "STRONG_BUY/BUY/HOLD/SELL/STRONG_SELL",
            "reason": "中文理由",
            "key_levels": {"support": x, "resistance": x},
            "risk_warning": "风险提示"
        }
        ```
    """

    id = "ai_composite"
    name = "AI 综合决策"
    params: AiCompositeParams

    async def evaluate(self, ctx: AnalysisContext) -> Optional[Signal]:
        # 构建 prompt
        prompt = self._build_prompt(ctx)

        # 调用 DeepSeek
        result = await self._call_ai(prompt)

        # 解析 JSON 响应
        signal = self._parse_response(result, ctx)

        return signal
```

---

## 五、信号系统

### 评分器

```python
# src/signals/scorer.py

class SignalScorer:
    """
    多因子信号评分器。

    评分公式:
        total = technical_score × 0.35
              + ai_score × 0.30
              + fundamental_score × 0.20
              + sentiment_score × 0.15

    决策阈值:
        ≥80: STRONG_BUY
        65-79: BUY
        35-64: HOLD
        20-34: SELL
        <20: STRONG_SELL
    """

    WEIGHTS = {
        "technical": 0.35,
        "ai": 0.30,
        "fundamental": 0.20,
        "sentiment": 0.15,
    }

    # 市场状态 → 策略类型权重调节
    REGIME_ADJUSTMENT = {
        "trend_up":    {"trend": 1.5, "reversal": 0.5, "ai": 1.0},
        "trend_down":  {"trend": 1.3, "reversal": 0.5, "ai": 1.0},
        "range_bound": {"trend": 0.5, "reversal": 1.5, "ai": 1.0},
        "weak_trend":  {"trend": 1.0, "reversal": 1.0, "ai": 1.0},
    }

    STRATEGY_TYPES = {
        "trend_break": "trend",
        "ma_alignment": "trend",
        "rsi_bounce": "reversal",
        "ai_composite": "ai",
    }

    async def score(
        self,
        effective_signals: list[EffectiveSignal],
        market_regime: MarketRegime,
        fundamentals: Optional[FundamentalData],
        news_sentiment: Optional[dict],
        indicators: TechnicalIndicators,
    ) -> ScoreResult:
        """
        综合评分。

        Args:
            effective_signals: 经过去重后的有效信号
            market_regime: 当前市场状态
            fundamentals: 基本面数据
            news_sentiment: 新闻情绪 {"pos": 0.x, "neg": 0.x, "neu": 0.x}
            indicators: 技术指标

        Returns:
            ScoreResult {
                total_score: 0-100,
                action: STRONG_BUY|BUY|HOLD|SELL|STRONG_SELL,
                breakdown: {
                    technical: float,
                    ai: float,
                    fundamental: float,
                    sentiment: float,
                }
            }
        """

    def _calc_technical_score(
        self, signals: list[EffectiveSignal], regime: MarketRegime
    ) -> float:
        """
        技术分计算。

        遍历每个有效信号:
        1. 确定策略类型 (trend / reversal / ai)
        2. 根据市场状态获取权重调整系数
        3. score += confidence × weight × regime_adjustment

        technical_score = min(total / len(signals) * 100, 100)
        """
        ...

    def _calc_fundamental_score(self, fundamentals: FundamentalData) -> float:
        """
        基本面分计算。基准 50 分。

        加分项:
            revenue_yoy > 20%  → +15
            net_profit_yoy > 20% → +10
            roe > 15%           → +10
            gross_margin > 40%  → +5

        减分项:
            debt_ratio > 70%    → -10
            net_profit < 0      → -20

        score = clamp(50 + adjustments, 0, 100)
        """
        ...

    def _calc_sentiment_score(self, sentiment: dict) -> float:
        """
        情绪分计算。

        score = (positive × 100) - (negative × 50) + (neutral × 50)
        score = clamp(score, 0, 100)
        """
        ...
```

### 信号聚合器

```python
# src/signals/aggregator.py

class SignalAggregator:
    """
    信号聚合器。

    职责:
        1. 收集所有策略的输出信号
        2. 按 symbol 分组
        3. 同一 symbol 多空信号冲突时 → HOLD
        4. 调用 Scorer 评分
    """

    async def aggregate(
        self,
        symbols: list[str],
        strategy_signals: dict[str, list[Signal]],  # symbol → [Signal, ...]
        scoring_context: dict,
    ) -> list[ScoreResult]:
        """
        按 symbol 聚合信号并评分。

        冲突处理:
            - 同一 symbol 同时有 BUY + SELL → HOLD，记录冲突
            - 同一策略的重复信号 → 只保留最近一个
            - 冷却期内的重复方向信号 → 丢弃
        """
        ...
```

---

## 六、AI 分析器

```python
# src/analyzers/ai.py

class AIAnalyzer:
    """
    DeepSeek AI 分析器。

    职责:
        1. 从 AnalysisContext 构建结构化的分析 prompt
        2. 调用 OpenCode Go API (DeepSeek v4-pro)
        3. 解析 AI 返回的 JSON
        4. 返回结构化评分 + 建议
    """

    API_BASE = "https://opencode.ai/zen/go/v1"
    MODEL = "deepseek-v4-pro"

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or os.environ.get("OPENCODE_API_KEY", "")

    async def analyze(
        self, ctx: AnalysisContext
    ) -> Optional[dict]:
        """
        调用 AI 分析并返回结构化结果。

        返回:
            {
                "trend_judgment": "up|down|sideways",
                "confidence": 0.0-1.0,
                "action": "BUY|SELL|HOLD",
                "score": 0-100,          # AI 独立评分
                "reason": "...",
                "key_levels": {"support": x, "resistance": x},
                "risk_warning": "...",
            }
        """

    def _build_prompt(self, ctx: AnalysisContext) -> str:
        """
        构建结构化 prompt。

        技术指标部分:
            最近 20 根 K 线的高/低/收
            MA/RSI/MACD/布林带/ATR/成交量

        市场状态部分:
            regime + volatility + ADX

        基本面部分:
            财报摘要 (如果有)

        新闻部分:
            标题 + 摘要列表 (标记 ⚠️ 无确切时间戳)

        已有持仓部分:
            方向 + 盈亏 + 持仓时间
        """

    async def _call_api(self, prompt: str) -> str:
        """
        调用 OpenCode Go API。

        POST /v1/chat/completions
        {
            "model": "deepseek-v4-pro",
            "messages": [
                {"role": "system", "content": "你是一个美股交易分析师..."},
                {"role": "user", "content": prompt}
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.3,
            "max_tokens": 1000,
        }
        """

    def _parse_response(self, raw: str) -> Optional[dict]:
        """
        解析 AI 返回。
        尝试 JSON 解析，失败时 fallback 到正则提取 + 默认值。
        """
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Fallback: 从文本中提取关键字段
            return self._fallback_parse(raw)
```

---

## 七、文件清单

### 新增文件

| 文件 | 状态 |
|------|------|
| `src/analyzers/technical.py` | `# TODO[Phase2]` 伪代码完成 |
| `src/analyzers/market_regime.py` | `# TODO[Phase2]` 伪代码完成 |
| `src/analyzers/sentiment.py` | `# TODO[Phase2]` 设计完成 |
| `src/analyzers/ai.py` | `# TODO[Phase2]` 伪代码完成 |
| `src/strategies/base.py` | `# TODO[Phase2]` 接口定义 |
| `src/strategies/trend_break.py` | `# TODO[Phase2]` 伪代码完成 |
| `src/strategies/rsi_bounce.py` | `# TODO[Phase2]` 伪代码完成 |
| `src/strategies/ai_composite.py` | `# TODO[Phase2]` 伪代码完成 |
| `src/strategies/registry.py` | `# TODO[Phase2]` 设计完成 |
| `src/strategies/convergence.py` | `# TODO[Phase2]` 设计完成 |
| `src/strategies/params.py` | `# TODO[Phase2]` 参数模型已定义 |
| `src/signals/scorer.py` | `# TODO[Phase2]` 伪代码完成 |
| `src/signals/aggregator.py` | `# TODO[Phase2]` 伪代码完成 |

### 修改文件

| 文件 | 改动 |
|------|------|
| `src/core/types.py` | 补充 TechnicalIndicators 字段（A 股的行业指标等） |
| `config.yaml` | 添加 strategies: 配置段 |
| `main.py` | 添加 --analyze 和 --scan 命令的完整实现 |

---

## 八、验收标准

```
Phase 2 完成时，以下命令必须可用:

1. python main.py --analyze AAPL
   输出: 完整的分析报告 (JSON)
   ├── 技术指标 (MA/RSI/MACD/布林带/ATR/成交量)
   ├── 市场状态 (趋势/震荡/波动率)
   ├── 基本面评分
   ├── 新闻情绪
   ├── 各策略信号
   ├── 综合评分 + 动作建议
   └── AI 分析

2. python main.py --scan
   输出: 所有配置品种的扫描结果
   ├── 按评分排序
   ├── 每个品种: 综合评分 + 动作 + 理由
   └── Top 3 推荐

3. 策略可插拔:
   ├── config.yaml 中新增 strategy → 自动发现
   ├── 禁用策略 → 不评估
   └── 修改参数 → 热生效
```

---

> **下一步**: 确认设计无误后，回复 **编码开始** 进入 Phase 2 实现阶段。
