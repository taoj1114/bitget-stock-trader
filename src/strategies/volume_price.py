"""量价背离策略 — 主力资金动向检测

核心逻辑:
    价涨量缩 → 顶背离 → SELL (上涨乏力，主力出货)
    价跌量增 → 底背离 → BUY  (恐慌性抛售被买入，主力吸筹)

原理:
    正常上涨应伴随放量 (需求驱动)
    正常下跌应伴随缩量 (抛售枯竭)
    背离 = 趋势不可持续的反转信号

Wyckoff 理论: 量价背离是最可靠的反转信号之一
"""

from src.core.interfaces import SignalStrategy
from src.core.types import AnalysisContext, Signal, Kline
from src.trading.levels import calc_dynamic_levels
from src.strategies.regime_filter import filter_trade_direction


class VolumePriceStrategy(SignalStrategy):
    """量价背离策略"""

    def __init__(self):
        self._params = VolumePriceParams()

    @property
    def id(self) -> str:
        return "volume_price"

    @property
    def name(self) -> str:
        return "量价背离"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def params(self) -> VolumePriceParams:
        return self._params

    @params.setter
    def params(self, value: VolumePriceParams):
        self._params = value

    async def evaluate(self, ctx: AnalysisContext) -> Signal | None:
        ind = ctx.indicators
        regime = ctx.market_regime
        entry = ctx.quote.mark_price
        klines = ctx.klines

        if len(klines) < 10:
            return None

        atr = ind.atr14 or entry * 0.02

        # ── 顶背离: 价格涨但量缩 = 上涨无力 → SELL ──
        price_rising = self._price_rising(klines, 5)
        volume_falling = self._volume_falling(klines, 5)

        if price_rising and volume_falling:
            if not filter_trade_direction(regime.regime, regime.adx, "SELL"):
                return None

            # 需要 RSI 偏高配合
            if ind.rsi14 > 55 or regime.regime == "trend_up":
                levels = calc_dynamic_levels(entry, atr, is_long=False, regime=regime)
                return Signal(
                    strategy_id=self.id, symbol=ctx.symbol,
                    action="SELL", confidence=0.7,
                    entry_price=entry,
                    stop_loss=levels["stop_loss"],
                    take_profits=[levels["take_profit_1"]],
                    reason=f"顶背离 价涨量缩 RSI{ind.rsi14:.0f} SL{levels['sl_pct']}%",
                )

        # ── 底背离: 价格跌但量增 = 恐慌吸筹 → BUY ──
        price_falling = self._price_falling(klines, 5)
        volume_rising = self._volume_rising(klines, 5)

        if price_falling and volume_rising:
            if not filter_trade_direction(regime.regime, regime.adx, "BUY"):
                return None

            if ind.rsi14 < 50 or regime.regime == "trend_down":
                levels = calc_dynamic_levels(entry, atr, is_long=True, regime=regime)
                return Signal(
                    strategy_id=self.id, symbol=ctx.symbol,
                    action="BUY", confidence=0.7,
                    entry_price=entry,
                    stop_loss=levels["stop_loss"],
                    take_profits=[levels["take_profit_1"]],
                    reason=f"底背离 价跌量增 RSI{ind.rsi14:.0f} SL{levels['sl_pct']}%",
                )

        return None

    def _price_rising(self, klines: list[Kline], window: int = 5) -> bool:
        """近 window 根 K线价格在涨"""
        if len(klines) < window:
            return False
        recent = klines[-window:]
        return recent[-1].close > recent[0].close * (1 + self.params.min_chg)

    def _price_falling(self, klines: list[Kline], window: int = 5) -> bool:
        """近 window 根 K线价格在跌"""
        if len(klines) < window:
            return False
        recent = klines[-window:]
        return recent[-1].close < recent[0].close * (1 - self.params.min_chg)

    def _volume_rising(self, klines: list[Kline], window: int = 5) -> bool:
        """近 window 根 K线量在增"""
        if len(klines) < window * 2:
            return False
        recent_vol = sum(k.volume for k in klines[-window:])
        prev_vol = sum(k.volume for k in klines[-window*2:-window])
        if prev_vol == 0:
            return False
        return recent_vol / prev_vol > self.params.vol_ratio

    def _volume_falling(self, klines: list[Kline], window: int = 5) -> bool:
        """近 window 根 K线量在缩"""
        if len(klines) < window * 2:
            return False
        recent_vol = sum(k.volume for k in klines[-window:])
        prev_vol = sum(k.volume for k in klines[-window*2:-window])
        if prev_vol == 0:
            return False
        return recent_vol / prev_vol < 1 / self.params.vol_ratio


from dataclasses import dataclass


@dataclass
class VolumePriceParams:
    """量价背离参数"""
    min_chg: float = 0.01       # 最小价格变化 (1%)
    vol_ratio: float = 1.5      # 量比阈值 (>1.5 = 放量, <0.67 = 缩量)
