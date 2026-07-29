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
| `src/core/interfaces.py` | ✅ 设计完整 | DataSource(含get_symbols)/NewsSource/Strategy/Executor/SafetyRule |
| `src/core/types.py` | ✅ 设计完整 | 全部DTO，含AnalysisContext.news_sentiment + AccountBalance.available_balance |
| `src/core/exceptions.py` | ✅ 设计完整 | 自定义异常体系 |
| `src/datasources/base.py` | ✅ 设计完整 | 基类 + DataSourceRegistry |
| `src/datasources/news/registry.py` | ✅ 设计完整 | 新闻源注册 + 主/备用降级 |
| `src/trading/executor.py` | ✅ 设计完整 | Executor 抽象接口 |

### 逻辑检查状态

| 检查项 | 状态 |
|--------|------|
| 接口定义 vs 伪代码使用 | ✅ 已对齐 |
| 数据模型字段一致性 | ✅ 已修复所有不匹配 |
| 方法命名一致性 | ✅ `calculate()` → `score()` 已统一 |
| 未定义函数/方法 | ✅ 补全 `_in_cooldown()` / `build_reason()` / `parse_search_result()` / 背离检测 |
| RSI 策略 `_prev_macd` 初始化 | ✅ 已修复 |
| SentimentAnalyzer 伪代码 | ✅ 已补全 |
| Fundamental 评分 net_margin | ✅ 已补充 |
| config.yaml 策略结构 | ✅ 列表格式修正 |

### 设计文档

| 文件 | 内容 |
|------|------|
| `PHASE2_DESIGN.md` | 技术指标/市场状态/SentimentAnalyzer/三大策略/评分器/AI分析器 |
| (外部) `PSEUDOCODE.md` | 13个模块伪代码 |

## 文件统计

- 架构核心（保留）：~1600 行
- 设计文档：~75KB（PLAN + PSEUDOCODE + PHASE2_DESIGN）
