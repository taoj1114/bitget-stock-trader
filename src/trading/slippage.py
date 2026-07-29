"""滑点模型 — 按交易时段 + 品种流动性模拟滑点"""

from datetime import datetime, timezone, timedelta

# 北京时间偏移
_BJT_OFFSET = timedelta(hours=8)


class SlippageModel:
    """模拟真实交易滑点。

    滑点基于:
        - 当前交易时段 (主力/盘前盘后/亚盘)
        - 品种流动性 (大盘股 vs 小盘股)
        - 市场波动率 (高波动时翻倍)

    时段定义 (北京时间):
        regular:   21:30 - 04:00  (美股主力交易时段)
        extended:  04:00 - 06:00 / 16:00 - 21:30 (盘前盘后)
        asia:      06:00 - 16:00  (亚盘，流动性最差)
    """

    # 基准滑点 (百分比，小数)
    BASE_SPREADS = {
        "regular":  0.0002,    # 0.02%
        "extended": 0.0010,    # 0.10%
        "asia":     0.0025,    # 0.25%
    }

    # 流动性因子 (相对于大盘股)
    LIQUIDITY_MAP = {
        "megacap": 1.0,    # AAPL, NVDA, MSFT, GOOGL, AMZN
        "large":   1.5,    # META, TSLA, NFLX, ADBE
        "mid":     2.0,    # PLTR, COIN, MSTR, SMCI
        "small":   3.0,    # 低市值/低成交
        "etf":     0.8,    # QQQ, SPY, IWM
    }

    # 大盘股名单 (megacap)
    _MEGACAP = {"AAPL", "NVDA", "MSFT", "GOOGL", "AMZN",
                "META", "TSLA", "AVGO", "BRKB", "JPM"}

    def __init__(self, config: dict | None = None):
        self.base_spreads = dict(self.BASE_SPREADS)
        if config and "base_spreads" in config:
            self.base_spreads.update(config["base_spreads"])

    def get_spread(self, symbol: str,
                   volatility: str = "normal",
                   now: datetime | None = None) -> float:
        """获取当前滑点。

        Args:
            symbol: 品种名 (如 AAPL, NVDA)
            volatility: 波动率状态 (high / normal / low)
            now: 指定时间 (默认当前时间，注入用于测试)

        Returns:
            float: 滑点 (小数)，如 0.0002 = 0.02%
        """
        window = self._current_window(now)
        base = self.base_spreads.get(window, 0.0010)
        liquidity = self._liquidity_factor(symbol)

        vol_mult = {"high": 1.5, "normal": 1.0, "low": 0.8}
        vol = vol_mult.get(volatility, 1.0)

        return round(base * liquidity * vol, 6)

    def get_slippage_percent(self, symbol: str,
                             volatility: str = "normal",
                             now: datetime | None = None) -> str:
        """获取滑点百分比字符串，用于显示。"""
        return f"{self.get_spread(symbol, volatility, now) * 100:.3f}%"

    def _current_window(self, now: datetime | None = None) -> str:
        """判断当前交易时段"""
        now = now or datetime.now(timezone.utc)
        h = (now + _BJT_OFFSET).hour  # 转北京时间

        if h >= 21 or h < 4:
            return "regular"
        if (4 <= h < 6) or (16 <= h < 21):
            return "extended"
        return "asia"

    def _liquidity_factor(self, symbol: str) -> float:
        """获取品种流动性因子"""
        base = symbol.upper().replace("USDT", "")
        if base in self._MEGACAP:
            return self.LIQUIDITY_MAP["megacap"]
        return self.LIQUIDITY_MAP["mid"]  # 默认中型股
