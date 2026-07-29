# 项目状态

## 整体进度

| Phase | 内容 | 状态 |
|-------|------|------|
| Phase 1 | 数据采集层 | 📋 设计完成（伪代码），待编码 |
| Phase 2 | 分析引擎 + 策略系统 | 📋 设计完成（伪代码），待编码 |
| Phase 3 | 模拟交易 + 安全系统 | 🔴 未开始 |
| Phase 4 | 参数进化 + Web API | 🔴 未开始 |

## 当前文件状态

### 架构核心（保留 — 所有模块的契约）

| 文件 | 类型 | 用途 |
|------|------|------|
| `src/core/interfaces.py` | ✅ 设计完整 | DataSource/NewsSource/Strategy/Executor 接口 |
| `src/core/types.py` | ✅ 设计完整 | 全部 DTO 模型（Quote/Kline/Signal/Position/...） |
| `src/core/exceptions.py` | ✅ 设计完整 | 自定义异常体系 |
| `src/datasources/base.py` | ✅ 设计完整 | 基类 + DataSourceRegistry |
| `src/datasources/news/registry.py` | ✅ 设计完整 | 新闻源注册 + 主/备用降级 |
| `src/trading/executor.py` | ✅ 设计完整 | Executor 抽象接口 |

### 实现存根（伪代码 — 待编码）

| 文件 | TODO |
|------|------|
| `src/datasources/bitget/market.py` | `TODO[Phase1]` Bitget 行情/K线/订单本 |
| `src/datasources/bitget/symbols.py` | `TODO[Phase1]` 合约信息 |
| `src/datasources/bitget/rate_limiter.py` | `TODO[Phase1]` 限速器 |
| `src/datasources/eastmoney/search.py` | `TODO[Phase1]` 代码搜索 |
| `src/datasources/eastmoney/quote.py` | `TODO[Phase1]` Push2 报价 |
| `src/datasources/eastmoney/fundamentals.py` | `TODO[Phase1]` 基本面数据 |
| `src/datasources/news/searxng.py` | `TODO[Phase1]` 新闻获取 |
| `src/cache/memory.py` | `TODO[Phase1]` 内存缓存 |
| `src/cache/persistent.py` | `TODO[Phase1]` SQLite K线缓存 |
| `src/config/loader.py` | `TODO[Phase1]` 配置加载 + 热更新 |
| `src/main.py` | `TODO[Phase1]` CLI 入口 |

### Phase 2 设计文档

| 文件 | 内容 |
|------|------|
| `PHASE2_DESIGN.md` | 完整设计（技术指标/市场状态/三大策略/评分器/AI分析器） |
| `src/analyzers/*` | 伪代码设计 |
| `src/strategies/*` | 伪代码设计 |
| `src/signals/*` | 伪代码设计 |

## 文件数统计

- 架构核心（保留）：~1500 行
- 实现存根（TODO）：~500 行
- 设计文档：~70KB（PLAN + PSEUDOCODE + PHASE2_DESIGN）
