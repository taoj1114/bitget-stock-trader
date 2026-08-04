"""测试新闻情绪分析器"""

from datetime import datetime, timezone, timedelta

from src.core.types import NewsItem
from src.analyzers.sentiment import SentimentAnalyzer, NullSentimentAnalyzer


class TestSentimentAnalyzer:
    """SentimentAnalyzer 测试"""

    def test_positive_news(self):
        """正面新闻 → overall > 0"""
        items = [NewsItem(
            title="Apple beats earnings, stock surges on record profit",
            snippet="AAPL shares hit all-time high after strong quarter",
            url="http://example.com",
        )]
        result = SentimentAnalyzer.score(items)
        assert result["positive"] > result["negative"]
        assert result["overall"] > 0
        assert result["count"] == 1
        assert not result["conflict"]

    def test_negative_news(self):
        """负面新闻 → overall < 0"""
        items = [NewsItem(
            title="Tesla miss earnings, shares plunge on weak demand",
            snippet="TSLA faces regulatory investigation, analysts downgrade",
            url="http://example.com",
        )]
        result = SentimentAnalyzer.score(items)
        assert result["negative"] > result["positive"]
        assert result["overall"] < 0
        assert not result["conflict"]

    def test_mixed_news(self):
        """混合情绪 → 综合判断"""
        items = [
            NewsItem(title="Strong growth ahead, company expands", snippet="...", url=""),
            NewsItem(title="Regulatory concerns rise, stock drops", snippet="...", url=""),
        ]
        result = SentimentAnalyzer.score(items)
        assert abs(result["overall"]) < 0.5

    def test_neutral_news(self):
        """中性新闻 → overall ≈ 0"""
        items = [NewsItem(
            title="Company announces date for quarterly report",
            snippet="The meeting will be held at 2pm EST",
            url="http://example.com",
        )]
        result = SentimentAnalyzer.score(items)
        assert result["neutral"] > 0.5
        assert abs(result["overall"]) < 0.3
        assert not result["conflict"]

    def test_empty_list(self):
        """空列表 → 全部中性"""
        result = SentimentAnalyzer.score([])
        assert result["neutral"] == 1.0
        assert result["overall"] == 0.0
        assert result["count"] == 0
        assert not result["conflict"]

    def test_to_score_100(self):
        """to_score_100 映射正确"""
        assert SentimentAnalyzer.to_score_100({"overall": 1.0}) == 100.0
        assert SentimentAnalyzer.to_score_100({"overall": 0.0}) == 50.0
        assert SentimentAnalyzer.to_score_100({"overall": -1.0}) == 0.0
        assert SentimentAnalyzer.to_score_100({"overall": 0.5}) == 75.0
        assert SentimentAnalyzer.to_score_100({"overall": -0.5}) == 25.0

    def test_multi_word_phrase_matching(self):
        """多词词组匹配"""
        items = [NewsItem(
            title="Company beat earnings expectations this quarter",
            snippet="Revenue exceeded all forecasts",
            url="",
        )]
        result = SentimentAnalyzer.score(items)
        assert result["positive"] > result["negative"]
        assert result["overall"] > 0

    def test_no_double_count_on_overlap(self):
        """
        修复 Bug #1: 强词组中的词不应在弱匹配中重复计数。
        'beat earnings' 命中 STRONG_POSITIVE(+3)，'beat' 不应再次
        命中 WEAK_POSITIVE(+1)。
        """
        items = [NewsItem(
            title="Company beat earnings today",
            snippet="",
            url="",
        )]
        # 'beat earnings' → +3，'beat' → 不计算
        result = SentimentAnalyzer.score(items)
        # 计算过程: total_score=3, scored_count=1
        # pos = 3/(1*3) = 1.0
        assert result["positive"] == 1.0
        assert result["negative"] == 0.0
        assert result["overall"] == 1.0

    def test_conflict_detection(self):
        """
        修复 Design #3: 强正面+强负面应标记为矛盾。
        """
        items = [
            NewsItem(title="Stock surges on record profit", snippet="", url=""),
            NewsItem(title="Stock crashes on investigation", snippet="", url=""),
        ]
        result = SentimentAnalyzer.score(items)
        # 'surges' 不是词库词，但 'record profit' 是 STRONG_POSITIVE
        # 'crashes' 不在词库，但 'crash' 不在 STRONG_NEGATIVE...
        # 关键是强正 + 强负都有 → conflict
        assert result["conflict"] is True
        # 正负相互抵消，overall 接近中性
        assert abs(result["overall"]) < 0.6

    def test_null_analyzer(self):
        """NullSentimentAnalyzer 总是中性"""
        items = [NewsItem(title="anything", snippet="...", url="")]
        result = NullSentimentAnalyzer.score(items)
        assert result["neutral"] == 1.0
        assert result["overall"] == 0.0
        assert not result["conflict"]

        score = NullSentimentAnalyzer.to_score_100(result)
        assert score == 50.0
