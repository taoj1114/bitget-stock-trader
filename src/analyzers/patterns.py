"""K线形态识别引擎 — Price Action 核心

支持形态:
    - 锤子线 (Hammer) / 倒锤子 (Inverted Hammer)
    - 看涨吞没 (Bullish Engulfing) / 看跌吞没 (Bearish Engulfing)
    - 内嵌棒 (Inside Bar)
    - 流星线 (Shooting Star) / 十字星 (Doji)
    - 大阳线/大阴线 (Marubozu)
    - 启明星 (Morning Star) / 黄昏星 (Evening Star)

用法:
    patterns = detect_patterns(klines)
    if patterns.hammer and patterns.near_support:
        → Buy the Dip
"""

from dataclasses import dataclass, field
from typing import Optional

from src.core.types import Kline


@dataclass
class CandlestickPatterns:
    """单根或多根 K 线组合的形态识别结果"""
    # 单根形态
    hammer: bool = False              # 锤子线 (长下影, 小实体在顶部)
    inverted_hammer: bool = False     # 倒锤子 (长上影, 小实体在底部)
    shooting_star: bool = False       # 流星线 (长上影, 小实体在底部, 出现在上涨后)
    marubozu_bull: bool = False       # 大阳线 (光头光脚或近似)
    marubozu_bear: bool = False       # 大阴线
    doji: bool = False                # 十字星

    # 组合形态
    bullish_engulfing: bool = False   # 看涨吞没
    bearish_engulfing: bool = False   # 看跌吞没
    inside_bar: bool = False          # 内嵌棒 (当前K线被前一根完全包裹)
    morning_star: bool = False        # 启明星 (下跌→十字星→大阳)
    evening_star: bool = False        # 黄昏星 (上涨→十字星→大阴)

    # 辅助判断
    near_support: bool = False        # 价格接近近期支撑位
    near_resistance: bool = False     # 价格接近近期阻力位
    support_price: float = 0.0
    resistance_price: float = 0.0


def detect_patterns(klines: list[Kline]) -> CandlestickPatterns:
    """从 K 线序列检测所有形态。

    需要至少 3 根 K 线（组合形态需要更多）。
    """
    result = CandlestickPatterns()
    if len(klines) < 3:
        return result

    curr = klines[-1]
    prev = klines[-2]
    prev2 = klines[-3]

    oc = curr.open
    cc = curr.close
    ch = curr.high
    cl = curr.low
    po = prev.open
    pc = prev.close
    ph = prev.high
    pl = prev.low

    body = abs(cc - oc)
    upper_shadow = ch - max(cc, oc)
    lower_shadow = min(cc, oc) - cl
    total_range = ch - cl if ch > cl else 0.001

    prev_body = abs(pc - po)
    prev_total = ph - pl if ph > pl else 0.001

    # ── 锤子线: 长下影, 小实体, 下影≥2×实体, 上影很短 ──
    if total_range > 0 and body > 0:
        if lower_shadow >= body * 2 and upper_shadow <= body * 0.5:
            if cc > oc:  # 阳锤子更强
                result.hammer = True

    # ── 流星线: 长上影, 小实体, 上影≥2×实体 ──
    if total_range > 0 and body > 0:
        if upper_shadow >= body * 2 and lower_shadow <= body * 0.5:
            result.shooting_star = True

    # ── 倒锤子: 长上影在底部区域 ──
    if total_range > 0 and body > 0:
        if upper_shadow >= body * 2 and lower_shadow <= body * 0.3:
            result.inverted_hammer = True

    # ── 大阳线: 实体≥总范围×0.8, 收盘远高于开盘 ──
    if total_range > 0 and body >= total_range * 0.8 and cc > oc:
        result.marubozu_bull = True

    # ── 大阴线: 实体≥总范围×0.8, 收盘远低于开盘 ──
    if total_range > 0 and body >= total_range * 0.8 and cc < oc:
        result.marubozu_bear = True

    # ── 十字星: 实体≤总范围×0.1 ──
    if total_range > 0 and body <= total_range * 0.1:
        result.doji = True

    # ── 看涨吞没: 前阴后阳, 后实体完全包裹前实体 ──
    if pc < po and cc > oc:  # 前阴后阳
        if oc <= pc and cc >= po:  # 完全包裹
            result.bullish_engulfing = True

    # ── 看跌吞没: 前阳后阴, 后实体完全包裹前实体 ──
    if pc > po and cc < oc:
        if oc >= pc and cc <= po:
            result.bearish_engulfing = True

    # ── 内嵌棒: 当前K线完全在前一根K线范围内 ──
    if ch <= ph and cl >= pl:
        result.inside_bar = True

    # ── 启明星: 下跌→十字星/小实体→大阳 ──
    if len(klines) >= 3:
        prev2_body = abs(prev2.close - prev2.open)
        prev2_total = prev2.high - prev2.low if prev2.high > prev2.low else 0.001
        # 前2根: 阴线下跌
        if prev2.close < prev2.open:
            # 前1根: 十字星或小实体
            if prev_body <= prev_total * 0.3:
                # 当前: 大阳线
                if body >= total_range * 0.6 and cc > oc:
                    # 当前收盘 > 前2根中点
                    if cc > (prev2.open + prev2.close) / 2:
                        result.morning_star = True

    # ── 黄昏星: 上涨→十字星/小实体→大阴 ──
    if len(klines) >= 3:
        if prev2.close > prev2.open:  # 前2根阳线
            if prev_body <= prev_total * 0.3:  # 中间十字星
                if body >= total_range * 0.6 and cc < oc:  # 当前大阴
                    if cc < (prev2.open + prev2.close) / 2:
                        result.evening_star = True

    # ── 支撑/阻力检测 ──
    if len(klines) >= 20:
        recent_lows = [k.low for k in klines[-20:-1]]
        recent_highs = [k.high for k in klines[-20:-1]]
        support = min(recent_lows)
        resistance = max(recent_highs)
        result.support_price = support
        result.resistance_price = resistance

        # 在支撑 2% 范围内
        if cl <= support * 1.02:
            result.near_support = True
        # 在阻力 2% 范围内
        if ch >= resistance * 0.98:
            result.near_resistance = True

    return result


def detect_vcp(klines: list[Kline], window: int = 20) -> tuple[bool, float]:
    """检测 VCP (Volatility Contraction Pattern) — Mark Minervini 招牌形态。

    条件:
        1. 最近 N 根 K线振幅在缩小 (max_range 递减)
        2. 成交量在萎缩
        3. 价格在窄幅区间内

    Returns:
        (is_vcp, squeeze_ratio): 是否 VCP + 压缩程度 (0-1, 越小越紧)
    """
    if len(klines) < window:
        return False, 1.0

    recent = klines[-window:]

    # 分成 3 段比较振幅
    n = window // 3
    if n < 3:
        return False, 1.0

    seg1 = [k.high - k.low for k in recent[-n:]]     # 最近
    seg2 = [k.high - k.low for k in recent[-n*2:-n]]
    seg3 = [k.high - k.low for k in recent[-n*3:-n*2]]

    avg1 = sum(seg1) / n if n > 0 else 0
    avg2 = sum(seg2) / n if n > 0 else 0
    avg3 = sum(seg3) / n if n > 0 else 0

    if avg3 == 0:
        return False, 1.0

    # 振幅递减检测
    range_shrinking = (avg1 < avg2 < avg3) or (avg1 < avg2 and avg2 <= avg3 * 0.9)

    # 成交量萎缩检测
    vol1 = sum(k.volume for k in recent[-n:])
    vol2 = sum(k.volume for k in recent[-n*2:-n])
    vol_shrinking = vol2 > 0 and vol1 < vol2 * 0.85

    squeeze_ratio = avg1 / avg3 if avg3 > 0 else 1.0

    return (range_shrinking and vol_shrinking), squeeze_ratio
