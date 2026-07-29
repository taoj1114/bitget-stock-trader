# 任务清单

## 设计阶段 ✅ 已完成

| 文档 | 内容 | 状态 |
|------|------|------|
| PLAN.md | 整体架构设计 | ✅ |
| PSEUDOCODE.md | 13个模块伪代码 | ✅ |
| PHASE2_DESIGN.md | 分析引擎+策略系统伪代码 | ✅ |
| STATUS.md | 进度跟踪 | ✅ |
| TASKS.md | 任务清单 | ✅ |

---

## Phase 1: 数据采集层 — 编码任务

### TASK-001: Bitget V3 行情模块
- **文件**: `src/datasources/bitget/market.py`
- **实现**: `BitgetMarketSource.get_quote()`, `get_klines()`, `get_order_book()`
- **API参考**: 文件中 `TODO[Phase1]` 注释
- **验收**: `python main.py --quote AAPL` 输出实时行情 JSON

### TASK-002: Bitget 合约信息
- **文件**: `src/datasources/bitget/symbols.py`
- **实现**: `BitgetSymbolSource.get_stock_symbols()`, `get_symbol_info()`
- **验收**: `python main.py --symbols` 输出 250+ 美股合约

### TASK-003: 限速器
- **文件**: `src/datasources/bitget/rate_limiter.py`
- **实现**: 基于时间间隔的限速器
- **验收**: 连续 20 次请求触发限速

### TASK-004: Eastmoney 代码搜索
- **文件**: `src/datasources/eastmoney/search.py`
- **实现**: `EastmoneySearch.resolve_symbol()`
- **验收**: `resolve_symbol("AAPL")` 返回 secid + secucode

### TASK-005: Eastmoney 行情
- **文件**: `src/datasources/eastmoney/quote.py`
- **实现**: `EastmoneyQuoteSource.get_quote()`
- **验收**: 返回含 PE/PB/市值的 Quote

### TASK-006: Eastmoney 基本面
- **文件**: `src/datasources/eastmoney/fundamentals.py`
- **实现**: `EastmoneyFundamentalSource.get_fundamentals()`
- **验收**: 返回营收/ROE/净利润

### TASK-007: SearXNG 新闻源
- **文件**: `src/datasources/news/searxng.py`
- **实现**: `SearXNGNewsSource.fetch_news()`
- **验收**: `python main.py --news AAPL` 输出新闻列表

### TASK-008: 缓存系统
- **文件**: `src/cache/memory.py`, `src/cache/persistent.py`
- **验收**: 内存缓存命中/过期正确，K线持久化增量和全量

### TASK-009: 配置系统
- **文件**: `src/config/loader.py`, `src/config/schema.py`
- **验收**: config.yaml 加载 + 热更新

### TASK-010: CLI 入口
- **文件**: `main.py`
- **验收**: 所有命令可用，JSON 输出

---

## Phase 2: 分析引擎 + 策略系统 — 编码任务

### TASK-011: 技术指标计算
- **文件**: `src/analyzers/technical.py`
- **验收**: `python main.py --analyze AAPL` 输出包含 MA/RSI/MACD 的分析

### TASK-012: 市场状态识别
- **文件**: `src/analyzers/market_regime.py`
- **验收**: 趋势/震荡/波动率判断准确

### TASK-013: 三大策略
- **文件**: 
  - `src/strategies/trend_break.py`
  - `src/strategies/rsi_bounce.py`
  - `src/strategies/ai_composite.py`
- **验收**: 各策略输出信号

### TASK-014: 信号聚合+评分
- **文件**: `src/signals/scorer.py`, `src/signals/aggregator.py`
- **验收**: 多策略信号综合评分

### TASK-015: AI 分析集成
- **文件**: `src/analyzers/ai.py`
- **验收**: DeepSeek v4-pro 返回结构化分析

### TASK-016: --analyze / --scan CLI
- **验收**: 完整分析报告输出

---

## Phase 3: 模拟交易 + 安全系统

_待规划_

## Phase 4: 参数进化 + Web API

_待规划_
