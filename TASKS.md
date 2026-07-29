# 任务清单

## Phase 1: 数据采集层

### TASK-001: 项目骨架
- 创建目录结构
- `requirements.txt`
- `config.yaml`（骨架）
- `.gitignore`
- **验收**: `python main.py --help` 显示所有命令

### TASK-002: 核心接口 + 数据模型
- `src/core/interfaces.py` — DataSource/NewsSource/SignalStrategy/Executor
- `src/core/types.py` — Quote/Kline/FundamentalData/Signal/Position/...
- `src/core/exceptions.py` — 自定义异常
- **验收**: 所有接口定义完整，类型提示齐全，可被其他模块 import

### TASK-003: Bitget 行情模块
- `src/datasources/bitget/market.py` — get_quote / get_klines
- `src/datasources/bitget/symbols.py` — get_symbols
- `src/datasources/bitget/rate_limiter.py` — 限速控制
- **验收**: `python main.py --quote AAPL` 输出结构化 JSON

### TASK-004: 缓存层
- `src/cache/memory.py` — TTL 内存缓存
- `src/cache/persistent.py` — SQLite K线缓存
- **验收**: 连续调用 10 次 get_quote 不触发限速，从缓存返回

### TASK-005: Eastmoney 代码搜索
- `src/datasources/eastmoney/search.py` — 解析 secid/secucode
- **验收**: `resolve_symbol("AAPL")` 返回 `{secid, secucode, market}`

### TASK-006: Eastmoney 报价
- `src/datasources/eastmoney/quote.py` — PE/PB/市值
- **验收**: `get_quote("105.AAPL")` 返回包含 PE/PB 的 Quote

### TASK-007: Eastmoney 基本面
- `src/datasources/eastmoney/fundamentals.py` — 财报数据
- **验收**: `get_fundamentals("AAPL")` 返回营收/ROE/净利润

### TASK-008: SearXNG 新闻源
- `src/datasources/news/base.py` — NewsSource 接口
- `src/datasources/news/searxng.py` — SearXNG 实现
- `src/datasources/news/registry.py` — 主/备用降级管理
- **验收**: `python main.py --news AAPL` 输出新闻列表

### TASK-009: 数据源整合 + 注册器
- `src/datasources/base.py` — BaseDataSource
- `src/datasources/registry.py` — 注册器
- **验收**: 启动时打印 "已注册 N 个数据源"

### TASK-010: 配置系统
- `src/config/loader.py` — yaml 加载 + 热更新
- `src/config/schema.py` — 校验
- **验收**: 修改 config.yaml 后系统自动感知

### TASK-011: main.py CLI
- `main.py` — --quote / --klines / --news / --fundamentals / --symbols / --server
- **验收**: 所有 CLI 命令可用，输出 JSON 格式

## Phase 2: 分析引擎 + 策略系统

_待规划_

## Phase 3: 模拟交易 + 安全系统

_待规划_

## Phase 4: 参数进化 + Web API

_待规划_
