"""参数种群 — 多组参数并行竞赛 + AI 进化优化

每代流程:
    1. 种群 N 组参数并行运行（纸盘或回测）
    2. 排名：综合夏普+胜率-回撤
    3. 保留 top 3（精英）
    4. AI 分析 top 3 → 生成 2 个新参数（变异）
    5. 替换 bottom 2 → 下一代

参数编码:
    trend_break:  {fast_ma, slow_ma, volume_ratio_threshold, atr_sl_multiplier, cooldown_bars}
    rsi_bounce:   {oversold, overbought, atr_sl_multiplier}
    ai_composite: {min_confidence}
"""

import json
import random
import time
from copy import deepcopy
from dataclasses import asdict
from typing import Any

from src.core.types import PerformanceMetrics
from src.strategies.trend_break import TrendBreakStrategy
from src.strategies.rsi_bounce import RsiBounceStrategy
from src.strategies.ai_composite import AICompositeStrategy


# ── 参数空间定义 ──────────────────────────────────

PARAM_SPACE = {
    "trend_break": {
        "fast_ma":                {"min": 5,  "max": 30,  "step": 1,  "default": 10},
        "slow_ma":                {"min": 15, "max": 60,  "step": 1,  "default": 30},
        "volume_ratio_threshold": {"min": 1.0,"max": 3.0, "step": 0.1,"default": 1.5},
        "atr_sl_multiplier":     {"min": 1.0,"max": 5.0, "step": 0.5,"default": 2.0},
        "cooldown_bars":         {"min": 12, "max": 96,  "step": 1,  "default": 48},
    },
    "rsi_bounce": {
        "oversold": {"default": 30, "min": 15, "max": 40, "step": 1},
        "overbought": {"default": 70, "min": 60, "max": 85, "step": 1},
        "atr_sl_multiplier": {"default": 2.0, "min": 1.0, "max": 4.0, "step": 0.5},
    },
    "momentum_chase": {
        "adx_threshold": {"default": 20, "min": 15, "max": 35, "step": 5},
        "min_cooldown": {"default": 6, "min": 2, "max": 24, "step": 2},
    },
    "bollinger_bounce": {
        "volume_dry_threshold": {"default": 0.7, "min": 0.5, "max": 1.0, "step": 0.05},
        "min_bb_width": {"default": 0.02, "min": 0.01, "max": 0.05, "step": 0.005},
    },
    "volume_price": {
        "min_chg": {"default": 0.01, "min": 0.005, "max": 0.03, "step": 0.005},
        "vol_ratio": {"default": 1.5, "min": 1.2, "max": 2.5, "step": 0.1},
    },
    "price_action": {
        "min_vcp_window": {"default": 20, "min": 10, "max": 30, "step": 5},
        "support_lookback": {"default": 20, "min": 10, "max": 40, "step": 5},
    },
    "ai_composite": {
        "min_confidence": {"min": 0.3, "max": 0.9, "step": 0.05, "default": 0.6},
    },
}


class ParamPopulation:
    """参数种群管理器。"""

    def __init__(self, strategy_id: str, population_size: int = 5):
        if strategy_id not in PARAM_SPACE:
            raise ValueError(f"Unknown strategy: {strategy_id}")

        self.strategy_id = strategy_id
        self.space = PARAM_SPACE[strategy_id]
        self.size = population_size
        self.generation = 0
        self.population: list[dict] = []
        self.performance: list[dict] = []  # [{params, metrics}, ...]

        self._initialize()

    def _initialize(self):
        """初始化种群：默认 + N-1 个随机变异。"""
        default = {k: v["default"] for k, v in self.space.items()}
        self.population = [default]
        for _ in range(self.size - 1):
            self.population.append(self._mutate(default, intensity=0.3))

    # ── 变异 ──────────────────────────────────────

    def _mutate(self, params: dict, intensity: float = 0.2) -> dict:
        """随机变异参数。intensity 越大变异幅度越大。"""
        new = {}
        for key, spec in self.space.items():
            base = params.get(key, spec["default"])
            step = spec["step"]
            # 随机偏移 ± intensity × range
            rng = (spec["max"] - spec["min"]) * intensity
            delta = random.uniform(-rng, rng)
            val = base + delta
            # 按 step 取整
            val = round(val / step) * step
            # 修复浮点精度
            if isinstance(step, float) and step < 1:
                val = round(val, len(str(step).split(".")[-1]))
            val = max(spec["min"], min(spec["max"], val))
            new[key] = val
        return new

    def _crossover(self, p1: dict, p2: dict) -> dict:
        """交叉：随机从两个父代中选参数。"""
        child = {}
        for key in self.space:
            child[key] = random.choice([p1.get(key), p2.get(key)])
        return child

    # ── 进化 ──────────────────────────────────────

    def evolve(self, performances: list[dict], ai_suggestions: list[dict] | None = None):
        """一代进化。

        Args:
            performances: [{params: {...}, score: float, ...}, ...] 按 score 降序
            ai_suggestions: AI 生成的新参数（可选，最多 2 个）
        """
        self.performance = performances
        self.generation += 1

        # 排序（score 高在前）
        ranked = sorted(performances, key=lambda p: p.get("score", 0), reverse=True)

        # 精英保留：top 3
        elites = [p["params"] for p in ranked[:3]]

        # AI 建议：最多 2 个
        ai_variants = []
        if ai_suggestions:
            for sug in ai_suggestions[:2]:
                if isinstance(sug, dict) and sug:
                    ai_variants.append(sug)

        # 填充到 population_size
        new_pop = list(elites) + list(ai_variants)
        while len(new_pop) < self.size:
            # 从 top 3 中选 2 个交叉 + 变异
            p1 = random.choice(elites)
            p2 = random.choice(elites)
            child = self._crossover(p1, p2)
            if random.random() < 0.5:
                child = self._mutate(child, intensity=0.1)  # 小变异
            new_pop.append(child)

        self.population = new_pop[:self.size]

    # ── 给 AI 的绩效摘要 ──────────────────────────

    def build_ai_prompt(self) -> str:
        """构建给 AI 的参数优化 prompt。"""
        if not self.performance:
            return "无绩效数据"

        ranked = sorted(self.performance, key=lambda p: p.get("score", 0), reverse=True)

        lines = [
            f"你是一个量化交易策略参数优化专家。",
            f"以下是策略 '{self.strategy_id}' 的第 {self.generation} 代参数竞赛结果：",
            "",
        ]

        for i, p in enumerate(ranked[:5]):
            params = p.get("params", {})
            lines.append(f"## 第{i+1}名 (score={p.get('score', 0):.2f})")
            lines.append(f"参数: {json.dumps(params)}")
            lines.append(f"绩效: 胜率{p.get('win_rate', 0):.1%} "
                        f"盈亏比{p.get('profit_factor', 0):.1f} "
                        f"夏普{p.get('sharpe', 0):.2f} "
                        f"最大回撤{p.get('max_drawdown', 0):.0f}")
            lines.append(f"交易{p.get('trades', 0)}笔 PnL${p.get('total_pnl', 0):.0f}")
            lines.append("")

        lines.extend([
            "参数空间:",
            json.dumps({k: f"[{v['min']}-{v['max']}]" for k, v in self.space.items()}),
            "",
            "请分析 top 3 的共性，给出 2 组可能更优的参数（JSON 数组格式）:",
            '[{"fast_ma": 12, ...}, {"fast_ma": 8, ...}]',
            "只输出 JSON 数组。",
        ])
        return "\n".join(lines)

    # ── 创建策略实例 ──────────────────────────────

    def create_strategy(self, params: dict):
        """用指定参数创建策略实例。"""
        if self.strategy_id == "trend_break":
            s = TrendBreakStrategy()
            from src.core.types import TrendBreakParams
            s.params = TrendBreakParams(**{**asdict(s.params), **params})
            return s
        elif self.strategy_id == "rsi_bounce":
            s = RsiBounceStrategy()
            from src.core.types import RsiBounceParams
            s.params = RsiBounceParams(**{**asdict(s.params), **params})
            return s
        elif self.strategy_id == "ai_composite":
            from src.core.types import AiCompositeParams
            s = AICompositeStrategy()
            s.params = AiCompositeParams(**{**asdict(s.params), **params})
            return s
        elif self.strategy_id == "momentum_chase":
            from src.strategies.momentum_chase import MomentumChaseStrategy, MomentumChaseParams
            s = MomentumChaseStrategy()
            s.params = MomentumChaseParams(**{**asdict(s.params), **params})
            return s
        elif self.strategy_id == "bollinger_bounce":
            from src.strategies.bollinger_bounce import BollingerBounceStrategy, BollingerBounceParams
            s = BollingerBounceStrategy()
            s.params = BollingerBounceParams(**{**asdict(s.params), **params})
            return s
        elif self.strategy_id == "volume_price":
            from src.strategies.volume_price import VolumePriceStrategy, VolumePriceParams
            s = VolumePriceStrategy()
            s.params = VolumePriceParams(**{**asdict(s.params), **params})
            return s
        elif self.strategy_id in ("price_action_vcp", "price_action_support",
                                    "price_action_fakey", "price_action"):
            from src.strategies.price_action import (
                PriceActionVCPStrategy, PriceActionSupportStrategy,
                PriceActionFakeyStrategy, PriceActionParams,
            )
            if self.strategy_id == "price_action_vcp":
                s = PriceActionVCPStrategy()
            elif self.strategy_id == "price_action_fakey":
                s = PriceActionFakeyStrategy()
            else:
                s = PriceActionSupportStrategy()
            s.params = PriceActionParams(**{**asdict(s.params), **params})
            return s
        return None

    def summary(self) -> str:
        return (f"策略={self.strategy_id} 第{self.generation}代 "
                f"种群={self.size} 已评估={len(self.performance)}")
