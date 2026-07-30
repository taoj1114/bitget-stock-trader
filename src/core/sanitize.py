"""数据清理工具 — 存储/序列化前的格式校验

解决:
    - float('nan') → json.dumps 崩溃
    - float('inf') / '-inf' → 序列化失败
    - None → 根据上下文转为 0 或保留 NULL
    - 负数价格/数量 → 拒绝或归零
"""

import math
import json
from typing import Any


def clean_float(val: Any, default: float = 0.0, allow_negative: bool = True) -> float:
    """安全转 float：NaN/Inf/None → default。

    Args:
        val: 原始值
        default: 异常时的默认值
        allow_negative: False 时负数也转 default
    """
    try:
        v = float(val)
    except (TypeError, ValueError):
        return default

    if math.isnan(v) or math.isinf(v):
        return default
    if not allow_negative and v < 0:
        return default
    return v


def clean_dict(data: dict, defaults: dict[str, Any] | None = None) -> dict:
    """清洗 dict 中所有值：NaN→0, Inf→0, None→保留。

    不会修改原 dict，返回新 dict。
    """
    defaults = defaults or {}
    result = {}
    for key, val in data.items():
        if isinstance(val, float):
            if math.isnan(val) or math.isinf(val):
                result[key] = defaults.get(key, 0.0)
            else:
                result[key] = val
        elif val is None:
            result[key] = defaults.get(key)
        else:
            result[key] = val
    return result


def safe_json_dumps(obj: Any, **kwargs) -> str:
    """安全 JSON 序列化：NaN/Inf → null，不抛异常。

    用法: 替代 json.dumps()，用于 FeatureCache、State 持久化。
    """
    def _clean(o):
        if isinstance(o, float):
            if math.isnan(o) or math.isinf(o):
                return None
        if isinstance(o, dict):
            return {k: _clean(v) for k, v in o.items()}
        if isinstance(o, list):
            return [_clean(v) for v in o]
        return o

    cleaned = _clean(obj)
    return json.dumps(cleaned, **kwargs)


def validate_kline(kline: dict) -> dict | None:
    """校验单根 K线数据。

    拒绝: timestamp ≤ 0, open/high/low/close ≤ 0, high < low
    修复: NaN/Inf → 0.0
    """
    required = ["timestamp", "open", "high", "low", "close"]
    for key in required:
        if key not in kline:
            return None

    ts = clean_float(kline["timestamp"], allow_negative=False)
    if ts <= 0:
        return None

    o = clean_float(kline["open"], allow_negative=False)
    h = clean_float(kline["high"], allow_negative=False)
    l = clean_float(kline["low"], allow_negative=False)
    c = clean_float(kline["close"], allow_negative=False)

    if o <= 0 or h <= 0 or l <= 0 or c <= 0:
        return None
    if h < l:
        return None  # high must be >= low

    return {
        "timestamp": int(ts),
        "open": o, "high": h, "low": l, "close": c,
        "volume": clean_float(kline.get("volume"), allow_negative=False),
        "turnover": clean_float(kline.get("turnover"), allow_negative=False),
    }
