#!/usr/bin/env python3
"""AI 参数进化优化器

用法:
    PYTHONPATH=. python3 scripts/optimize.py trend_break --generations 5 --trades 30

流程:
    1. 初始化参数种群（5组随机参数）
    2. 每组用相同历史数据/纸盘跑 N 笔交易
    3. 排名 → AI 分析 top 3 → 生成新参数
    4. 替换垫底 → 下一代
"""

import asyncio
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config.loader import get_config
from src.optimization.population import ParamPopulation, PARAM_SPACE
from src.optimization.performance import PerformanceAnalyzer
from src.trading.tracker import Tracker
from src.trading.paper_executor import PaperExecutor
from src.datasources.bitget.market import BitgetMarketSource
from src.analyzers.technical import TechnicalAnalyzer
from src.analyzers.market_regime import MarketRegimeDetector
from src.signals.aggregator import SignalAggregator
from src.core.types import AnalysisContext, Signal

logger = logging.getLogger("optimizer")


class AIOptimizer:
    """AI 驱动的参数进化优化器。"""

    def __init__(self, strategy_id: str, population_size: int = 5, api_key: str = ""):
        if strategy_id not in PARAM_SPACE:
            raise ValueError(f"Unknown strategy: {strategy_id}. Available: {list(PARAM_SPACE)}")

        self.strategy_id = strategy_id
        self.pop = ParamPopulation(strategy_id, population_size)
        self._api_key = api_key or get_config().deepseek.get("api_key", "")
        self._market = BitgetMarketSource()
        self._analyzer = PerformanceAnalyzer()
        self._tech = TechnicalAnalyzer()
        self._regime = MarketRegimeDetector()
        self._aggregator = SignalAggregator()

    async def evaluate_generation(self, symbols: list[str], min_trades: int = 20) -> list[dict]:
        """评估当前种群中每组参数：用真实数据跑策略，看是否触发信号。

        简化版：每组参数对不同品种跑一次扫描，记录触发信号数和平均置信度。
        完整版需要 accumulate paper trades — 但纸盘不支持多实例并行。
        """
        results = []
        for params in self.pop.population:
            strategy = self.pop.create_strategy(params)
            if not strategy:
                continue

            signals = 0
            total_conf = 0.0
            buy_count = sell_count = 0

            for symbol in symbols[:3]:  # 限制品种数
                try:
                    quote = await self._market.get_quote(symbol)
                    klines = await self._market.get_klines(symbol, "1H", 100)
                    if not quote or len(klines) < 30:
                        continue

                    ind = self._tech.calculate(klines)
                    regime = self._regime.detect(klines)
                    ctx = AnalysisContext(
                        symbol=symbol, quote=quote, klines=klines,
                        indicators=ind, fundamentals=None, news=[],
                        market_regime=regime,
                    )

                    sig = await strategy.evaluate(ctx)
                    if sig:
                        signals += 1
                        total_conf += sig.confidence
                        if sig.action in ("BUY", "STRONG_BUY"):
                            buy_count += 1
                        else:
                            sell_count += 1
                except Exception:
                    continue

            avg_conf = total_conf / signals if signals else 0
            # 简易评分：信号多 + 置信度高 + 多空平衡
            score = (min(signals, 10) * 3 + avg_conf * 50 +
                     (5 if buy_count > 0 and sell_count > 0 else 0))

            results.append({
                "params": params,
                "signals": signals,
                "avg_confidence": round(avg_conf, 3),
                "buys": buy_count,
                "sells": sell_count,
                "score": round(score, 2),
            })

        return results

    async def ai_suggest(self, performances: list[dict]) -> list[dict]:
        """调用 AI 分析绩效，生成新一代参数建议。"""
        if not self._api_key:
            logger.warning("无 API key，跳过 AI 建议")
            return []

        prompt = self.pop.build_ai_prompt()
        # 用简单的文本标注 top-3
        ranked = sorted(performances, key=lambda p: p.get("score", 0), reverse=True)
        prompt += "\n\n当前 top 3:\n"
        for i, p in enumerate(ranked[:3]):
            prompt += f"{i+1}. {json.dumps(p['params'])} (score={p['score']})\n"

        try:
            import httpx
            url = "https://opencode.ai/zen/go/v1/chat/completions"
            payload = {
                "model": "deepseek-v4-flash",
                "messages": [
                    {"role": "system", "content": "你是量化策略优化专家。只输出 JSON 数组。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 500,
            }
            headers = {"Authorization": f"Bearer {self._api_key}"}
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    # 提取 JSON 数组
                    import re
                    match = re.search(r"\[.*\]", content, re.DOTALL)
                    if match:
                        suggestions = json.loads(match.group(0))
                        logger.info("AI 生成了 %d 组新参数", len(suggestions))
                        return suggestions[:2]
        except Exception as e:
            logger.error("AI 建议失败: %s", e)

        return []

    async def run(self, symbols: list[str], generations: int = 5):
        """运行完整进化流程。"""
        for gen in range(generations):
            logger.info("=== 第 %d 代 ===", gen + 1)
            logger.info("种群: %s", self.pop.summary())

            # 评估当前种群
            perf = await self.evaluate_generation(symbols)

            if not perf:
                logger.warning("无评估结果，跳过")
                continue

            # 打印排名
            ranked = sorted(perf, key=lambda p: p["score"], reverse=True)
            for i, p in enumerate(ranked):
                logger.info("  #%d score=%.1f signals=%d conf=%.2f params=%s",
                           i+1, p["score"], p["signals"], p["avg_confidence"],
                           json.dumps(p["params"]))

            # AI 建议
            ai_variants = await self.ai_suggest(perf)

            # 进化
            self.pop.evolve(perf, ai_variants)

            if gen < generations - 1:
                await asyncio.sleep(2)

        # 最终结果
        best = ranked[0] if ranked else None
        if best:
            logger.info("=== 最优参数 ===")
            logger.info("策略: %s", self.strategy_id)
            logger.info("参数: %s", json.dumps(best["params"]))
            logger.info("评分: %.1f", best["score"])
            return best
        return None


# ── CLI ───────────────────────────────────────────

async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    args = sys.argv[1:]
    if not args:
        print("Usage: python3 scripts/optimize.py <strategy_id> [--generations 5] [--size 5]")
        print(f"Available: {list(PARAM_SPACE)}")
        return

    strategy_id = args[0]
    generations = 3
    population_size = 5
    symbols = get_config().symbols[:3]

    for i, a in enumerate(args):
        if a == "--generations" and i + 1 < len(args):
            generations = int(args[i + 1])
        if a == "--size" and i + 1 < len(args):
            population_size = int(args[i + 1])

    optimizer = AIOptimizer(strategy_id, population_size)
    best = await optimizer.run(symbols, generations)

    if best:
        print(f"\n最优参数: {json.dumps(best['params'])}")
        print(f"评分: {best['score']}")


if __name__ == "__main__":
    asyncio.run(main())
