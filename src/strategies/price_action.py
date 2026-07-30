"""Price Action 策略集

含三个经典策略:
    ① VCP突破 (Mark Minervini 招牌) — 振幅收缩后大阳突破
    ② 关键支撑反转 (Buy the Dip) — 支撑位锤子线/吞没确认
    ③ 内嵌棒假突破 (Inside Bar Fakey) — 机构诱多/诱空陷阱
"""

from src.core.interfaces import SignalStrategy
from src.core.types import AnalysisContext, Signal
from src.trading.levels import calc_dynamic_levels
from src.strategies.regime_filter import filter_trade_direction
from src.analyzers.patterns import (
    detect_patterns, detect_vcp, CandlestickPatterns,
)


class PriceActionVCPStrategy(SignalStrategy):
    """VCP突破策略 — 振幅收缩 + 放量大阳 = 主升浪启动

    检测: 20根K线振幅递减 + 成交量萎缩 + 大阳线突破
    入场: 大阳线收盘确认
    止损: 突破K线最低价
    """

    @property
    def id(self) -> str: return "price_action_vcp"
    @property
    def name(self) -> str: return "VCP突破"
    @property
    def version(self) -> str: return "1.0.0"

    def __init__(self):
        self._params = PriceActionParams()

    @property
    def params(self): return self._params
    @params.setter
    def params(self, v): self._params = v

    async def evaluate(self, ctx: AnalysisContext) -> Signal | None:
        entry = ctx.quote.mark_price
        atr = ctx.indicators.atr14 or entry * 0.02
        regime = ctx.market_regime

        # VCP 检测
        is_vcp, squeeze = detect_vcp(ctx.klines, window=20)
        if not is_vcp or squeeze > 0.5:
            return None

        # 需要大阳线突破
        patterns = detect_patterns(ctx.klines)
        if not patterns.marubozu_bull:
            return None

        # 方向过滤
        if not filter_trade_direction(regime.regime, regime.adx, "BUY"):
            return None

        # 突破确认: 收盘高于前5根高点
        recent_high = max(k.high for k in ctx.klines[-6:-1])
        if entry <= recent_high:
            return None

        levels = calc_dynamic_levels(entry, atr, is_long=True, regime=regime)
        return Signal(
            strategy_id=self.id, symbol=ctx.symbol,
            action="BUY", confidence=0.8,
            entry_price=entry,
            stop_loss=levels["stop_loss"],
            take_profits=[levels["take_profit_1"]],
            reason=f"VCP突破 squeeze={squeeze:.1%} SL{levels['sl_pct']}%",
        )


class PriceActionSupportStrategy(SignalStrategy):
    """关键支撑反转策略 — 支撑位锤子线/吞没 = 机构托盘

    检测: 价格在支撑位 + 看涨形态 (锤子线/吞没/启明星)
    入场: 形态确认后
    止损: 形态最低价
    """

    @property
    def id(self) -> str: return "price_action_support"
    @property
    def name(self) -> str: return "支撑反转"
    @property
    def version(self) -> str: return "1.0.0"

    def __init__(self):
        self._params = PriceActionParams()

    @property
    def params(self): return self._params
    @params.setter
    def params(self, v): self._params = v

    async def evaluate(self, ctx: AnalysisContext) -> Signal | None:
        entry = ctx.quote.mark_price
        atr = ctx.indicators.atr14 or entry * 0.02
        regime = ctx.market_regime
        patterns = detect_patterns(ctx.klines)

        # 必须在支撑位
        if not patterns.near_support:
            return None

        # 看涨形态确认
        has_bullish = (patterns.hammer or patterns.bullish_engulfing or
                       patterns.morning_star)
        if not has_bullish:
            return None

        # 方向过滤
        if not filter_trade_direction(regime.regime, regime.adx, "BUY"):
            return None

        # 形态强度评分
        if patterns.morning_star:
            conf = 0.85
        elif patterns.bullish_engulfing:
            conf = 0.75
        else:
            conf = 0.65

        levels = calc_dynamic_levels(entry, atr, is_long=True, regime=regime)
        return Signal(
            strategy_id=self.id, symbol=ctx.symbol,
            action="BUY", confidence=conf,
            entry_price=entry,
            stop_loss=levels["stop_loss"],
            take_profits=[levels["take_profit_1"]],
            reason=f"支撑反转 pattern={self._pattern_name(patterns)} SL{levels['sl_pct']}%",
        )

    @staticmethod
    def _pattern_name(p: CandlestickPatterns) -> str:
        if p.morning_star: return "启明星"
        if p.bullish_engulfing: return "吞没"
        if p.hammer: return "锤子线"
        return "unknown"


class PriceActionFakeyStrategy(SignalStrategy):
    """内嵌棒假突破策略 — 机构诱多/诱空陷阱

    检测: Inside Bar → 假突破 (Pin Bar) → 反转
    逻辑: 第一根大K线(母) → 第二根内嵌(子) → 第三根看似突破但反转
    入场: 价格回到内嵌棒区间内
    止损: 假突破K线最高/最低点
    """

    @property
    def id(self) -> str: return "price_action_fakey"
    @property
    def name(self) -> str: return "假突破陷阱"
    @property
    def version(self) -> str: return "1.0.0"

    def __init__(self):
        self._params = PriceActionParams()

    @property
    def params(self): return self._params
    @params.setter
    def params(self, v): self._params = v

    async def evaluate(self, ctx: AnalysisContext) -> Signal | None:
        if len(ctx.klines) < 4:
            return None

        entry = ctx.quote.mark_price
        atr = ctx.indicators.atr14 or entry * 0.02
        regime = ctx.market_regime
        patterns = detect_patterns(ctx.klines)

        # 必须有内嵌棒 + 流星线 (上假突破) 或 锤子线 (下假突破)
        if not patterns.inside_bar:
            return None

        # ── 上假突破做空: Inside Bar → 向上假突破 → 流星线 ──
        if patterns.shooting_star:
            if not filter_trade_direction(regime.regime, regime.adx, "SELL"):
                return None
            levels = calc_dynamic_levels(entry, atr, is_long=False, regime=regime)
            return Signal(
                strategy_id=self.id, symbol=ctx.symbol,
                action="SELL", confidence=0.7,
                entry_price=entry,
                stop_loss=levels["stop_loss"],
                take_profits=[levels["take_profit_1"]],
                reason=f"上假突破 流星线 SL{levels['sl_pct']}%",
            )

        # ── 下假突破做多: Inside Bar → 向下假突破 → 锤子线 ──
        if patterns.hammer:
            if not filter_trade_direction(regime.regime, regime.adx, "BUY"):
                return None
            levels = calc_dynamic_levels(entry, atr, is_long=True, regime=regime)
            return Signal(
                strategy_id=self.id, symbol=ctx.symbol,
                action="BUY", confidence=0.7,
                entry_price=entry,
                stop_loss=levels["stop_loss"],
                take_profits=[levels["take_profit_1"]],
                reason=f"下假突破 锤子线 SL{levels['sl_pct']}%",
            )

        return None


from dataclasses import dataclass


@dataclass
class PriceActionParams:
    """Price Action 策略通用参数"""
    min_vcp_window: int = 20
    support_lookback: int = 20
    fakey_confirm_bars: int = 1
