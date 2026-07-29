# Phase 4 设计文档 — 参数进化 + Web API

> ⚠️ 本阶段只产出设计文档和伪代码，不写实现代码。
> 所有代码文件标记为 `TODO[Phase4]`，具体实现由编码阶段完成。

---

## 一、Phase 4 总览

### 新增模块

| 模块 | 文件 | 职责 |
|------|------|------|
| 绩效分析器 | `src/optimization/performance.py` | 交易数据统计 → 胜率/夏普/回撤 |
| 参数调优器 | `src/optimization/param_tuner.py` | 规则优化 + AI 推荐参数 |
| 冷启动管理 | `src/optimization/cold_start.py` | 新策略评估期管理 |
| 参数版本管理 | `src/optimization/version.py` | 参数版本追踪 + 自动回滚 |
| Web API | `src/api/server.py` | FastAPI 应用 + CORS |
| 行情路由 | `src/api/routes/market.py` | quote/klines/symbols |
| 分析路由 | `src/api/routes/analysis.py` | analyze/scan |
| 信号路由 | `src/api/routes/signals.py` | signals 查询 |
| 交易路由 | `src/api/routes/trading.py` | positions/balance/history |
| 策略路由 | `src/api/routes/strategies.py` | 策略管理/参数热更新 |
| 优化路由 | `src/api/routes/optimization.py` | 优化建议/应用 |
| 安全路由 | `src/api/routes/safety.py` | 安全事件/风控状态 |
| WebSocket | `src/api/websocket.py` | 实时行情/信号推送 |
| 依赖注入 | `src/api/dependencies.py` | FastAPI 依赖注入 |

### 架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                          Web 层 (FastAPI)                           │
│                                                                     │
│  REST API:  /api/v1/stocks/...  /api/v1/trading/...                │
│  WebSocket: ws://host/ws/ticks   ws://host/ws/signals               │
│                                                                     │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ │
│  │ 行情路由  │ │ 分析路由  │ │ 交易路由  │ │ 策略路由  │ │ 优化路由  │ │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ │
└───────┼────────────┼────────────┼────────────┼────────────┼───────┘
        │            │            │            │            │
        ▼            ▼            ▼            ▼            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       业务层 (已有)                                  │
│  DataSources → Analyzers → Strategies → Scorer → Executor          │
└─────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         优化层                                       │
│  Tracker → PerformanceAnalyzer → ParamTuner → 热更新                │
│                                  ↓                                  │
│                          AutoRollback (回滚)                        │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 二、绩效分析器

```python
# src/optimization/performance.py

class PerformanceAnalyzer:
    """
    交易绩效分析器。

    输入: list[TradeRecord] (从 Tracker 获取)
    输出: PerformanceReport (按策略/市场状态/时间维度统计)

    指标:
        - 胜率 (Win Rate)
        - 盈亏比 (Profit Factor)
        - 期望值 (Expectancy)
        - 夏普比率 (Sharpe Ratio)
        - 最大回撤 (Max Drawdown)
        - 卡玛比率 (Calmar Ratio)
        - 平均持仓时间
        - 各市场状态下的表现
    """

    MIN_TRADES_FOR_ANALYSIS = 10  # 最少交易数才做分析

    async def analyze(self, trades: list[TradeRecord]) -> PerformanceReport:
        """
        分析交易绩效。

        统计维度:
            1. 全量交易汇总
            2. 按策略分组
            3. 按市场状态分组
            4. 按时间 (最近7天/30天)
        """
        if len(trades) < self.MIN_TRADES_FOR_ANALYSIS:
            return PerformanceReport(
                strategy_id="all",
                trades=len(trades),
                message=f"数据不足 (最少 {self.MIN_TRADES_FOR_ANALYSIS} 笔)"
            )

        # 只分析已平仓交易
        closed = [t for t in trades if t.type == "CLOSE" and t.pnl is not None]

        report = PerformanceReport(
            strategy_id="all",
            trades=len(closed),
            win_rate=self._win_rate(closed),
            avg_win=self._avg_win(closed),
            avg_loss=self._avg_loss(closed),
            profit_factor=self._profit_factor(closed),
            total_pnl=sum(t.pnl for t in closed),
            sharpe=self._sharpe_ratio([t.pnl for t in closed]),
            max_drawdown=self._max_drawdown([t.pnl for t in closed]),
            avg_holding_hours=self._avg_holding(closed),
        )

        # 按策略细分
        for sid in set(t.strategy_id for t in closed):
            group = [t for t in closed if t.strategy_id == sid]
            report.per_strategy[sid] = self._stats(group)

        # 按市场状态细分
        for regime in set(t.market_regime for t in closed if t.market_regime):
            group = [t for t in closed if t.market_regime == regime]
            report.by_regime[regime] = self._stats(group)

        return report

    def _stats(self, trades: list[TradeRecord]) -> dict:
        """计算一组交易的统计指标"""
        wins = [t for t in trades if t.pnl and t.pnl > 0]
        losses = [t for t in trades if t.pnl and t.pnl < 0]
        return {
            "trades": len(trades),
            "win_rate": len(wins) / len(trades) if trades else 0,
            "avg_win": sum(t.pnl for t in wins) / len(wins) if wins else 0,
            "avg_loss": sum(t.pnl for t in losses) / len(losses) if losses else 0,
            "profit_factor": (
                sum(t.pnl for t in wins) / abs(sum(t.pnl for t in losses))
                if losses else float('inf') if wins else 0
            ),
            "total_pnl": sum(t.pnl or 0 for t in trades),
            "avg_holding": sum(t.holding_hours for t in trades if t.holding_hours) / len(trades) if trades else 0,
        }

    def _win_rate(self, trades: list[TradeRecord]) -> float:
        wins = sum(1 for t in trades if t.pnl and t.pnl > 0)
        return wins / len(trades) if trades else 0

    def _profit_factor(self, trades: list[TradeRecord]) -> float:
        wins = sum(t.pnl for t in trades if t.pnl and t.pnl > 0)
        losses = abs(sum(t.pnl for t in trades if t.pnl and t.pnl < 0))
        return wins / losses if losses else float('inf') if wins else 0

    def _sharpe_ratio(self, pnls: list[float], rf: float = 0.02) -> float:
        """夏普比率 = (年化收益 - 无风险利率) / 年化波动率"""
        if len(pnls) < 2 or sum(pnls) == 0:
            return 0.0
        import numpy as np
        returns = np.array(pnls)
        excess = returns.mean() - rf / 252  # 日化无风险利率
        return excess / returns.std() * np.sqrt(252) if returns.std() > 0 else 0

    def _max_drawdown(self, pnls: list[float]) -> float:
        """最大回撤 (百分比)"""
        if not pnls:
            return 0.0
        cumulative = 0
        peak = 0
        drawdown = 0
        for p in pnls:
            cumulative += p
            if cumulative > peak:
                peak = cumulative
            dd = (peak - cumulative) / peak * 100 if peak > 0 else 0
            drawdown = max(drawdown, dd)
        return drawdown
```

---

## 三、参数调优器

```python
# src/optimization/param_tuner.py

class ParamTuner:
    """
    参数调优器 — 规则优化 + AI 推荐。

    触发条件:
        - 定时触发 (每周/每日)
        - 策略交易数达到阈值 (每 50 笔)
        - 胜率持续低于 40% (自动触发)

    调优方式 A: 规则优化
        基于预定义规则调整参数:
            - 胜率低 + 盈亏比高 → 延长均线周期
            - 亏损源于止损 → 扩大 ATR 倍数
            - 连续亏损 → 降低策略权重

    调优方式 B: AI 推荐
        将绩效数据发送给 DeepSeek v4-pro
        AI 分析数据模式并建议参数调整

    调优方式 C: 手动
        通过 API 直接设置参数
    """

    MIN_TRADES_FOR_TUNE = 30  # 最少交易数才调优

    async def tune(self, report: PerformanceReport,
                   strategies: dict[str, SignalStrategy]) -> list[ParamSuggestion]:
        """
        分析绩效并生成参数调优建议。

        Args:
            report: PerformanceAnalyzer 的输出
            strategies: 当前注册的策略实例

        Returns:
            list[ParamSuggestion]: 每个包含 strategy_id, param_path,
                                   current_value, suggested_value, confidence, reasoning
        """
        suggestions = []

        for strategy_id, perf in report.per_strategy.items():
            if perf["trades"] < self.MIN_TRADES_FOR_TUNE:
                continue

            strategy = strategies.get(strategy_id)
            if not strategy:
                continue

            # 方式 A: 规则优化
            rule_suggestions = self._rule_based_tune(strategy_id, perf, strategy)
            suggestions.extend(rule_suggestions)

            # 方式 B: AI 推荐 (交易数达标 + 活跃策略)
            if perf["trades"] >= 30:
                ai_suggestions = await self._ai_tune(strategy_id, perf, strategy)
                suggestions.extend(ai_suggestions)

        return suggestions

    def _rule_based_tune(self, strategy_id: str, perf: dict,
                         strategy: SignalStrategy) -> list[ParamSuggestion]:
        """
        基于规则的参数调优。

        规则表:
            trend_break:
                win_rate < 40% + profit_factor > 1.5:
                    → fast_ma +5 (延长快线过滤噪音)
                avg_loss > 0 + total_pnl < 0:
                    → atr_sl_multiplier +0.5 (扩大止损)

            rsi_bounce:
                win_rate < 35%:
                    → oversold 从 30 降到 25 (更严的超卖条件)

            ai_composite:
                win_rate < 40%:
                    → min_confidence +0.1 (要求更高置信度)
        """
        suggestions = []

        if strategy_id == "trend_break":
            if perf["win_rate"] < 0.4 and perf.get("profit_factor", 0) > 1.5:
                new_val = strategy.params.fast_ma + 5
                suggestions.append(ParamSuggestion(
                    strategy_id=strategy_id,
                    param_path="fast_ma",
                    current_value=strategy.params.fast_ma,
                    suggested_value=min(new_val, 50),  # 上限 50
                    confidence=0.6,
                    reasoning=f"胜率{perf['win_rate']:.0%}低但盈亏比{perf['profit_factor']:.1f}高，"
                              f"延长快线从{strategy.params.fast_ma}到{min(new_val, 50)}减少假信号",
                    based_on_trades=perf["trades"],
                ))
            if perf.get("avg_loss", 0) > 0 and perf.get("total_pnl", 0) < 0:
                new_val = strategy.params.atr_sl_multiplier + 0.5
                suggestions.append(ParamSuggestion(
                    strategy_id=strategy_id,
                    param_path="atr_sl_multiplier",
                    current_value=strategy.params.atr_sl_multiplier,
                    suggested_value=min(new_val, 5.0),
                    confidence=0.5,
                    reasoning="亏损多因止损过紧，扩大ATR止损倍数",
                    based_on_trades=perf["trades"],
                ))

        elif strategy_id == "rsi_bounce":
            if perf["win_rate"] < 0.35:
                new_val = strategy.params.oversold - 5
                suggestions.append(ParamSuggestion(
                    strategy_id=strategy_id,
                    param_path="oversold",
                    current_value=strategy.params.oversold,
                    suggested_value=max(new_val, 15),  # 下限 15
                    confidence=0.5,
                    reasoning="胜率过低，收紧超卖阈值减少假信号",
                    based_on_trades=perf["trades"],
                ))

        elif strategy_id == "ai_composite":
            if perf["win_rate"] < 0.4:
                new_val = strategy.params.min_confidence + 0.1
                suggestions.append(ParamSuggestion(
                    strategy_id=strategy_id,
                    param_path="min_confidence",
                    current_value=strategy.params.min_confidence,
                    suggested_value=min(new_val, 0.9),
                    confidence=0.4,
                    reasoning="AI策略胜率不足，提高置信度门槛",
                    based_on_trades=perf["trades"],
                ))

        return suggestions

    async def _ai_tune(self, strategy_id: str, perf: dict,
                       strategy: SignalStrategy) -> list[ParamSuggestion]:
        """
        方式B: 把绩效数据和当前参数发给 DeepSeek，让它提调优建议。
        """
        prompt = f"""你是一个量化交易策略优化器。分析以下策略的交易绩效，并建议参数调整方向。

## 策略: {strategy.name} ({strategy_id})
## 当前参数: {strategy.params.__dict__}
## 交易数据:
- 总交易数: {perf['trades']}
- 胜率: {perf['win_rate']:.1%}
- 平均盈利: {perf['avg_win']:.2f} USDT
- 平均亏损: {perf['avg_loss']:.2f} USDT
- 盈亏比: {perf['profit_factor']:.2f}
- 夏普比率: {perf['sharpe']:.2f}
- 最大回撤: {perf['max_drawdown']:.1f}%
- 平均持仓: {perf['avg_holding']:.1f}h

## 要求:
以 JSON 格式输出参数调整建议:
[
  {{
    "param_path": "参数路径名",
    "current_value": 当前值,
    "suggested_value": 建议值,
    "confidence": 0.0-1.0,
    "reasoning": "推理过程"
  }}
]
"""
        result = await self._call_deepseek(prompt)
        return self._parse_ai_response(result)

    async def _call_deepseek(self, prompt: str) -> str:
        """调用 OpenCode Go API (DeepSeek v4-pro)"""
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://opencode.ai/zen/go/v1/chat/completions",
                json={
                    "model": "deepseek-v4-pro",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 1000,
                },
                timeout=30,
            )
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    def _parse_ai_response(self, raw: str) -> list[ParamSuggestion]:
        """解析 AI 返回的 JSON"""
        import json
        try:
            items = json.loads(raw)
            if isinstance(items, list):
                return [ParamSuggestion(**item) for item in items]
        except (json.JSONDecodeError, TypeError):
            pass
        return []
```

---

## 四、参数版本管理 + 自动回滚

```python
# src/optimization/version.py

class ParamVersionManager:
    """
    参数版本管理 + 自动回滚。

    每次参数变更生成一个新的版本号。
    如果新参数绩效不如旧参数，自动回滚。

    记录格式:
        version: int (自增)
        strategy_id: str
        params_snapshot: dict (完整参数快照)
        applied_at: datetime
        performance_since_change: PerformanceReport (可选)
    """

    def __init__(self):
        self._versions: list[dict] = []
        self._rollback_threshold = 0.9  # 新参数绩效低于旧参数 90% 时回滚

    def save_version(self, strategy_id: str, params: StrategyParams) -> int:
        """保存参数版本，返回版本号"""
        version = len(self._versions) + 1
        self._versions.append({
            "version": version,
            "strategy_id": strategy_id,
            "params_snapshot": params.__dict__.copy(),
            "applied_at": datetime.now().isoformat(),
        })
        return version

    def check_rollback(self, strategy_id: str, old_perf: PerformanceReport,
                       new_perf: PerformanceReport) -> Optional[str]:
        """
        检查是否需要回滚。

        条件: 新参数绩效 < 旧参数绩效 × rollback_threshold
        返回: 回滚原因(None=不回滚)
        """
        if new_perf.trades < 5:  # 数据太少不判断
            return None

        old_win = old_perf.win_rate or 0
        new_win = new_perf.win_rate or 0

        if old_win > 0 and new_win < old_win * self._rollback_threshold:
            return (f"新参数胜率 {new_win:.1%} 低于旧参数 {old_win:.1%} 的 "
                    f"{self._rollback_threshold:.0%}, 触发回滚")

        old_pf = old_perf.profit_factor or 0
        new_pf = new_perf.profit_factor or 0

        if old_pf > 1 and new_pf < old_pf * self._rollback_threshold:
            return f"新参数盈亏比 {new_pf:.1f} 低于旧参数 {old_pf:.1f}, 触发回滚"

        return None

    def rollback(self, strategy_id: str, strategies: dict) -> Optional[str]:
        """
        回滚到上一个版本。

        流程:
            1. 找到上一个版本
            2. 恢复参数
            3. 标记当前版本为 "rolled_back"
        """
        strategy_versions = [v for v in self._versions
                             if v["strategy_id"] == strategy_id]
        if len(strategy_versions) < 2:
            return None

        # 找到当前版本前的最后一个未回滚版本
        for v in reversed(strategy_versions[:-1]):
            if v.get("status") != "rolled_back":
                strategy = strategies.get(strategy_id)
                if strategy:
                    # 恢复参数
                    for key, val in v["params_snapshot"].items():
                        setattr(strategy.params, key, val)
                    # 标记当前版本已回滚
                    strategy_versions[-1]["status"] = "rolled_back"
                    return f"回滚到版本 {v['version']} (应用时间: {v['applied_at']})"
        return None
```

---

## 五、冷启动管理

```python
# src/optimization/cold_start.py

class ColdStartManager:
    """
    新策略/新参数冷启动管理。

    新策略加入系统时:
        1. 权重为 0 (不参与信号聚合)
        2. 每次评估记录交易结果但不执行
        3. 满 30 笔交易 + 7 天后 → 加入活跃池
        4. 进入活跃池时记录初始参数版本

    从 config.yaml 加载新策略时:
        1. 检查 registry 中是否已存在
        2. 不存在 → 创建实例 → 进入冷启动
        3. 存在 → 复用现有实例

    策略状态:
        evaluation: 评估中 (权重=0)
        active:     活跃 (权重从 config 读取)
        paused:     暂停 (权重=0, 手动或自动暂停)
    """

    EVALUATION_TRADES = 30
    WARMUP_DAYS = 7

    def __init__(self, tracker):
        self._tracker = tracker  # Tracker 实例

    async def get_status(self, strategy_id: str) -> StrategyStatus:
        """获取策略冷启动状态"""
        trades = len(await self._tracker.get_strategy_trades(strategy_id))
        first_trade = await self._tracker.get_first_trade_date(strategy_id)
        days_active = (datetime.now() - first_trade).days if first_trade else 0

        if trades >= self.EVALUATION_TRADES and days_active >= self.WARMUP_DAYS:
            return StrategyStatus(
                phase="active", weight=1.0,
                trades_done=trades,
                message="已通过冷启动，进入活跃池"
            )

        return StrategyStatus(
            phase="evaluation", weight=0.0,
            trades_done=trades,
            trades_needed=max(0, self.EVALUATION_TRADES - trades),
            message=f"冷启动: {trades}/{self.EVALUATION_TRADES}笔 "
                    f"{days_active}/{self.WARMUP_DAYS}天"
        )

    async def should_participate(self, strategy_id: str) -> bool:
        """策略是否应参与信号聚合"""
        status = await self.get_status(strategy_id)
        return status.phase == "active"
```

---

## 六、Web API

### FastAPI 应用

```python
# src/api/server.py

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from src.api.routes import market, analysis, signals, trading, strategies, optimization, safety
from src.api.dependencies import get_executor, get_scanner

app = FastAPI(
    title="Bitget Stock Trader API",
    description="AI 美股合约交易分析工具",
    version="1.0.0",
)

# CORS (允许 Web UI 访问)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(market.router, prefix="/api/v1")
app.include_router(analysis.router, prefix="/api/v1")
app.include_router(signals.router, prefix="/api/v1")
app.include_router(trading.router, prefix="/api/v1")
app.include_router(strategies.router, prefix="/api/v1")
app.include_router(optimization.router, prefix="/api/v1")
app.include_router(safety.router, prefix="/api/v1")


@app.get("/api/health")
async def health():
    """健康检查"""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}
```

### REST API 端点一览

| 方法 | 路径 | 功能 | 返回 |
|------|------|------|------|
| GET | `/api/health` | 健康检查 | `{status, timestamp}` |
| GET | `/api/v1/symbols` | 可交易品种 | list of ContractInfo |
| GET | `/api/v1/quote/{symbol}` | 实时行情 | Quote |
| GET | `/api/v1/klines/{symbol}` | K线 | list of Kline |
| POST | `/api/v1/analyze/{symbol}` | 单品种分析 | ScoreResult |
| POST | `/api/v1/scan` | 全品种扫描 | list of ScoreResult |
| GET | `/api/v1/signals` | 当前信号 | list of Signal |
| GET | `/api/v1/positions` | 当前持仓 | list of Position |
| GET | `/api/v1/balance` | 账户余额 | AccountBalance |
| GET | `/api/v1/history` | 交易历史 | list of TradeRecord |
| GET | `/api/v1/performance` | 绩效统计 | PerformanceReport |
| GET | `/api/v1/strategies` | 策略列表 | dict of StrategyStatus |
| PUT | `/api/v1/strategies/{id}/params` | 热更新参数 | 更新后的参数 |
| PUT | `/api/v1/strategies/{id}/toggle` | 启用/禁用 | 新状态 |
| GET | `/api/v1/optimization/suggestions` | 调优建议 | list of ParamSuggestion |
| POST | `/api/v1/optimization/apply` | 应用建议 | 应用结果 |
| GET | `/api/v1/optimization/versions` | 参数版本历史 | list of version |
| POST | `/api/v1/optimization/rollback/{version}` | 回滚到版本 | 回滚结果 |
| GET | `/api/v1/safety/events` | 安全事件日志 | list of SafetyEvent |
| GET | `/api/v1/safety/rules` | 安全规则状态 | dict |
| WS | `/ws/ticks` | 实时行情推送 | Quote |
| WS | `/ws/signals` | 实时信号推送 | Signal |

### WebSocket 实时推送

```python
# src/api/websocket.py

class WebSocketManager:
    """
    WebSocket 连接管理器。

    推送通道:
        ticks:   每 60s 推送所有已配置品种的行情
        signals: 新信号生成时立即推送
        trades:  交易执行时立即推送
    """

    def __init__(self):
        self._connections: dict[str, set[WebSocket]] = {
            "ticks": set(),
            "signals": set(),
            "trades": set(),
        }

    async def connect(self, websocket: WebSocket, channel: str):
        await websocket.accept()
        self._connections.setdefault(channel, set()).add(websocket)

    async def disconnect(self, websocket: WebSocket, channel: str):
        self._connections.get(channel, set()).discard(websocket)

    async def broadcast(self, channel: str, data: dict):
        """向所有监听该频道的客户端推送"""
        for ws in self._connections.get(channel, set()).copy():
            try:
                await ws.send_json(data)
            except Exception:
                self._connections[channel].discard(ws)


# 在 main.py 中注册 WebSocket
@app.websocket("/ws/ticks")
async def ws_ticks(websocket: WebSocket):
    await ws_manager.connect(websocket, "ticks")
    try:
        while True:
            await websocket.receive_text()  # 保持连接
    except:
        await ws_manager.disconnect(websocket, "ticks")
```

### 依赖注入

```python
# src/api/dependencies.py

from src.config.loader import get_config
from src.trading.executor_factory import create_executor

# 全局实例 (在 API 启动时初始化)
_executor = None
_scanner = None
_registry = None


def get_executor():
    """获取 Executor 实例 (自动响应 mode 变更)"""
    global _executor
    config = get_config()
    mode = config.get("mode", "paper")
    if _executor is None or _executor.name != mode:
        _executor = create_executor(mode, config.raw)
    return _executor


def get_registry():
    """获取 DataSourceRegistry"""
    global _registry
    if _registry is None:
        from src.datasources.base import registry
        _registry = registry
    return _registry
```

---

## 七、配置补充

```yaml
# config.yaml — Phase 4 新增字段

api:
  host: "0.0.0.0"
  port: 8000
  cors_origins: ["*"]

optimization:
  auto_tune: false           # 是否自动调优
  trigger_interval: "weekly" # daily / weekly / manual
  trades_per_analysis: 50    # 每 N 笔交易触发一次分析
  rollback_enabled: true     # 是否自动回滚
  rollback_threshold: 0.9    # 新参数绩效低于旧参数 90% 时回滚

deepseek:
  api_key: ""                # 从环境变量读取
  base_url: "https://opencode.ai/zen/go/v1"
  model: "deepseek-v4-pro"
```

---

## 八、main.py 补充

```python
# main.py — Phase 4 新增命令

async def cmd_server():
    """启动 Web 服务"""
    import uvicorn
    from src.api.server import app
    config = get_config()
    
    # 启动前初始化
    print("初始化数据源...")
    _init_datasources()
    print("初始化策略...")
    _init_strategies()
    print(f"启动 API 服务: http://{config.api_host}:{config.api_port}")
    
    uvicorn.run(app, host=config.api_host, port=config.api_port)

async def cmd_optimize():
    """运行参数优化"""
    tracker = Tracker()
    trades = await tracker.get_all_closed_trades()
    analyzer = PerformanceAnalyzer()
    report = await analyzer.analyze(trades)
    
    tuner = ParamTuner()
    suggestions = await tuner.tune(report, strategy_registry.get_all())
    
    if suggestions:
        print(f"参数调优建议 ({len(suggestions)} 条):")
        for s in suggestions:
            print(f"  [{s.strategy_id}] {s.param_path}: "
                  f"{s.current_value} → {s.suggested_value} "
                  f"(置信度: {s.confidence:.0%})")
            print(f"    理由: {s.reasoning}")
    else:
        print("暂无调优建议")
```

---

## 九、文件清单

| 文件 | TODO | 参考 |
|------|------|------|
| `src/optimization/performance.py` | `TODO[Phase4]` | 本设计 §二 |
| `src/optimization/param_tuner.py` | `TODO[Phase4]` | 本设计 §三 + PSEUDOCODE.md §10 |
| `src/optimization/version.py` | `TODO[Phase4]` | 本设计 §四 |
| `src/optimization/cold_start.py` | `TODO[Phase4]` | 本设计 §五 + PSEUDOCODE.md §11 |
| `src/api/server.py` | `TODO[Phase4]` | 本设计 §六 |
| `src/api/dependencies.py` | `TODO[Phase4]` | 本设计 §六 |
| `src/api/websocket.py` | `TODO[Phase4]` | 本设计 §六 |
| `src/api/routes/market.py` | `TODO[Phase4]` | 端点在 §六 |
| `src/api/routes/analysis.py` | `TODO[Phase4]` | 端点在 §六 |
| `src/api/routes/signals.py` | `TODO[Phase4]` | 端点在 §六 |
| `src/api/routes/trading.py` | `TODO[Phase4]` | 端点在 §六 |
| `src/api/routes/strategies.py` | `TODO[Phase4]` | 端点在 §六 |
| `src/api/routes/optimization.py` | `TODO[Phase4]` | 端点在 §六 |
| `src/api/routes/safety.py` | `TODO[Phase4]` | 端点在 §六 |

---

## 十、验收标准

```
Phase 4 完成时:

1. python main.py --server
   └── 启动 FastAPI 服务 → http://localhost:8000/docs (Swagger)
   └── /api/health → 200 OK

2. WebSocket 实时推送:
   └── ws://localhost:8000/ws/ticks → 每 60s 收到行情
   └── ws://localhost:8000/ws/signals → 新信号即时推送

3. HTTP API:
   └── GET /api/v1/quote/AAPL → 行情
   └── GET /api/v1/balance → 余额
   └── POST /api/v1/scan → 全品种扫描
   └── GET /api/v1/signals → 当前信号
   └── GET /api/v1/performance → 绩效

4. 参数调优:
   └── python main.py --optimize
       → 输出调优建议 (或 "暂无建议")
   └── GET /api/v1/optimization/suggestions → 调优建议列表
   └── POST /api/v1/optimization/apply → 应用建议
   └── GET /api/v1/optimization/versions → 版本历史

5. 自动回滚:
   └── 模拟: 新参数胜率低于旧参数 90% → 自动回滚
   └── 日志记录回滚事件

6. 冷启动:
   └── 新策略自动进入 evaluation 模式
   └── 满 30 笔交易后自动切换为 active
```

---

> **下一篇**: 确认设计无误后，回复 **编码开始** 进入 Phase 4 实现阶段。
