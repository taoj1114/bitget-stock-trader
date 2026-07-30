"""动态止盈止损计算 — 基于 ATR + 波动率 + 市场状态

不做固定百分比，根据实际情况动态调整。
"""

from src.core.types import MarketRegime


def calc_dynamic_levels(
    entry: float,
    atr: float,
    is_long: bool,
    regime: MarketRegime | None = None,
    base_sl_mult: float = 2.0,
    base_tp_mult: float = 3.0,
) -> dict:
    """根据 ATR、波动率、市场状态计算止损止盈。

    核心逻辑:
        - 止损距离 = ATR × sl_multiplier
        - 止盈距离 = ATR × tp_multiplier
        - 高波动 → 扩大乘数（防止被震出）
        - 低波动 → 收紧乘数（防止利润回吐）
        - 震荡市 → 收紧止盈（快进快出）
        - 趋势市 → 放宽止盈（让利润跑）

    Args:
        entry: 入场价
        atr: ATR(14) 值
        is_long: True=多头, False=空头
        regime: 市场状态（可选）
        base_sl_mult: 基础止损乘数
        base_tp_mult: 基础止盈乘数

    Returns:
        dict: {stop_loss, take_profit_1, take_profit_2, tp1_pct, tp2_pct}
    """
    if atr <= 0:
        atr = entry * 0.02  # fallback 2%

    sl_mult = base_sl_mult
    tp_mult = base_tp_mult

    # ── 波动率调节 ──
    if regime:
        if regime.volatility == "high":
            sl_mult *= 1.3   # 扩大止损 30%
            tp_mult *= 1.3   # 目标也要更远
        elif regime.volatility == "low":
            sl_mult *= 0.8   # 收紧止损
            tp_mult *= 0.8   # 收益空间小

        # 市场状态
        if regime.regime in ("range_bound",):
            tp_mult *= 0.7   # 震荡市快进快出
        elif regime.regime in ("trend_up", "trend_down"):
            tp_mult *= 1.2   # 趋势市让利润跑

    stop_distance = atr * sl_mult
    tp1_distance = atr * tp_mult
    tp2_distance = atr * tp_mult * 1.5  # 第二目标更远

    if is_long:
        return {
            "stop_loss": entry - stop_distance,
            "take_profit_1": entry + tp1_distance,
            "take_profit_2": entry + tp2_distance,
            "sl_pct": round(stop_distance / entry * 100, 2),
            "tp1_pct": round(tp1_distance / entry * 100, 2),
            "tp2_pct": round(tp2_distance / entry * 100, 2),
        }
    else:
        return {
            "stop_loss": entry + stop_distance,
            "take_profit_1": entry - tp1_distance,
            "take_profit_2": entry - tp2_distance,
            "sl_pct": round(stop_distance / entry * 100, 2),
            "tp1_pct": round(tp1_distance / entry * 100, 2),
            "tp2_pct": round(tp2_distance / entry * 100, 2),
        }
