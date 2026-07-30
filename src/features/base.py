"""因子函数基类 + 注册表"""

from abc import ABC, abstractmethod
from typing import Optional

import pandas as pd


class FeatureFunction(ABC):
    """可注册到 FeaturePipeline 的单个因子计算单元。

    子类需定义类属性 name 并实现 compute()。
    """

    name: str  # 子类必须定义: name = "price_momentum"

    @abstractmethod
    def compute(self, df: pd.DataFrame, **context) -> dict:
        """计算因子。

        Args:
            df: K线 DataFrame，含至少 open/high/low/close/volume，时间升序
            context: 额外上下文（如 fund_history, sentiment_history 等）

        Returns:
            {factor_name: value, ...} 扁平字典
        """
        ...

    @staticmethod
    def _sma(series: pd.Series, period: int) -> pd.Series:
        return series.rolling(period).mean()

    @staticmethod
    def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(period).mean()
        avg_loss = loss.rolling(period).mean()
        rs = avg_gain / avg_loss.replace(0, 1e-9)
        return 100.0 - (100.0 / (1.0 + rs))

    @staticmethod
    def _safe(val, default=0.0):
        """安全取值，NaN/None → default。"""
        if val is None:
            return default
        try:
            if pd.isna(val):
                return default
            return float(val)
        except (TypeError, ValueError):
            return default
