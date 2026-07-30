"""信号评分器 — 多因子加权评分

权重: 技术35% + AI30% + 基本面20% + 情绪15%
"""

from typing import Optional

from src.core.types import (
    EffectiveSignal, MarketRegime, FundamentalData,
    TechnicalIndicators, ScoreResult,
)


class SignalScorer:
    """多因子信号评分器。"""

    WEIGHTS = {"technical": 0.35, "ai": 0.30, "fundamental": 0.20, "sentiment": 0.15}

    # 做空方向权重调整：基本面+新闻权重提升，技术权重降低
    SHORT_WEIGHTS = {"technical": 0.25, "ai": 0.20, "fundamental": 0.30, "sentiment": 0.25}

    REGIME_ADJUSTMENT = {
        "trend_up":    {"trend": 1.5, "reversal": 0.5, "ai": 1.0},
        "trend_down":  {"trend": 1.3, "reversal": 0.5, "ai": 1.0},
        "range_bound": {"trend": 0.5, "reversal": 1.5, "ai": 1.0},
        "weak_trend":  {"trend": 1.0, "reversal": 1.0, "ai": 1.0},
    }

    STRATEGY_TYPES = {
        "trend_break": "trend",
        "rsi_bounce": "reversal",
        "ai_composite": "ai",
    }

    async def score(
        self,
        effective_signals: list[EffectiveSignal],
        market_regime: MarketRegime,
        fundamentals: Optional[FundamentalData] = None,
        news_sentiment: Optional[dict] = None,
        indicators: Optional[TechnicalIndicators] = None,
    ) -> ScoreResult:
        technical = self._calc_technical(effective_signals, market_regime)
        ai = self._calc_ai(effective_signals)
        fundamental = self._calc_fundamental(fundamentals)
        sentiment = self._calc_sentiment(news_sentiment)

        # 方向感知：做空信号加权基本面+新闻
        is_short = any(eff.signal.action in ("SELL", "STRONG_SELL") for eff in effective_signals)
        w = self.SHORT_WEIGHTS if is_short else self.WEIGHTS

        total = (
            technical * w["technical"]
            + ai * w["ai"]
            + fundamental * w["fundamental"]
            + sentiment * w["sentiment"]
        )
        total = max(0, min(100, total))

        return ScoreResult(
            total_score=round(total, 2),
            action=self._to_action(total),
            breakdown={
                "technical": round(technical, 2),
                "ai": round(ai, 2),
                "fundamental": round(fundamental, 2),
                "sentiment": round(sentiment, 2),
            },
        )

    def _calc_technical(self, signals: list[EffectiveSignal], regime: MarketRegime) -> float:
        if not signals:
            return 50.0
        adj = self.REGIME_ADJUSTMENT.get(regime.regime, {})
        total = 0.0
        for eff in signals:
            sig = eff.signal
            stype = self.STRATEGY_TYPES.get(sig.strategy_id, "trend")
            factor = adj.get(stype, 1.0)
            base = {"STRONG_BUY": 90, "BUY": 70, "SELL": 30, "STRONG_SELL": 10}.get(sig.action, 50)
            total += base * sig.confidence * factor * eff.weight
        return max(0, min(100, total / len(signals)))

    def _calc_ai(self, signals: list[EffectiveSignal]) -> float:
        ai_signals = [e for e in signals if e.signal.strategy_id == "ai_composite"]
        if not ai_signals:
            return 50.0
        total = 0.0
        for eff in ai_signals:
            sig = eff.signal
            base = {"STRONG_BUY": 90, "BUY": 70, "SELL": 30, "STRONG_SELL": 10}.get(sig.action, 50)
            total += base * sig.confidence * eff.weight
        return max(0, min(100, total / len(ai_signals)))

    def _calc_fundamental(self, funds: Optional[FundamentalData]) -> float:
        if funds is None:
            return 50.0
        score = 50.0
        if funds.revenue_yoy and funds.revenue_yoy > 20:
            score += 15
        if funds.net_profit_yoy and funds.net_profit_yoy > 20:
            score += 10
        if funds.roe and funds.roe > 15:
            score += 10
        if funds.gross_margin and funds.gross_margin > 40:
            score += 5
        if funds.debt_ratio and funds.debt_ratio > 70:
            score -= 10
        if funds.net_profit is not None and funds.net_profit < 0:
            score -= 20
        return max(0, min(100, score))

    def _calc_sentiment(self, sentiment: Optional[dict]) -> float:
        if sentiment is None:
            return 50.0
        pos = sentiment.get("positive", 0)
        neg = sentiment.get("negative", 0)
        neu = sentiment.get("neutral", 0)
        return max(0, min(100, pos * 100 - neg * 50 + neu * 50))

    @staticmethod
    def _to_action(score: float) -> str:
        if score >= 80:
            return "STRONG_BUY"
        if score >= 65:
            return "BUY"
        if score >= 35:
            return "HOLD"
        if score >= 20:
            return "SELL"
        return "STRONG_SELL"
