"""K线多周期合成器 — 1H → 4H/1D/1W

使用 pandas resample，不持久化合成结果。
"""

import pandas as pd


class KlineAggregator:
    """从 1H K线合成高周期。"""

    RESAMPLE_MAP = {
        "4H": "4h",
        "1D": "1D",
        "1W": "1W",
    }

    def aggregate(self, rows_1h: list[dict], target_interval: str) -> list[dict]:
        """1H → 目标周期。

        Args:
            rows_1h: 1H K线列表（时间升序），dict 含 timestamp/open/high/low/close/volume/turnover
            target_interval: '4H' / '1D' / '1W'

        Returns:
            合成后的 K线列表（时间升序）
        """
        if not rows_1h:
            return []

        rule = self.RESAMPLE_MAP.get(target_interval)
        if rule is None:
            raise ValueError(f"Unsupported interval: {target_interval} (expect 4H/1D/1W)")

        df = pd.DataFrame(rows_1h)
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("datetime", inplace=True)

        resampled = df.resample(rule).agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
            "turnover": "sum",
        }).dropna()

        result = []
        for ts, row in resampled.iterrows():
            result.append({
                "timestamp": int(ts.timestamp() * 1000),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
                "turnover": float(row["turnover"]),
            })
        return result
