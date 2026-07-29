# 项目状态

## 整体进度

| Phase | 内容 | 状态 |
|-------|------|------|
| Phase 1 | 数据采集层 | 📋 设计完成（伪代码），待编码 |
| Phase 2 | 分析引擎 + 策略系统 | 📋 设计完成（伪代码），待编码 |
| Phase 3 | 模拟交易 + 安全系统 | 📋 设计完成（伪代码），待编码 |
| Phase 4 | 参数进化 + Web API | 📋 设计完成（伪代码），待编码 |

## 设计文档清单

| 文档 | 内容 | 大小 |
|------|------|------|
| `PLAN.md` | 整体架构 + 7个优化项 + 启动编排 | ~36KB |
| `PSEUDOCODE.md` | 13个模块伪代码 (项目根目录) | ~43KB |
| `PHASE2_DESIGN.md` | 分析引擎 + 策略系统 | ~32KB |
| `PHASE3_DESIGN.md` | 模拟交易 + 安全系统 | ~27KB |
| `PHASE4_DESIGN.md` | 参数进化 + Web API | ~31KB |
| `TESTING.md` | 独立测试方案 + 测试用例 | ~10KB |
| `AI_COLLAB.md` | 多 AI 协作开发指南 | ~5KB |
| `STATUS.md` | 进度跟踪 | — |
| `TASKS.md` | 任务清单 | — |

## 架构核心（保留 — 所有模块的契约）

| 文件 | 用途 |
|------|------|
| `src/core/interfaces.py` | DataSource/NewsSource/Strategy/Executor/SafetyRule |
| `src/core/types.py` | 全部 DTO（含 news_sentiment / available_balance / ParamSuggestion） |
| `src/core/exceptions.py` | 自定义异常体系 |
| `src/datasources/base.py` | 基类 + DataSourceRegistry |
| `src/datasources/news/registry.py` | 新闻源注册 + 主/备用降级 |
| `src/trading/executor.py` | Executor 抽象接口 |

## 环境

```bash
# 安装
cd bitget-stock-trader && uv sync

# 测试
uv run pytest tests/ -x -v

# 指定模块
uv run pytest tests/analyzers/ -x -v
```
