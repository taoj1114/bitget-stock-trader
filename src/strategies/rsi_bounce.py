"""RSI 超卖反弹策略 — 逆势反转信号"""

from typing import Optional

from src.core.interfaces import SignalStrategy
from src.core.types import AnalysisContext, Signal, RsiBounceParams
from src.trading.levels import calc_dynamic_levels
from src.strategies.regime_filter import (
    filter_trade_direction, get_min_score_for_regime,
)


class RsiBounceStrategy(SignalStrategy):
    """RSI 超卖反弹 — 震荡市最优"""

    def __init__(self) -> None:
        self._params = RsiBounceParams()
        self._prev_macd: float = 0.0

    @property
    def id(self) -> str:
        return "rsi_bounce"

    @property
    def name(self) -> str:
        return "RSI 超卖反弹"

    @property
    def params(self) -> RsiBounceParams:
        return self._params

    @params.setter
    def params(self, p: RsiBounceParams) -> None:
        self._params = p

    async def evaluate(self, ctx: AnalysisContext) -> Optional[Signal]:
        p = self.params
        ind = ctx.indicators
        regime = ctx.market_regime.regime

        # ── 市场状态过滤 ──
        if regime == "trend_down" and ind.rsi14 > p.overbought:
            return None  # 下跌趋势超买 = 继续跌

        # ═══ 超卖买入 ═══════════════════════════
        if ind.rsi14 < p.oversold:
            divergence = self._detect_bullish_divergence(ctx.klines, ind)
            near_support = self._near_support(ctx.klines, ind.atr14)

            score = 1 + (1 if divergence else 0) + (1 if near_support else 0)

            # 场景化门槛：强下跌中做多需要最高确认，顺势做空仅需1分
            min_score = get_min_score_for_regime(ctx.market_regime.regime,
                                                  ctx.market_regime.adx,
                                                  "BUY", base_min=2)

            # 方向过滤：强下跌趋势禁止做多
            if not filter_trade_direction(ctx.market_regime.regime,
                                          ctx.market_regime.adx, "BUY"):
                return None

            if score >= min_score:
                entry = ctx.quote.mark_price
                atr = ind.atr14 or entry * 0.02
                levels = calc_dynamic_levels(entry, atr, is_long=True,
                                             regime=ctx.market_regime,
                                             base_sl_mult=p.atr_sl_multiplier)
                return Signal(
                    strategy_id=self.id, symbol=ctx.symbol,
                    action="BUY", confidence=score / 3.0,
                    entry_price=entry,
                    stop_loss=levels["stop_loss"],
                    take_profits=[levels["take_profit_1"]],
                    reason=f"RSI({ind.rsi14:.0f})超卖{' MACD底背离' if divergence else ''} SL{levels['sl_pct']}% TP{levels['tp1_pct']}%",
                )

        # ═══ 超买卖出 ═══════════════════════════
        if ind.rsi14 > p.overbought:
            divergence = self._detect_bearish_divergence(ctx.klines, ind)
            near_resist = self._near_resistance(ctx.klines, ind.atr14)

            score = 1 + (1 if divergence else 0) + (1 if near_resist else 0)

            min_score = get_min_score_for_regime(ctx.market_regime.regime,
                                                  ctx.market_regime.adx,
                                                  "SELL", base_min=2)

            if not filter_trade_direction(ctx.market_regime.regime,
                                          ctx.market_regime.adx, "SELL"):
                return None

            if score >= min_score:
                entry = ctx.quote.mark_price
                atr = ind.atr14 or entry * 0.02
                levels = calc_dynamic_levels(entry, atr, is_long=False,
                                             regime=ctx.market_regime,
                                             base_sl_mult=p.atr_sl_multiplier)
                return Signal(
                    strategy_id=self.id, symbol=ctx.symbol,
                    action="SELL", confidence=score / 3.0,
                    entry_price=entry,
                    stop_loss=levels["stop_loss"],
                    take_profits=[levels["take_profit_1"]],
                    reason=f"RSI({ind.rsi14:.0f})超买{' MACD顶背离' if divergence else ''} SL{levels['sl_pct']}% TP{levels['tp1_pct']}%",
                )

        return None

    def _detect_bullish_divergence(self, klines, ind) -> bool:
        if len(klines) < 5:
            return False
        price_lower = klines[-1].low < klines[-3].low
        macd_higher = ind.macd > self._prev_macd
        self._prev_macd = ind.macd
        return price_lower and macd_higher

    def _detect_bearish_divergence(self, klines, ind) -> bool:
        if len(klines) < 5:
            return False
        price_higher = klines[-1].high > klines[-3].high
        macd_lower = ind.macd < self._prev_macd
        self._prev_macd = ind.macd
        return price_higher and macd_lower

    def _near_support(self, klines, atr: float) -> bool:
        if len(klines) < 20 or atr <= 0:
            return False
        support = min(k.low for k in klines[-20:])
        return abs(klines[-1].close - support) <= atr * 1.5

    def _near_resistance(self, klines, atr: float) -> bool:
        if len(klines) < 20 or atr <= 0:
            return False
        resistance = max(k.high for k in klines[-20:])
        return abs(resistance - klines[-1].close) <= atr * 1.5
