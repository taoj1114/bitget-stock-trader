"""数据清理层测试"""

import math
import json
import pytest

from src.core.sanitize import (
    clean_float,
    validate_kline,
    safe_json_dumps,
    clean_dict,
)


class TestCleanFloat:
    def test_normal_float(self):
        assert clean_float(3.14) == 3.14
        assert clean_float(0) == 0.0

    def test_nan_returns_zero(self):
        assert clean_float(float('nan')) == 0.0

    def test_inf_returns_zero(self):
        assert clean_float(float('inf')) == 0.0
        assert clean_float(float('-inf')) == 0.0

    def test_none_returns_zero(self):
        assert clean_float(None) == 0.0

    def test_no_negative_when_disallowed(self):
        assert clean_float(-5, allow_negative=False) == 0.0

    def test_negative_when_allowed(self):
        assert clean_float(-5, allow_negative=True) == -5.0

    def test_string_conversion(self):
        assert clean_float("3.14") == 3.14
        assert clean_float("abc") == 0.0


class TestValidateKline:
    """validate_kline 返回 dict(有效) 或 None(无效)"""

    def test_valid_kline(self):
        k = {"timestamp": 1000, "open": 10, "high": 12,
             "low": 9, "close": 11, "volume": 100}
        assert validate_kline(k) is not None  # 返回 dict

    def test_missing_field(self):
        k = {"timestamp": 1000, "open": 10, "high": 12, "low": 9}
        assert validate_kline(k) is None

    def test_zero_timestamp(self):
        k = {"timestamp": 0, "open": 10, "high": 12,
             "low": 9, "close": 11, "volume": 100}
        assert validate_kline(k) is None

    def test_negative_price(self):
        k = {"timestamp": 1000, "open": -10, "high": 12,
             "low": 9, "close": 11, "volume": 100}
        assert validate_kline(k) is None

    def test_high_less_than_low(self):
        k = {"timestamp": 1000, "open": 10, "high": 9,
             "low": 12, "close": 11, "volume": 100}
        assert validate_kline(k) is None

    def test_nan_open(self):
        k = {"timestamp": 1000, "open": float('nan'), "high": 12,
             "low": 9, "close": 11, "volume": 100}
        assert validate_kline(k) is None


class TestSafeJsonDumps:
    def test_normal_dict(self):
        assert safe_json_dumps({"a": 1}) == '{"a": 1}'

    def test_nan_converted_to_null(self):
        result = safe_json_dumps({"value": float('nan')})
        assert "null" in result

    def test_inf_converted_to_null(self):
        result = safe_json_dumps({"value": float('inf')})
        assert "null" in result

    def test_nested_nan(self):
        data = {"a": [{"b": float('nan')}]}
        result = safe_json_dumps(data)
        parsed = json.loads(result)
        assert parsed["a"][0]["b"] is None

    def test_mixed_valid_and_invalid(self):
        data = {"ok": 1.5, "bad": float('nan'), "also_bad": float('-inf')}
        result = safe_json_dumps(data)
        parsed = json.loads(result)
        assert parsed["ok"] == 1.5
        assert parsed["bad"] is None

    def test_indent_works(self):
        result = safe_json_dumps({"a": 1}, indent=2)
        assert "\n" in result
