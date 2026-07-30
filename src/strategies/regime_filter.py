"""场景化不对称交易方向过滤

核心理念：
    强上升趋势 (ADX>30) → 只做多，不做空
    强下跌趋势 (ADX>30) → 只做空，不做多
    震荡市           → 双向对称
    弱趋势           → 提高门槛，不做逆势单

用法:
    from src.strategies.regime_filter import filter_trade_direction

    allowed = filter_trade_direction(regime, adx, action="BUY")
    if not allowed:
        return None  # 市场状态不允许这个方向
"""

from src.core.types import MarketRegime


def filter_trade_direction(regime: str, adx: float, action: str) -> bool:
    """判断当前市场状态是否允许该交易方向。

    Args:
        regime: 市场状态 (trend_up/trend_down/range_bound/weak_trend)
        adx: ADX 值
        action: 交易方向 (BUY/SELL)

    Returns:
        True=允许, False=不允许
    """
    is_buy = action == "BUY"
    is_strong = adx > 30

    # ── 强上升趋势 ──
    if regime == "trend_up" and is_strong:
        return is_buy  # 只允许做多，禁止做空

    # ── 强下跌趋势 ──
    if regime == "trend_down" and is_strong:
        return not is_buy  # 只允许做空，禁止做多

    # ── 震荡市 → 双向允许 ──
    if regime == "range_bound":
        return True

    # ── 弱趋势 → 提高门槛 ──
    # 允许顺势，禁止逆势
    if regime == "weak_trend":
        if is_buy:
            return adx > 15  # 至少有点上升动量才做多
        else:
            return adx > 15

    # 未知状态 → 保守允许
    return True


def get_min_score_for_regime(regime: str, adx: float, action: str,
                              base_min: int = 2) -> int:
    """场景化门槛：强逆势需要更高得分。

    Returns:
        最低所需得分 (0-3)
    """
    is_buy = action == "BUY"
    is_strong = adx > 30

    # 强趋势中逆势 → 需要最高确认
    if regime == "trend_down" and is_strong and is_buy:
        return 3  # 强下跌不做多（除非极强确认）
    if regime == "trend_up" and is_strong and not is_buy:
        return 3  # 强上升不做空

    # 强趋势中顺势 → 低门槛
    if regime == "trend_down" and is_strong and not is_buy:
        return 1  # 强下跌做空，1分即可
    if regime == "trend_up" and is_strong and is_buy:
        return 1

    return base_min
