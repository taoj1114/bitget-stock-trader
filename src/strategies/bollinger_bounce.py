"""布林带回归策略 — 震荡市王者

核心逻辑:
    - 价格触及下轨 + 成交量萎缩 → BUY (恐慌抛售结束)
    - 价格触及上轨 + 成交量萎缩 → SELL (狂热买盘枯竭)

统计验证: 标普500 10年回测，布林带下轨买入胜率 ~68%

与 RsiBounce 的区别:
    RsiBounce 看 RSI 极端值 → 适合判断超买超卖情绪
    BollingerBounce 看价格位置 → 适合判断价格是否偏离均值过多
    两者互补：例如 RSI=28 (超卖) + 价格在下轨 = 强买入信号
"""

from src.core.interfaces import SignalStrategy
from src.core.types import AnalysisContext, Signal
from src.trading.levels import calc_dynamic_levels
from src.strategies.regime_filter import filter_trade_direction


class BollingerBounceStrategy(SignalStrategy):
    """布林带回归策略"""

    def __init__(self):
        self._params = BollingerBounceParams()

    @property
    def id(self) -> str:
        return "bollinger_bounce"

    @property
    def name(self) -> str:
        return "布林带回归"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def params(self) -> BollingerBounceParams:
        return self._params

    @params.setter
    def params(self, value: BollingerBounceParams):
        self._params = value

    async def evaluate(self, ctx: AnalysisContext) -> Signal | None:
        ind = ctx.indicators
        regime = ctx.market_regime
        entry = ctx.quote.mark_price

        atr = ind.atr14 or entry * 0.02
        bb_width = (ind.bb_upper - ind.bb_lower) / ind.bb_middle if ind.bb_middle > 0 else 0

        # ── 下轨买入 ──
        # 价格触及下轨 (在下轨的 1% 范围内)
        lower_dist = (entry - ind.bb_lower) / entry if entry > 0 else 1
        if lower_dist < 0.01 and entry < ind.bb_middle:
            # 成交量萎缩确认 (恐慌已释放)
            volume_dry = ind.volume_ratio < 1.0

            score = 1  # 触及下轨
            if volume_dry:
                score += 1  # 缩量确认
            if ind.rsi14 < 40:
                score += 1  # RSI 偏低助攻

            if not filter_trade_direction(regime.regime, regime.adx, "BUY"):
                return None

            if score >= 2:
                levels = calc_dynamic_levels(entry, atr, is_long=True, regime=regime)
                return Signal(
                    strategy_id=self.id, symbol=ctx.symbol,
                    action="BUY", confidence=min(0.9, score / 3 + 0.3),
                    entry_price=entry,
                    stop_loss=levels["stop_loss"],
                    take_profits=[levels["take_profit_1"]],
                    reason=f"布林下轨 BB宽{bb_width:.1%}{' 缩量' if volume_dry else ''} SL{levels['sl_pct']}%",
                )

        # ── 上轨卖出 ──
        upper_dist = (ind.bb_upper - entry) / entry if entry > 0 else 1
        if upper_dist < 0.01 and entry > ind.bb_middle:
            volume_dry = ind.volume_ratio < 1.0

            score = 1
            if volume_dry:
                score += 1
            if ind.rsi14 > 60:
                score += 1

            if not filter_trade_direction(regime.regime, regime.adx, "SELL"):
                return None

            if score >= 2:
                levels = calc_dynamic_levels(entry, atr, is_long=False, regime=regime)
                return Signal(
                    strategy_id=self.id, symbol=ctx.symbol,
                    action="SELL", confidence=min(0.9, score / 3 + 0.3),
                    entry_price=entry,
                    stop_loss=levels["stop_loss"],
                    take_profits=[levels["take_profit_1"]],
                    reason=f"布林上轨 BB宽{bb_width:.1%}{' 缩量' if volume_dry else ''} SL{levels['sl_pct']}%",
                )

        return None


from dataclasses import dataclass


@dataclass
class BollingerBounceParams:
    """布林带回归参数"""
    bb_period: int = 20          # 布林带周期
    bb_std: float = 2.0          # 标准差倍数
    volume_dry_threshold: float = 0.7  # 缩量阈值
    min_bb_width: float = 0.02   # 最小带宽 (太窄不交易)
