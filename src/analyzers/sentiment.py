"""新闻情绪分析器 — 基于关键词打分"""

import re
from typing import Optional

from src.core.types import NewsItem


class SentimentAnalyzer:
    """新闻情绪分析器。基于关键词匹配，无需外部 API。

    用法:
        analyzer = SentimentAnalyzer()
        result = analyzer.score(news_items)
        # → {"positive": 0.6, "negative": 0.2, "neutral": 0.2, "overall": 0.4, "count": 5}
    """

    # 正面关键词（按强度分组）
    STRONG_POSITIVE = {
        "surge", "soar", "breakthrough", "record profit", "outperform",
        "beat earnings", "upgraded", "bullish", "exceed expectations",
        "dividend increase", "buyback", "all-time high",
    }
    WEAK_POSITIVE = {
        "beat", "upgrade", "growth", "positive", "innovation",
        "expansion", "partnership", "launch", "profit", "gain",
        "recovery", "momentum", "confidence", "optimistic",
        "improvement", "strong", "opportunity", "ahead of",
    }

    # 负面关键词
    STRONG_NEGATIVE = {
        "plunge", "crash", "bankruptcy", "lawsuit", "fraud",
        "investigation", "recall", "downgraded", "miss earnings",
        "sell-off", "bearish", "below expectations", "layoff",
    }
    WEAK_NEGATIVE = {
        "drop", "decline", "loss", "fall", "miss", "underperform",
        "regulatory", "fine", "delay", "ban", "risk", "uncertainty",
        "weak", "downgrade", "concern", "volatile", "cut",
    }

    # 强烈词权重
    STRONG_WEIGHT = 3.0
    WEAK_WEIGHT = 1.0

    @classmethod
    def score(cls, news_items: list[NewsItem]) -> dict:
        """对新闻列表进行情绪打分。

        Args:
            news_items: 新闻条目列表

        Returns:
            dict: {"positive": float, "negative": float,
                   "neutral": float, "overall": float,
                   "count": int}
        """
        if not news_items:
            return {"positive": 0.0, "negative": 0.0,
                    "neutral": 1.0, "overall": 0.0, "count": 0}

        total_score = 0.0
        scored_count = 0

        for item in news_items:
            text = cls._normalize(item.title + " " + item.snippet)
            score = cls._score_text(text)
            if score != 0:
                scored_count += 1
            total_score += score

        if scored_count == 0:
            return {"positive": 0.0, "negative": 0.0,
                    "neutral": 1.0, "overall": 0.0, "count": len(news_items)}

        pos = max(0, total_score / (scored_count * cls.STRONG_WEIGHT))
        neg = max(0, -total_score / (scored_count * cls.STRONG_WEIGHT))
        neu = 1.0 - pos - neg if pos + neg <= 1.0 else 0.0

        # 归一化到 0-1
        total = pos + neg + neu
        pos /= total
        neg /= total
        neu /= total

        return {
            "positive": round(pos, 4),
            "negative": round(neg, 4),
            "neutral": round(neu, 4),
            "overall": round(pos - neg, 4),
            "count": len(news_items),
        }

    @classmethod
    def to_score_100(cls, sentiment: dict) -> float:
        """将情绪结果映射到 0-100 分数。

        overall = -1 (极负面) → 0
        overall =  0 (中性)   → 50
        overall = +1 (极正面) → 100
        """
        return (sentiment["overall"] + 1.0) * 50.0

    @classmethod
    def _normalize(cls, text: str) -> str:
        """清洗文本：转小写、去标点"""
        text = text.lower()
        text = re.sub(r"[^\w\s]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @classmethod
    def _score_text(cls, text: str) -> float:
        """对单条文本打分。返回正值=正面，负值=负面。"""
        score = 0.0
        words = set(text.split())

        # 多词词组匹配
        for phrase in cls.STRONG_POSITIVE:
            if phrase in text:
                score += cls.STRONG_WEIGHT
        for phrase in cls.STRONG_NEGATIVE:
            if phrase in text:
                score -= cls.STRONG_WEIGHT

        # 单次匹配
        for word in words:
            if word in cls.WEAK_POSITIVE:
                score += cls.WEAK_WEIGHT
            if word in cls.WEAK_NEGATIVE:
                score -= cls.WEAK_WEIGHT

        return score


class NullSentimentAnalyzer:
    """空实现 — 不分析情绪，默认全部中性。

    在未配置新闻源时使用。
    """

    @classmethod
    def score(cls, news_items: list) -> dict:
        return {"positive": 0.0, "negative": 0.0,
                "neutral": 1.0, "overall": 0.0, "count": len(news_items)}

    @classmethod
    def to_score_100(cls, sentiment: dict) -> float:
        return 50.0
