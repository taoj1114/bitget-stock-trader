# Phase 3 设计文档 — 模拟交易 + 安全系统

> ⚠️ 本阶段只产出设计文档和伪代码，不写实现代码。
> 所有代码文件标记为 `TODO[Phase3]`，具体实现由编码阶段完成。

---

## 一、Phase 3 总览

### 新增模块

| 模块 | 文件 | 职责 |
|------|------|------|
| 模拟执行器 | `src/trading/paper_executor.py` | 模拟开仓/平仓/止盈止损/资金费率结算 |
| 实盘执行器 | `src/trading/real_executor.py` | Bitget 真实下单（接口预留） |
| 风控管理器 | `src/trading/risk_manager.py` | 仓位计算/杠杆管理/日回撤限制 |
| 安全护栏 | `src/trading/safety.py` | 三层安全规则（硬限制/条件/熔断） |
| 滑点模型 | `src/trading/slippage.py` | 按交易时段 + 品种流动性的滑点计算 |
| 交易追踪器 | `src/trading/tracker.py` | 交易日志 SQLite 读写 + 统计查询 |
| 执行器工厂 | `src/trading/executor_factory.py` | 根据 mode 自动切换 Paper/Real |

### 数据流

```
Signal (来自 Phase 2)
  │
  ▼
┌──────────────────┐
│  ExecutorFactory  │── mode: paper → PaperExecutor
│  (自动切换)       │── mode: real  → RealExecutor
└──────┬───────────┘
       │
       ▼
┌──────────────────────────────────────────────┐
│  PaperExecutor                               │
│                                              │
│  1. SafetyManager.check()  ← 安全护栏        │
│  2. SlippageModel.apply()  ← 滑点计算        │
│  3. RiskManager.calc_size() ← 仓位计算       │
│  4. 创建 Position                          │
│  5. 扣减余额                                 │
│  6. Tracker.record()      ← 记录交易日志    │
└──────┬───────────────────────────────────────┘
       │
       ▼ (定时 tick)
┌──────────────────┐
│  tick_positions()  │── 检查止损/止盈/移动止损
└──────────────────┘
       │
       ▼ (平仓)
┌──────────────────┐
│  close_position()  │── 计算盈亏(PnL) + 资金费率
└──────────────────┘
       │
       ▼
┌──────────────────┐
│  Tracker.record()  │── 写入 trade_log + 更新绩效
└──────────────────┘
```

---

## 二、模拟交易引擎

### 核心流程

```python
# src/trading/paper_executor.py

class PaperExecutor(Executor):
    """
    模拟交易执行器。

    核心逻辑:
        1. 接收 Signal → 创建 Order → Safety 检查
        2. 计算滑点 → 确定实际成交价
        3. 计算仓位大小 → 创建 Position
        4. 扣减 balance / 增加 used_margin
        5. tick 循环检查止损/止盈/移动止损
        6. 平仓时计算 PnL + 资金费率

    所有操作在内存 + SQLite 中完成，永不发送真实 API 请求。
    """

    def __init__(self, config: dict):
        self.balance = AccountBalance(
            initial_capital=10_000,
            current_balance=10_000,
        )
        self.positions: dict[str, Position] = {}
        self.slippage = SlippageModel(config.get("slippage", {}))
        self.safety = SafetyManager(config.get("safety", {}))
        self.risk = RiskManager(config.get("safety", {}))

    async def execute_signal(self, signal: Signal) -> OrderResult:
        """
        执行交易信号。

        流程:
            1. 确定方向 (LONG/SHORT)
            2. 计算仓位 (RiskManager.calc_position_size)
            3. 安全检查 (SafetyManager.check_order)
            4. 计算滑点 (SlippageModel.get_price)
            5. 创建 Position
            6. 更新余额
            7. 记录交易日志 → Tracker.record_open
        """
        # 确定方向
        side = "LONG" if signal.action in ("BUY", "STRONG_BUY") else "SHORT"

        # 计算仓位
        quantity = self.risk.calc_position_size(
            balance=self.balance,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            side=side,
        )
        if quantity <= 0:
            return OrderResult(status="REJECTED", reason="仓位计算结果为0")

        # 构建订单
        order = Order(
            symbol=signal.symbol,
            side=side,
            quantity=quantity,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profits=signal.take_profits,
        )

        # 安全检查
        verdict = await self.safety.check_order(order, self.balance, self.positions)
        if not verdict.passed:
            return OrderResult(status="REJECTED", reason=verdict.reason)

        # 检查是否已有同品种持仓 (防止覆盖)
        if signal.symbol in self.positions:
            existing = self.positions[signal.symbol]
            return OrderResult(
                status="REJECTED",
                reason=f"{signal.symbol} 已有持仓 ({existing.side} {existing.quantity}@${existing.entry_price:.2f})"
            )

        # 滑点计算
        spread = self.slippage.get_spread(signal.symbol)
        actual_price = signal.entry_price * (1 + spread) if side == "LONG" \
                       else signal.entry_price * (1 - spread)

        # 创建持仓
        position = Position(
            id=str(uuid.uuid4())[:8],
            symbol=signal.symbol,
            side=side,
            quantity=quantity,
            entry_price=actual_price,
            mark_price=actual_price,
            stop_loss=signal.stop_loss,
            take_profit_levels=[
                {"level": i+1, "price": p, "filled": False, "ratio": 1/len(signal.take_profits)}
                for i, p in enumerate(signal.take_profits)
            ],
            opened_at=datetime.now(),
            strategy_id=signal.strategy_id,
        )

        # 扣减余额
        cost = actual_price * quantity
        self.balance.current_balance -= cost
        self.balance.used_margin += cost
        self.positions[signal.symbol] = position

        # 记录交易
        Tracker.record_open(position, signal, spread)

        return OrderResult(
            status="FILLED",
            position_id=position.id,
            fill_price=actual_price,
            fill_quantity=quantity,
        )

    async def tick_positions(self, quotes: dict[str, Quote]):
        """
        定时检查持仓状态 (由定时器调用, 例如每分钟)。

        检查项目:
            1. 止损触发? → 平仓 (STOP_LOSS)
            2. 止盈触发? → 分批平仓 (TAKE_PROFIT_1/2/3)
            3. 移动止损? → 价格有利方向移动时上调/下调
            4. 更新 unrealized_pnl
        """
        for symbol, pos in list(self.positions.items()):
            quote = quotes.get(symbol)
            if not quote:
                continue

            price = quote.mark_price
            pos.mark_price = price
            pos.unrealized_pnl = self._calc_unrealized_pnl(pos, price)

            # 止损
            if (pos.side == "LONG" and price <= pos.stop_loss) or \
               (pos.side == "SHORT" and price >= pos.stop_loss):
                await self.close_position(pos.id, reason="STOP_LOSS")
                continue

            # 止盈
            for tp in pos.take_profit_levels:
                if tp["filled"]:
                    continue
                hit = (pos.side == "LONG" and price >= tp["price"]) or \
                      (pos.side == "SHORT" and price <= tp["price"])
                if hit:
                    tp["filled"] = True
                    close_qty = pos.quantity * tp["ratio"]
                    await self._partial_close(pos, close_qty,
                                              reason=f"TAKE_PROFIT_{tp['level']}")

            # 移动止损
            self._update_trailing_stop(pos, price)

    async def close_position(self, position_id: str, reason: str) -> OrderResult:
        """
        平仓。

        流程:
            1. 从 positions 中移除
            2. 获取当前价格 + 滑点
            3. 计算 PnL
    """

    async def close_position(self, position_id: str, reason: str) -> OrderResult:
        """
        平仓（全平）。
        """
        pos = self.positions.pop(position_id, None)
        if not pos:
            return OrderResult(status="NOT_FOUND", reason="持仓不存在")

        exit_price = self._get_exit_price(pos)
        pnl = self._calc_pnl(pos, exit_price)
        funding = self._calc_funding_cost(pos)

        # 结算
        self.balance.current_balance += exit_price * pos.quantity
        self.balance.used_margin -= pos.entry_price * pos.quantity
        self.balance.total_pnl += pnl
        self.balance.total_funding_cost += funding

        # 更新胜率统计
        self.balance.total_trades += 1
        if pnl > 0:
            self.balance.win_count += 1
        else:
            self.balance.loss_count += 1

        # 当日回撤跟踪
        self.safety.record_daily_pnl(pnl)

        Tracker.record_close(pos, exit_price, pnl, funding, reason)

        return OrderResult(
            status="CLOSED",
            fill_price=exit_price,
            fill_quantity=pos.quantity,
            reason=reason,
        )

    async def _partial_close(self, pos: Position, close_qty: float, reason: str) -> OrderResult:
        """
        分批平仓（部分平仓）。
        从持仓中扣除 close_qty，已平部分结算盈亏。
        """
        if close_qty <= 0 or close_qty > pos.quantity:
            return OrderResult(status="REJECTED", reason=f"无效分批数量: {close_qty}")

        exit_price = self._get_exit_price(pos)

        if pos.side == "LONG":
            pnl = (exit_price - pos.entry_price) * close_qty
        else:
            pnl = (pos.entry_price - exit_price) * close_qty

        pos.quantity -= close_qty
        self.balance.current_balance += exit_price * close_qty
        self.balance.used_margin -= pos.entry_price * close_qty
        self.balance.total_pnl += pnl

        Tracker.record_close(pos, exit_price, pnl, 0, reason)

        return OrderResult(status="PARTIAL_CLOSED", fill_price=exit_price,
                           fill_quantity=close_qty, reason=reason)

    def _get_exit_price(self, pos: Position) -> float:
        """获取平仓价（含滑点）"""
        quote = get_latest_quote(pos.symbol)
        spread = self.slippage.get_spread(pos.symbol)
        if pos.side == "LONG":
            return quote.mark_price * (1 - spread)
        else:
            return quote.mark_price * (1 + spread)

    def _calc_position_size(self, signal: Signal) -> float:
        """计算仓位大小 (委托给 RiskManager)"""
        return self.risk.calc_position_size(
            balance=self.balance,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            side="LONG" if signal.action in ("BUY", "STRONG_BUY") else "SHORT",
        )

    def _calc_pnl(self, pos: Position, exit_price: float) -> float:
        """计算盈亏"""
        if pos.side == "LONG":
            return (exit_price - pos.entry_price) * pos.quantity
        else:
            return (pos.entry_price - exit_price) * pos.quantity

    def _calc_funding_cost(self, pos: Position) -> float:
        """
        计算持仓期间的资金费率成本。

        资金费率每 8h 结算一次 (Bitget 美股合约)。
        cost = mark_price × quantity × funding_rate × intervals

        ⚠️ 简化版本使用当前 funding_rate 近似计算。
        精确实现需要在每个结算周期记录当时的 funding_rate 快照。
        如果当前费率在持仓期间大幅变化，可以引入历史费率追踪:

            rate_snapshots = [
                {"time": opened_at, "rate": pos.opening_rate},
                {"time": ...,       "rate": ...},
            ]
            total_cost = sum(mark_price × quantity × rate for each interval)

        当前简化为:
            cost = mark_price × quantity × |current_funding_rate| × intervals
        """
        hours = (datetime.now() - pos.opened_at).total_seconds() / 3600
        intervals = hours / 8  # 8h 结算周期
        return pos.mark_price * pos.quantity * abs(pos.funding_rate) * intervals

    def _update_trailing_stop(self, pos: Position, price: float):
        """
        移动止损。
        当价格向有利方向移动超过 2% 时，将止损移动到盈亏平衡点。
        """
        if pos.side == "LONG" and price > pos.entry_price * 1.02:
            new_sl = price * 0.98
            if new_sl > pos.stop_loss:
                pos.stop_loss = new_sl
        elif pos.side == "SHORT" and price < pos.entry_price * 0.98:
            new_sl = price * 1.02
            if new_sl < pos.stop_loss:
                pos.stop_loss = new_sl
```

---

## 三、滑点模型

```python
# src/trading/slippage.py

class SlippageModel:
    """
    模拟真实交易滑点。

    滑点基于:
        - 当前交易时段 (regular/extended/asia)
        - 品种流动性 (大盘股 vs 小盘股)
        - 市场波动率 (高波动时滑点更大)

    时段定义 (北京时间):
        regular:   21:30 - 04:00 (美股交易时段)
        extended:  04:00 - 06:00 / 16:00 - 21:30
        asia:      06:00 - 16:00
    """

    # 基准滑点 (百分比)
    BASE_SPREADS = {
        "regular":  0.0002,    # 0.02%   (高流动性)
        "extended": 0.0010,    # 0.10%   (中流动性)
        "asia":     0.0025,    # 0.25%   (低流动性)
    }

    # 品种流动性因子 (相对于大盘股)
    LIQUIDITY_FACTOR = {
        "megacap": 1.0,   # AAPL, NVDA, MSFT, GOOGL, AMZN
        "large":   1.5,   # META, TSLA, NFLX, ADBE
        "mid":     2.0,   # PLTR, COIN, MSTR, SMCI
        "small":   3.0,   # 小市值/低成交品种
        "etf":     0.8,   # QQQ, SPY, IWM (ETF 流动性最好)
    }

    MEGACAP = {"AAPL", "NVDA", "MSFT", "GOOGL", "AMZN", "META", "TSLA"}

    def __init__(self, config: dict = None):
        self.base_spreads = (config or {}).get("base_spreads", self.BASE_SPREADS)

    def get_spread(self, symbol: str, volatility: str = "normal") -> float:
        """
        获取当前滑点。

        公式:
            spread = base_spread[时段] × liquidity_factor[symbol] × vol_factor

        返回示例:
            regular + AAPL + normal → 0.0002 (0.02%)
            asia + SMCI + high     → 0.0025 × 2.0 × 1.5 = 0.0075 (0.75%)
        """
        window = self._current_window()
        base = self.base_spreads.get(window, 0.0010)

        liquidity = self._liquidity_factor(symbol)

        vol_factor = {"high": 1.5, "normal": 1.0, "low": 0.8}
        vol = vol_factor.get(volatility, 1.0)

        return base * liquidity * vol

    def _current_window(self) -> str:
        """判断当前交易时段"""
        from datetime import datetime
        h = datetime.now().hour  # UTC → 北京时间 +8

        # 北京时间 21:30 - 04:00 = 美股交易时段
        if h >= 21 or h < 4:
            return "regular"
        # 北京时间 04:00 - 06:00 或 16:00 - 21:30 = 盘前盘后
        if (4 <= h < 6) or (16 <= h < 21):
            return "extended"
        # 北京时间 06:00 - 16:00 = 亚洲时段
        return "asia"

    def _liquidity_factor(self, symbol: str) -> float:
        """获取品种流动性因子"""
        base = symbol.replace("USDT", "").upper()
        if base in self.MEGACAP:
            return 1.0
        # 默认中型股
        return 1.5
```

---

## 四、风控管理器

```python
# src/trading/risk_manager.py

class RiskManager:
    """
    风控管理器 — 仓位计算 + 杠杆管理。

    仓位计算公式 (凯利公式改良版):
        risk_amount = balance × risk_per_trade_pct
        position_size = risk_amount / (entry - stop_loss)  (做多)
        position_size = risk_amount / (stop_loss - entry)  (做空)

    杠杆限制:
        max_leverage = 1 (初始) → 随胜率逐步解禁
        实际杠杆 = min(计算杠杆, max_leverage)
    """

    def __init__(self, config: dict):
        self.max_single_risk = config.get("max_single_risk_pct", 2.0) / 100
        self.max_total_exposure = config.get("max_total_exposure_pct", 50.0) / 100
        self.max_leverage = config.get("max_leverage", 1)
        self.max_positions = config.get("max_positions", 5)
        self.max_order_value = config.get("max_order_value", 5000)

    def calc_position_size(self, balance: AccountBalance, entry_price: float,
                           stop_loss: float, side: str) -> float:
        """
        计算仓位大小 (基于风险百分比)。

        如果 stop_loss == entry_price，返回 0 (无法计算风险)。
        """
        if entry_price == 0 or stop_loss == entry_price:
            return 0

        # 单笔风险金额
        risk_amount = balance.current_balance * self.max_single_risk

        # 单位风险 (每股/每合约亏损)
        if side == "LONG":
            unit_risk = entry_price - stop_loss
        else:
            unit_risk = stop_loss - entry_price

        if unit_risk <= 0:
            return 0

        quantity = risk_amount / unit_risk

        # 检查最大订单金额
        max_qty = self.max_order_value / entry_price
        quantity = min(quantity, max_qty)

        return max(quantity, 0)
```

---

## 五、多层安全护栏

```python
# src/trading/safety.py

class SafetyManager:
    """
    三层安全护栏。Paper 和 Real 模式均运行。

    Layer 1: 硬限制 (不可绕过)
        - 单笔最大金额
        - 最大持仓数
        - 最大杠杆
        - 总敞口比例

    Layer 2: 条件限制 (可配置)
        - 连续亏损次数 → 暂停
        - 日最大回撤 → 当天暂停
        - 资金费率过高 → 禁止开对应方向

    Layer 3: 熔断 (自动触发)
        - 数据源连续失败 → 只读
        - 价格突变 → 暂停交易
        - API 错误率过高 → 降级
    """

    def __init__(self, config: dict):
        cfg = config or {}

        # Layer 1
        self.max_single_value = cfg.get("max_order_value", 5000)
        self.max_positions = cfg.get("max_positions", 5)
        self.max_leverage = cfg.get("max_leverage", 1)
        self.max_exposure_pct = cfg.get("max_total_exposure_pct", 50.0)

        # Layer 2
        self.max_consecutive_losses = cfg.get("max_consecutive_losses", 5)
        self.max_daily_drawdown_pct = cfg.get("max_daily_drawdown_pct", 5.0)
        self.max_funding_rate = cfg.get("max_funding_rate", 0.001)

        # Layer 2 状态
        self._consecutive_losses = 0
        self._daily_drawdown = 0.0
        self._cooldown_until = None
        self._daily_frozen = False
        self._daily_pnls: list[float] = []

        # Layer 3
        self._datasource_failures = 0
        self._circuit_breaker_active = False
        self._circuit_breaker_reason = ""

    async def check_order(self, order: Order, balance: AccountBalance,
                          positions: dict) -> SafetyVerdict:
        """
        订单级安全检查。

        检查:
            Layer 1: 单笔金额 / 持仓数 / 杠杆 / 敞口
            Layer 2: 连续亏损 / 日回撤 / 资金费率
            Layer 3: 熔断状态
        """
        # === Layer 1 ===
        order_value = order.entry_price * order.quantity
        if order_value > self.max_single_value:
            return SafetyVerdict(False, f"超单笔上限 {self.max_single_value}", "hard")

        if len(positions) >= self.max_positions:
            return SafetyVerdict(False, f"已达最大持仓数 {self.max_positions}", "hard")

        total_exposure = sum(p.entry_price * p.quantity for p in positions.values())
        exposure_pct = (total_exposure + order_value) / balance.current_balance * 100
        if exposure_pct > self.max_exposure_pct:
            return SafetyVerdict(False, f"总敞口 {exposure_pct:.1f}% 超限 {self.max_exposure_pct}%", "hard")

        # === Layer 2 ===
        if self._daily_frozen:
            return SafetyVerdict(False, f"当日回撤达 {self.max_daily_drawdown_pct}%, 当天已暂停", "conditional")

        if self._cooldown_until and datetime.now() < self._cooldown_until:
            remaining = (self._cooldown_until - datetime.now()).total_seconds() / 3600
            return SafetyVerdict(False, f"连续亏损暂停中, 剩余 {remaining:.1f}h", "conditional")

        # === Layer 3 ===
        if self._circuit_breaker_active:
            return SafetyVerdict(False, f"熔断: {self._circuit_breaker_reason}", "circuit_breaker")

        return SafetyVerdict(True, "", "")

    def record_daily_pnl(self, pnl: float):
        """记录当日盈亏，触发日回撤检测"""
        self._daily_pnls.append(pnl)
        daily_pnl = sum(self._daily_pnls)
        if daily_pnl < 0 and abs(daily_pnl) > self.max_daily_drawdown_pct:
            self._daily_frozen = True

    def record_trade_result(self, pnl: float):
        """记录交易结果，触发连续亏损检测"""
        if pnl < 0:
            self._consecutive_losses += 1
            if self._consecutive_losses >= self.max_consecutive_losses:
                cooldown_hours = 24
                self._cooldown_until = datetime.now() + timedelta(hours=cooldown_hours)
        else:
            self._consecutive_losses = 0

    def record_datasource_failure(self):
        """记录数据源失败，触发熔断"""
        self._datasource_failures += 1
        if self._datasource_failures >= 5:
            self._circuit_breaker_active = True
            self._circuit_breaker_reason = f"数据源连续失败 {self._datasource_failures} 次"

    def reset_daily(self):
        """每日重置"""
        self._daily_frozen = False
        self._daily_pnls = []
        self._datasource_failures = 0

    async def health_check(self, quote, klines) -> tuple[bool, str]:
        """数据健康检查。返回 (通过?, 原因)"""
        # 检查价格是否异常
        if len(klines) >= 2:
            prev_close = klines[-2].close
            change_pct = abs(quote.price / prev_close - 1) * 100
            if change_pct > 5.0:
                self._circuit_breaker_active = True
                self._circuit_breaker_reason = f"价格突变 {change_pct:.1f}%"
                return (False, self._circuit_breaker_reason)

        # 检查数据是否过期（最后一根K线是否超过30分钟前）
        if klines:
            from datetime import datetime, timezone
            now_ts = datetime.now(timezone.utc).timestamp() * 1000
            last_kline_age = (now_ts - klines[-1].timestamp) / 1000 / 60  # 分钟
            if last_kline_age > 30:
                return (False, f"K线数据过期 ({last_kline_age:.0f}分钟前更新)")

        return (True, "")
```

---

## 六、交易追踪器

```python
# src/trading/tracker.py

class Tracker:
    """
    交易日志追踪器 — SQLite 存储。所有方法为静态，全局共享。

    首次使用前需调用 init_db() 建表（在 main.py 启动时执行一次）。

    表结构:
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            symbol TEXT,
            side TEXT,
            type TEXT,          -- OPEN / CLOSE / SL / TP
            price REAL,
            quantity REAL,
            pnl REAL,
            pnl_pct REAL,
            reason TEXT,
            strategy_id TEXT,
            spread REAL,
            funding_cost REAL,
            holding_hours REAL,
            market_regime TEXT,
            signal_score REAL,
            entry_price REAL,   -- 关联到开仓记录
            position_id TEXT    -- 关联到持仓
        );

        CREATE TABLE daily_summary (
            date TEXT PRIMARY KEY,
            total_trades INTEGER,
            win_count INTEGER,
            total_pnl REAL,
            max_drawdown REAL,
            strategies_used TEXT
        );
    """

    @staticmethod
    def init_db():
        """初始化数据库（建表）。启动时调用一次。"""
        ...

    @staticmethod
    def record_open(position: Position, signal: Signal, spread: float):
        """记录开仓"""
        ...

    @staticmethod  
    def record_close(position: Position, exit_price: float, pnl: float,
                     funding_cost: float, reason: str):
        """记录平仓"""
        ...

    @staticmethod
    def get_strategy_trades(strategy_id: str, limit: int = 100) -> list[TradeRecord]:
        """获取策略的历史交易"""
        ...

    @staticmethod
    def get_daily_pnl(date: str = None) -> float:
        """获取当日/指定日期盈亏"""
        ...

    @staticmethod
    def get_open_trades() -> list[TradeRecord]:
        """获取当前持仓对应的开仓记录"""
        ...
```

---

## 七、执行器工厂

```python
# src/trading/executor_factory.py

def create_executor(mode: str, config: dict) -> Executor:
    """
    执行器工厂 — 根据 mode 返回对应实例。

    mode:
        "paper" → PaperExecutor (模拟交易，不碰API)
        "real"  → RealExecutor (Bitget真实下单，需API Key)
    
    扩展方式: 新增 mode 时在此添加 elif 分支。
    """
    if mode == "paper":
        return PaperExecutor(config)
    elif mode == "real":
        raise NotImplementedError("TODO[Phase3]: RealExecutor 实现，需要 Bitget API Key")
    else:
        raise ValueError(f"未知执行器模式: {mode}")
```

### RealExecutor 接口预留

```python
# src/trading/real_executor.py

class RealExecutor(Executor):
    """
    Bitget 真实交易执行器。

    需要:
        - bitget.api_key
        - bitget.api_secret
        - bitget.api_passphrase

    接口与 PaperExecutor 一致，但调用 Bitget V3 UTA API:
        POST /api/v3/trade/order          (开仓)
        POST /api/v3/trade/close-position (平仓)
        GET  /api/v3/account/accounts     (账户信息)
        GET  /api/v3/misc/position        (持仓查询)

    迁移方式:
        1. 实现此类的所有接口方法
        2. 在 executor_factory.py 中注册
        3. 修改 config.yaml mode: "real"
        4. ⚠️ 先在测试网验证
    """

    async def execute_signal(self, signal: Signal) -> OrderResult:
        raise NotImplementedError("TODO[Phase3]: 实现 RealExecutor")
```

---

## 八、完整扫描周期 (含 Phase 3)

```
scan_all():
    1. 获取数据 (Phase 1)
    2. 技术分析 (Phase 2)
    3. 策略评估 (Phase 2)
    4. 信号聚合评分 (Phase 2)
    5. ─────────────────────────────
       ExecutorFactory(mode).execute_signal(signal)
         5a. RiskManager.calc_position_size()
         5b. SafetyManager.check_order()   ← Layer 1/2/3
         5c. SlippageModel.get_spread()
         5d. PaperExecutor._create_position()
         5e. Tracker.record_open()
       ─────────────────────────────
    6. 定时 tick:
         6a. PaperExecutor.tick_positions()
         6b. 止损/止盈/移动止损
         6c. SafetyManager.record_daily_pnl()
    7. 平仓:
         7a. PaperExecutor.close_position()
         7b. 计算 PnL + 资金费率
         7c. SafetyManager.record_trade_result()
         7d. Tracker.record_close()
```

---

## 九、文件清单

| 文件 | TODO | 参考伪代码 |
|------|------|-----------|
| `src/trading/paper_executor.py` | `TODO[Phase3]` | PSEUDOCODE.md §8 |
| `src/trading/real_executor.py` | `TODO[Phase3]` | 本设计 §七 |
| `src/trading/risk_manager.py` | `TODO[Phase3]` | 本设计 §四 |
| `src/trading/safety.py` | `TODO[Phase3]` | PSEUDOCODE.md §9 |
| `src/trading/slippage.py` | `TODO[Phase3]` | 本设计 §三 |
| `src/trading/tracker.py` | `TODO[Phase3]` | 本设计 §六 |
| `src/trading/executor_factory.py` | `TODO[Phase3]` | 本设计 §七 |

---

## 十、验收标准

```
Phase 3 完成时:

1. python main.py --scan
   └── 信号 → 模拟开仓 → 显示持仓和余额

2. python main.py --positions
   └── 显示当前模拟持仓 (symbol/方向/数量/入场价/浮盈)

3. python main.py --balance
   └── 显示账户余额 (初始/当前/已用保证金/总盈亏)

4. python main.py --history
   └── 显示交易历史 (按时间倒序)

5. 安全护栏日志:
   └── SafetyManager 的拦截事件可查询

6. 切换模式:
   └── config.yaml mode: "real" → 提示 "需要 API Key"
   └── config.yaml mode: "paper" → 正常模拟交易
```

---

> **下一步**: 确认设计无误后，回复 **编码开始** 进入 Phase 3 实现阶段。
