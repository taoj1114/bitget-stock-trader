# 独立测试方案

> 每层模块可独立测试，无依赖顺序。测试文件放 `tests/` 目录下。

---

## 一、技术原则

每条测试:
1. **不启动 main.py** — 直接 import 目标模块
2. **不依赖网络** — httpx 用 `respx` mock，SeaeXNG 用 fake response
3. **不依赖数据库** — Tracker 用 `:memory:` SQLite
4. **纯函数直接测** — TechnicalAnalyzer、Scorer 等传入数据断言返回值
5. **状态模块造状态** — PaperExecutor 构造好 balance/positions 再测

---

## 二、模块测试方法

### 数据源层 (Phase 1)

```
tests/datasources/
├── test_bitget_market.py
├── test_bitget_symbols.py
├── test_eastmoney_search.py
├── test_searxng_news.py
└── test_cache.py
```

**测试方法**: mock HTTP 请求

```python
# test_bitget_market.py

@pytest.mark.asyncio
async def test_get_quote_parses_correctly():
    """验证 Bitget API 返回被正确解析为 Quote 对象"""
    # Arrange: mock 网络响应
    httpx_mock.get(
        "https://api.bitget.com/api/v3/market/tickers"
        "?category=USDT-FUTURES&symbol=AAPLUSDT"
    ).respond(
        json={
            "code": "00000",
            "data": [{
                "lastPrice": "342.5",
                "openPrice24h": "338.0",
                "highPrice24h": "345.0",
                "lowPrice24h": "335.0",
                "price24hPcnt": "0.015",
                "volume24h": "15000",
                "turnover24h": "5100000",
                "indexPrice": "342.3",
                "markPrice": "342.5",
                "fundingRate": "0.0001",
                "openInterest": "20000",
                "bid1Price": "342.4",
                "ask1Price": "342.6",
                "ts": "1700000000000",
            }]
        }
    )

    source = BitgetMarketSource()
    quote = await source.get_quote("AAPL")

    assert quote.symbol == "AAPL"
    assert quote.price == 342.5
    assert quote.change_pct == 0.015
    assert quote.funding_rate == 0.0001
```

**覆盖边界**:
- 响应为空 → `get_quote` 返回 `None`
- status=offline → `get_stock_symbols()` 过滤掉
- 网络超时 → 抛 `DataSourceTimeout`
- API 返回错误码 → 抛 `DataSourceError`

---

### 分析引擎 (Phase 2)

```
tests/analyzers/
├── test_technical.py
├── test_market_regime.py
├── test_sentiment.py
└── test_ai.py
```

**技术指标 — 纯函数，最简单**

```python
# test_technical.py

@pytest.fixture
def klines():
    """100 根稳定上涨的 K 线"""
    return [
        Kline(timestamp=..., open=100+i, high=101+i, low=99+i, close=100+i*1.01,
              volume=10000, turnover=...)
        for i in range(100)
    ]

def test_ma5_upward_trend(klines):
    ta = TechnicalAnalyzer()
    ind = ta.calculate(klines)
    assert ind.ma10 > ind.ma30        # 上涨趋势中快线 > 慢线
    assert ind.rsi14 > 50             # 上涨趋势中 RSI > 50
    assert ind.volume_ratio > 0       # 有成交量

def test_rsi_oversold():
    """持续下跌的 K 线 → RSI < 30"""
    klines = [Kline(close=100 - i, ...) for i in range(100)]
    ind = TechnicalAnalyzer().calculate(klines)
    assert ind.rsi14 < 30

def test_golden_cross(klines):
    ind = TechnicalAnalyzer().calculate(klines)
    assert ind.ma10 > ind.ma30 and ind.ma10_prev <= ind.ma30_prev
```

**市场状态 — 纯函数**
```python
def test_trend_up_detected():
    klines = [...]  # 稳定上涨
    ind = TechnicalAnalyzer().calculate(klines)
    regime = MarketRegimeDetector().detect(klines, ind)
    assert regime.regime == "trend_up"
    assert regime.adx > 25
```

---

### 策略层 (Phase 2)

```
tests/strategies/
├── test_trend_break.py
├── test_rsi_bounce.py
└── test_convergence.py
```

**策略测试 — 构造 AnalysisContext**

```python
# test_trend_break.py

def make_golden_cross_context():
    return AnalysisContext(
        symbol="AAPL",
        quote=Quote(price=150, mark_price=150, ...),
        klines=[Kline(...) for _ in range(100)],
        indicators=TechnicalIndicators(
            ma10=155, ma30=148,          # 金叉
            ma10_prev=149, ma30_prev=148,
            rsi14=55,
            volume_ratio=2.0,
            atr14=3.0,
        ),
        market_regime=MarketRegime(regime="trend_up"),
        news=[],
    )

@pytest.mark.asyncio
async def test_golden_cross_generates_buy():
    ctx = make_golden_cross_context()
    signal = await TrendBreakStrategy().evaluate(ctx)
    assert signal is not None
    assert signal.action in ("BUY", "STRONG_BUY")
    assert signal.entry_price > 0
    assert signal.stop_loss < signal.entry_price
    assert len(signal.take_profits) == 2
    assert all(tp > signal.entry_price for tp in signal.take_profits)

@pytest.mark.asyncio
async def test_no_signal_without_golden_cross():
    ctx = make_golden_cross_context()
    ctx.indicators.ma10 = 140    # 反转: 快线 < 慢线
    ctx.indicators.ma30 = 148
    signal = await TrendBreakStrategy().evaluate(ctx)
    assert signal is None

@pytest.mark.asyncio
async def test_cooldown_blocks_duplicate():
    ctx = make_golden_cross_context()
    strategy = TrendBreakStrategy()
    signal1 = await strategy.evaluate(ctx)
    signal2 = await strategy.evaluate(ctx)  # 冷却中
    assert signal1 is not None
    assert signal2 is None  # 冷却期阻挡了
```

---

### 信号层 (Phase 2)

```
tests/signals/
├── test_scorer.py
└── test_aggregator.py
```

```python
# test_scorer.py

def test_scorer_breaks_down_correctly():
    scorer = SignalScorer()
    result = scorer.score(
        effective_signals=[EffectiveSignal(signal=Signal(
            strategy_id="trend_break", action="BUY",
            confidence=0.8, entry_price=100,
            stop_loss=95, take_profits=[103, 105],
            reason="test",
        ))],
        market_regime=MarketRegime(regime="trend_up"),
        fundamentals=FundamentalData(symbol="AAPL"),
        news_sentiment={"positive": 0.3, "negative": 0.1, "neutral": 0.6},
        indicators=TechnicalIndicators(...),
    )
    assert isinstance(result, ScoreResult)
    assert 0 <= result.total_score <= 100
    assert result.action in ("STRONG_BUY", "BUY", "HOLD", "SELL", "STRONG_SELL")
    assert set(result.breakdown.keys()) == {"technical", "ai", "fundamental", "sentiment"}
```

---

### 交易执行层 (Phase 3)

```
tests/trading/
├── test_paper_executor.py
├── test_safety.py
├── test_slippage.py
└── test_risk_manager.py
```

```python
# test_paper_executor.py  (最复杂，需要模拟运行时)

@pytest.fixture
def executor():
    return PaperExecutor({
        "safety": {"max_leverage": 1},
        "slippage": {"base_spreads": {"regular": 0.0}},  # 滑点=0 便于测试
    })

@pytest.mark.asyncio
async def test_open_position_deducts_balance(executor):
    initial = executor.balance.current_balance
    signal = Signal(
        strategy_id="test", symbol="AAPL",
        action="BUY", confidence=0.8,
        entry_price=100, stop_loss=95, take_profits=[103],
        reason="test",
    )
    result = await executor.execute_signal(signal)
    assert result.status == "FILLED"
    assert executor.balance.current_balance < initial  # 扣钱了
    assert "AAPL" in executor.positions

@pytest.mark.asyncio
async def test_stop_loss_triggers_close(executor):
    signal = Signal(..., entry_price=100, stop_loss=95, ...)
    await executor.execute_signal(signal)
    # 模拟价格跌到止损位
    await executor.tick_positions({"AAPL": Quote(mark_price=94, ...)})
    assert "AAPL" not in executor.positions
    assert executor.balance.total_pnl < 0  # 亏损
```

---

### 优化层 (Phase 4)

```
tests/optimization/
├── test_performance.py
├── test_param_tuner.py
└── test_cold_start.py
```

```python
# test_performance.py

def test_win_rate_calculation():
    trades = [
        TradeRecord(..., pnl=50, type="CLOSE"),
        TradeRecord(..., pnl=-20, type="CLOSE"),
        TradeRecord(..., pnl=30, type="CLOSE"),
        TradeRecord(..., pnl=-10, type="CLOSE"),
    ]
    analyzer = PerformanceAnalyzer()
    report = analyzer.analyze(trades)
    assert report.win_rate == 0.5      # 2 wins / 4 total
    assert report.total_pnl == 50       # 50-20+30-10

def test_tuner_suggests_fast_ma_increase():
    """低胜率+高盈亏比 → 建议延长均线"""
    report = PerformanceReport(
        per_strategy={
            "trend_break": {
                "trades": 50, "win_rate": 0.35,
                "profit_factor": 2.0, "avg_loss": 20, "total_pnl": 100,
            }
        }
    )
    tuner = ParamTuner()
    suggestions = tuner._rule_based_tune(
        "trend_break", report.per_strategy["trend_break"],
        TrendBreakStrategy(),
    )
    assert any("fast_ma" in s.param_path for s in suggestions)
```

---

## 三、集成测试（仅 AI-6 做）

```
tests/
├── test_integration.py
```

```python
# test_integration.py — 端到端测试（不带网络）

@pytest.mark.asyncio
async def test_scan_to_signal():
    """从 K线 → 指标 → 策略 → 评分 → 信号"""
    klines = ...          # fake Klines
    indicators = TechnicalAnalyzer().calculate(klines)
    regime = MarketRegimeDetector().detect(klines, indicators)
    # ... 构造完整上下文
    ctx = AnalysisContext(..., indicators=indicators, market_regime=regime)
    strat = TrendBreakStrategy()
    signal = await strat.evaluate(ctx)
    assert signal is not None
```

---

## 四、测试配置

```toml
# pyproject.toml (测试相关片段)
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
markers = [
    "slow: 需要网络或较慢的测试",
    "integration: 端到端测试",
]

[tool.coverage.run]
source = ["src"]
omit = ["tests/*"]
```

---

## 五、运行方式

```bash
# 所有独立测试（不需要网络）
uv run pytest tests/ -x -v

# 只测某个模块
uv run pytest tests/analyzers/test_technical.py -v

# 跳过慢测试
uv run pytest tests/ -x -v -m "not slow"

# 带覆盖率
uv run pytest tests/ --cov=src --cov-report=term-missing

# 模块完全独立，没有跨模块 import:
uv run pytest tests/strategies/      # 成功，不依赖 datasources
uv run pytest tests/trading/         # 成功，不依赖 strategies
```
