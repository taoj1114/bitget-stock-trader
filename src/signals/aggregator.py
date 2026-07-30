"""信号聚合器 — 多策略信号合并 + 冲突处理"""

from typing import Optional

from src.core.types import (
    Signal, ScoreResult, MarketRegime, FundamentalData,
    TechnicalIndicators, EffectiveSignal,
)
from src.signals.scorer import SignalScorer


class SignalAggregator:
    """信号聚合器。"""

    def __init__(self, scorer: SignalScorer | None = None):
        self.scorer = scorer or SignalScorer()

    async def aggregate(
        self,
        symbol: str,
        raw_signals: list[Signal],
        market_regime: MarketRegime,
        fundamentals: Optional[FundamentalData] = None,
        news_sentiment: Optional[dict] = None,
        indicators: Optional[TechnicalIndicators] = None,
    ) -> ScoreResult:
        if not raw_signals:
            return ScoreResult(
                total_score=50.0, action="HOLD",
                breakdown={"technical": 50, "ai": 50, "fundamental": 50, "sentiment": 50},
            )

        # 去重：同策略保留最高 confidence
        deduped = self._deduplicate(raw_signals)

        # 冲突检测
        has_buy = any(s.action in ("BUY", "STRONG_BUY") for s in deduped)
        has_sell = any(s.action in ("SELL", "STRONG_SELL") for s in deduped)
        if has_buy and has_sell:
            return ScoreResult(
                total_score=50.0, action="HOLD",
                breakdown={"technical": 50, "ai": 50, "fundamental": 50, "sentiment": 50},
                details={"conflict": True, "signal_count": len(deduped)},
            )

        effective = [EffectiveSignal(signal=s, weight=1.0) for s in deduped]
        result = await self.scorer.score(
            effective_signals=effective, market_regime=market_regime,
            fundamentals=fundamentals, news_sentiment=news_sentiment,
            indicators=indicators or TechnicalIndicators(symbol=symbol, timestamp=0),
        )
        result.details["signal_count"] = len(deduped)
        result.details["strategies"] = [s.strategy_id for s in deduped]
        return result

    @staticmethod
    def _deduplicate(signals: list[Signal]) -> list[Signal]:
        by_strategy: dict[str, Signal] = {}
        for sig in signals:
            existing = by_strategy.get(sig.strategy_id)
            if existing is None or sig.confidence > existing.confidence:
                by_strategy[sig.strategy_id] = sig
        return list(by_strategy.values())
