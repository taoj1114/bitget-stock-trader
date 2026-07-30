"""趋势突破策略 — MA金叉/死叉 + 放量 + RSI 趋势跟踪"""

from typing import Optional

from src.core.interfaces import SignalStrategy
from src.core.types import (
    AnalysisContext, Kline, Signal, TechnicalIndicators, TrendBreakParams,
)
from src.trading.levels import calc_dynamic_levels


class TrendBreakStrategy(SignalStrategy):
    """趋势突破策略 — 均线金叉死叉信号"""

    def __init__(self) -> None:
        self._params = TrendBreakParams()
        self._last_signals: dict[tuple[str, str], dict] = {}

    @property
    def id(self) -> str:
        return "trend_break"

    @property
    def name(self) -> str:
        return "趋势突破"

    @property
    def params(self) -> TrendBreakParams:
        return self._params

    @params.setter
    def params(self, p: TrendBreakParams) -> None:
        self._params = p

    async def evaluate(self, ctx: AnalysisContext) -> Optional[Signal]:
        p = self.params
        ind = ctx.indicators

        if not p.enabled:
            return None

        entry = ctx.quote.mark_price
        if entry <= 0:
            return None

        regime = ctx.market_regime.regime

        # ═══ 多头检查 ═══════════════════════════
        bull_flags = 0
        if ind.ma10 > ind.ma30 and ind.ma10_prev <= ind.ma30_prev:
            bull_flags += 1
        if ind.ma30 > ind.ma30_prev:
            bull_flags += 1
        if ind.volume_ratio > p.volume_ratio_threshold:
            bull_flags += 1
        if ind.rsi14 < p.rsi_upper:
            bull_flags += 1
        if regime not in ("trend_up", "weak_trend"):
            bull_flags -= 1

        if self._in_cooldown(ctx.symbol, "BUY", ctx.klines, p.cooldown_bars):
            bull_flags = 0
        if ctx.current_position is not None and ctx.current_position.side == "LONG":
            bull_flags = 0

        if bull_flags >= 3:
            atr = ind.atr14 or entry * 0.02
            levels = calc_dynamic_levels(entry, atr, is_long=True,
                                         regime=ctx.market_regime,
                                         base_sl_mult=p.atr_sl_multiplier)
            return Signal(
                strategy_id=self.id, symbol=ctx.symbol,
                action="STRONG_BUY" if bull_flags >= 4 else "BUY",
                confidence=bull_flags / 4.0,
                entry_price=entry,
                stop_loss=levels["stop_loss"],
                take_profits=[levels["take_profit_1"], levels["take_profit_2"]],
                reason=f"MA金叉 {ind.ma10:.1f}>{ind.ma30:.1f} 放量{ind.volume_ratio:.1f}x RSI{ind.rsi14:.0f} SL{levels['sl_pct']}% TP{levels['tp1_pct']}%/{levels['tp2_pct']}%",
                timestamp=ctx.quote.timestamp,
            )

        # ═══ 空头检查 ═══════════════════════════
        bear_flags = 0
        if ind.ma10 < ind.ma30 and ind.ma10_prev >= ind.ma30_prev:
            bear_flags += 1
        if ind.volume_ratio > p.volume_ratio_threshold:
            bear_flags += 1
        if regime in ("trend_down", "weak_trend"):
            bear_flags += 1

        if self._in_cooldown(ctx.symbol, "SELL", ctx.klines, p.cooldown_bars):
            bear_flags = 0
        if ctx.current_position is not None and ctx.current_position.side == "SHORT":
            bear_flags = 0

        if bear_flags >= 2:
            atr = ind.atr14 or entry * 0.02
            levels = calc_dynamic_levels(entry, atr, is_long=False,
                                         regime=ctx.market_regime,
                                         base_sl_mult=p.atr_sl_multiplier)
            return Signal(
                strategy_id=self.id, symbol=ctx.symbol,
                action="SELL",
                confidence=bear_flags / 3.0,
                entry_price=entry,
                stop_loss=levels["stop_loss"],
                take_profits=[levels["take_profit_1"], levels["take_profit_2"]],
                reason=f"MA死叉 {ind.ma10:.1f}<{ind.ma30:.1f} 放量{ind.volume_ratio:.1f}x SL{levels['sl_pct']}% TP{levels['tp1_pct']}%/{levels['tp2_pct']}%",
                timestamp=ctx.quote.timestamp,
            )

        return None

    def _in_cooldown(self, symbol: str, side: str, klines: list[Kline], bars: int) -> bool:
        last = self._last_signals.get((symbol, side))
        if last is None:
            return False
        bars_since = len(klines) - last.get("kline_index", 0)
        return bars_since < bars
