# AI 协作开发指南

> 如何让多个 AI 同时工作在不同模块，并且不互相冲突。

---

## 一、角色分工

```
AI-0 (架构维护)
  └── 交付: core/types.py, core/interfaces.py
  └── 维护: STATUS.md, TASKS.md
  └── 协调: 各 AI 之间的接口变更

AI-1 (Phase 1 数据源层)
  └── src/datasources/bitget/market.py
  └── src/datasources/bitget/symbols.py
  └── src/datasources/eastmoney/quote.py
  └── src/datasources/eastmoney/fundamentals.py
  └── src/datasources/eastmoney/search.py
  └── src/datasources/news/searxng.py
  └── src/cache/memory.py, src/cache/persistent.py
  └── src/config/loader.py
  └── 测试: tests/datasources/

AI-2 (Phase 2 分析引擎)
  └── src/analyzers/technical.py
  └── src/analyzers/market_regime.py
  └── src/analyzers/sentiment.py
  └── 测试: tests/analyzers/

AI-3 (Phase 2 策略 + 信号)
  └── src/strategies/trend_break.py
  └── src/strategies/rsi_bounce.py
  └── src/strategies/ai_composite.py
  └── src/strategies/registry.py
  └── src/strategies/convergence.py
  └── src/signals/scorer.py, aggregator.py
  └── src/analyzers/ai.py
  └── 测试: tests/strategies/, tests/signals/

AI-4 (Phase 3 交易执行)
  └── src/trading/paper_executor.py
  └── src/trading/real_executor.py（接口预留）
  └── src/trading/safety.py
  └── src/trading/slippage.py
  └── src/trading/risk_manager.py
  └── src/trading/tracker.py
  └── src/trading/executor_factory.py
  └── 测试: tests/trading/

AI-5 (Phase 4 优化 + API)
  └── src/optimization/performance.py
  └── src/optimization/param_tuner.py
  └── src/optimization/version.py
  └── src/optimization/cold_start.py
  └── src/api/server.py, routes/*, websocket.py
  └── 测试: tests/optimization/, tests/api/

AI-6 (集成)
  └── main.py
  └── tests/test_integration.py
  └── 验证端到端: 数据源→分析→信号→交易
```

---

## 二、开发顺序

```
Phase 0: AI-0 交付 core/types.py + core/interfaces.py
              其他 AI 等待 (预计 1 次对话)
                 ↓
Phase 1: AI-1 独立开始 (数据源层)
Phase 2: AI-2 + AI-3 可并行开始 (分析 + 策略)
Phase 3: AI-4 等 AI-2/3 交付后开始 (交易依赖信号)
Phase 4: AI-5 随时可开始 (优化/API 只依赖 core)
                 ↓
            AI-6 集成 (等所有人)
```

**关键路径**: AI-0 → AI-2(分析) → AI-4(交易)
**可并行**: AI-1 + AI-2 + AI-3 (数据/分析/策略)

---

## 三、AI 交接清单

### 新 AI 加入时读取（按顺序）

| 优先级 | 文件 | 获取什么 |
|--------|------|----------|
| 1 | `STATUS.md` | 进度 + 下一个任务 |
| 2 | `core/types.py` | 所有数据模型 |
| 3 | `core/interfaces.py` | 所有接口契约 |
| 4 | `PHASE{N}_DESIGN.md` | 该 Phase 的设计细节 |
| 5 | `TASKS.md` | 具体任务列表 |
| 6 | `pyproject.toml` + `uv.lock` | 依赖环境 |

### 完成一个 AI 任务时的输出

```
输出清单:
  1. 所有代码文件（实现接口）
  2. 所有测试文件（通过 pytest）
  3. 更新 STATUS.md（标记已完成的 TASK）
  4. 更新 TASKS.md（标记已完成的 TASK）
```

---

## 四、不冲突规则

### 文件归属
每个文件**只有一个 AI 负责**。不跨 AI 修改。

```
√ AI-1 改 src/datasources/bitget/market.py
√ AI-2 改 src/analyzers/technical.py
× AI-1 改 src/analyzers/technical.py  ← 冲突
```

### 跨 AI 通信（通过接口，不通过代码）
AI-2（分析引擎）需要 `Kline[]`，这个在 `types.py` 中已定义。
AI-3（策略）需要 `AnalysisContext`，在 `types.py` 中已定义。
AI-4（交易）需要 `Signal`，在 `types.py` 中已定义。

**不需要互相沟通**。接口定义在 `core/interfaces.py` + `core/types.py`，任何人不能私自改。

如果 AI-3 发现 `Signal` 缺字段，流程是：

```
AI-3 → 通知 AI-0 + 提出建议 → AI-0 判断 → AI-0 改 types.py → 通知所有人
```

### 版本管理
```bash
# 所有 AI 使用同一套依赖
cd bitget-stock-trader && uv sync

# 添加新依赖
uv add <package>         # 自动更新 pyproject.toml + uv.lock
uv add --dev <package>   # 开发依赖

# 同步 lock
uv lock
```

依赖变更也必须由 AI-0 协调。

---

## 五、Git 分支策略

```
main                  ← 稳定，只有 AI-0 可以合并
  ├── feat/phase1-datasources   ← AI-1
  ├── feat/phase2-analyzers     ← AI-2
  ├── feat/phase2-strategies    ← AI-3
  ├── feat/phase3-trading       ← AI-4
  ├── feat/phase4-optimization  ← AI-5
  └── feat/integration          ← AI-6
```

每个 AI 在自己分支上工作，完成后提 PR。AI-0 审查代码一致性后合并。

---

## 六、快速开始

```bash
# 克隆
git clone https://github.com/taoj1114/bitget-stock-trader.git
cd bitget-stock-trader

# 安装依赖 (uv 保证版本一致)
uv sync

# 创建分支
git checkout -b feat/phase2-analyzers

# 运行测试
uv run pytest tests/ -x -v

# 查看任务
cat TASKS.md | grep "TASK-" | grep -i "TODO"

# 编码参考
cat PHASE2_DESIGN.md | head -50
```
