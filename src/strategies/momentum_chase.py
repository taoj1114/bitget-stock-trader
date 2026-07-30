"""顺势追踪策略 — 趋势方向跟随

核心逻辑:
    - trend_down + ADX>20 + 价格在 MA10 之下 → SELL (顺势做空)
    - trend_up   + ADX>20 + 价格在 MA10 之上 → BUY  (顺势做多)

定位: 与 RsiBounce 互补。RsiBounce 是反转策略（猜底/猜顶），
      MomentumChase 是趋势策略（追涨/追跌）。

止盈止损: 动态 ATR 计算，趋势市中 TP 放宽，让利润奔跑。
"""

from src.core.interfaces import SignalStrategy
from src.core.types import AnalysisContext, Signal
from src.trading.levels import calc_dynamic_levels
from src.strategies.regime_filter import filter_trade_direction


class MomentumChaseStrategy(SignalStrategy):
    """顺势追踪策略"""

    def __init__(self):
        self.params = MomentumChaseParams()
        self._last_signals: dict = {}

    @property
    def id(self) -> str:
        return "momentum_chase"

    @property
    def name(self) -> str:
        return "顺势追踪"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def params(self) -> MomentumChaseParams:
        return self._params

    @params.setter
    def params(self, value: MomentumChaseParams):
        self._params = value

    async def evaluate(self, ctx: AnalysisContext) -> Signal | None:
        ind = ctx.indicators
        regime = ctx.market_regime.regime
        adx = ctx.market_regime.adx
        entry = ctx.quote.mark_price

        atr = ind.atr14 or entry * 0.02
        mr = ctx.market_regime  # full MarketRegime object

        # ── 顺势做空 ──
        if regime == "trend_down" and adx > self.params.adx_threshold:
            if not filter_trade_direction(regime, adx, "SELL"):
                return None
            if entry < ind.ma10:
                levels = calc_dynamic_levels(entry, atr, is_long=False,
                                             regime=mr)
                return Signal(
                    strategy_id=self.id, symbol=ctx.symbol,
                    action="SELL", confidence=min(0.75, adx / 60),
                    entry_price=entry,
                    stop_loss=levels["stop_loss"],
                    take_profits=[levels["take_profit_1"]],
                    reason=f"下跌趋势 ADX={adx:.0f} 顺势做空 SL{levels['sl_pct']}% TP{levels['tp1_pct']}%",
                )

        # ── 顺势做多 ──
        if regime == "trend_up" and adx > self.params.adx_threshold:
            if not filter_trade_direction(regime, adx, "BUY"):
                return None
            if entry > ind.ma10:
                levels = calc_dynamic_levels(entry, atr, is_long=True,
                                             regime=mr)
                return Signal(
                    strategy_id=self.id, symbol=ctx.symbol,
                    action="BUY", confidence=min(0.75, adx / 60),
                    entry_price=entry,
                    stop_loss=levels["stop_loss"],
                    take_profits=[levels["take_profit_1"]],
                    reason=f"上升趋势 ADX={adx:.0f} 顺势做多 SL{levels['sl_pct']}% TP{levels['tp1_pct']}%",
                )

        return None


# 简洁参数（不需要复杂配置）
from dataclasses import dataclass

@dataclass
class MomentumChaseParams:
    """顺势策略参数"""
    adx_threshold: int = 20  # ADX > 20 才认为是有效趋势
    min_cooldown: int = 6    # 最小冷却期 (6根1H = 6小时)
