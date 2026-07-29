"""测试新闻情绪分析器和滑点模型"""

from datetime import datetime, timezone, timedelta

from src.core.types import NewsItem
from src.analyzers.sentiment import SentimentAnalyzer, NullSentimentAnalyzer
from src.trading.slippage import SlippageModel


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


class TestSlippageModel:
    """SlippageModel 测试"""

    def setup_method(self):
        self.model = SlippageModel()

    @staticmethod
    def _bjt_time(hour: int, minute: int = 0) -> datetime:
        """构造指定北京时间的时间对象"""
        utc_hour = hour - 8
        day_offset = 0
        if utc_hour < 0:
            utc_hour += 24
            day_offset = -1
        return (datetime(2026, 7, 30, utc_hour, minute, 0, tzinfo=timezone.utc)
                + timedelta(days=day_offset))

    def test_regular_hours_megacap(self):
        """主力时段 + 大盘股 → 最低滑点"""
        now = self._bjt_time(22, 0)
        assert self.model.get_spread("AAPL", now=now) == 0.0002

    def test_asia_hours(self):
        """亚盘时段 → 最高滑点"""
        now = self._bjt_time(10, 0)
        assert self.model.get_spread("AAPL", now=now) == 0.0025

    def test_regular_hours_small(self):
        """主力时段 + 非大盘股 → 比大盘高"""
        now = self._bjt_time(22, 0)
        # 未识别品种默认 mid=2.0x
        unknown = self.model.get_spread("ZZZZZ", now=now)
        assert unknown == 0.0004  # 0.0002 × 2.0

    def test_large_cap_correct_factor(self):
        """修复 Bug #2: large-cap 应有自己的流动性因子 (1.5x)"""
        now = self._bjt_time(22, 0)
        nflx = self.model.get_spread("NFLX", now=now)
        assert nflx == 0.0003  # 0.0002 × 1.5

    def test_etf_correct_factor(self):
        """修复 Bug #2: ETF 应有自己的流动性因子 (0.8x)"""
        now = self._bjt_time(22, 0)
        spy = self.model.get_spread("SPY", now=now)
        assert spy == round(0.0002 * 0.8, 6)  # 0.00016

    def test_timezone_boundary_precision(self):
        """
        修复 Design #5: 时段边界精确到分钟。
        21:15 BJT → extended (16:00-21:30)
        21:30 BJT → regular
        """
        # 21:15 → extended
        s1 = self.model.get_spread("AAPL", now=self._bjt_time(21, 15))
        assert s1 == 0.0010

        # 21:30 → regular
        s2 = self.model.get_spread("AAPL", now=self._bjt_time(21, 30))
        assert s2 == 0.0002

        # 04:00 → extended (盘后)
        s3 = self.model.get_spread("AAPL", now=self._bjt_time(4, 0))
        assert s3 == 0.0010

        # 03:59 → regular
        s4 = self.model.get_spread("AAPL", now=self._bjt_time(3, 59))
        assert s4 == 0.0002

    def test_high_volatility(self):
        """高波动率 → 滑点 1.5x"""
        now = self._bjt_time(22)
        high = self.model.get_spread("AAPL", volatility="high", now=now)
        assert high == round(0.0002 * 1.5, 6)

    def test_low_volatility(self):
        """低波动率 → 滑点 0.8x"""
        now = self._bjt_time(22)
        low = self.model.get_spread("AAPL", volatility="low", now=now)
        assert low == round(0.0002 * 0.8, 6)

    def test_extended_hours(self):
        """盘前盘后时段"""
        # 17:00 BJT = 盘前
        spread1 = self.model.get_spread("AAPL", now=self._bjt_time(17, 0))
        assert spread1 == 0.0010

        # 05:00 BJT = 盘后
        spread2 = self.model.get_spread("AAPL", now=self._bjt_time(5, 0))
        assert spread2 == 0.0010

    def test_get_slippage_percent(self):
        """百分比字符串格式"""
        s = self.model.get_slippage_percent("AAPL", now=self._bjt_time(22, 0))
        assert s == "0.020%"

    def test_custom_config(self):
        """自定义配置覆盖默认"""
        model = SlippageModel({"base_spreads": {"regular": 0.0005}})
        assert model.get_spread("AAPL", now=self._bjt_time(22, 0)) == 0.0005
