"""特征计算管线 — 注册因子函数 → 逐品种计算 → 缓存结果"""

import json
import logging
import sqlite3
import time
from typing import Optional

import pandas as pd

from src.features.base import FeatureFunction
from src.features.price_features import PriceMomentum, PriceDeviation, BBandFeatures, ATRFeatures
from src.features.volume_features import VolumeFeatures
from src.features.rsi_features import RSITrajectory
from src.features.macd_features import MACDTrajectory
from src.features.sentiment_features import SentimentTrajectory
from src.features.fundamental_features import FundamentalMomentum
from src.core.sanitize import safe_json_dumps

logger = logging.getLogger(__name__)


# ── 全局因子注册表 ──────────────────────────────────

FEATURE_REGISTRY: list[FeatureFunction] = [
    PriceMomentum(),
    PriceDeviation(),
    BBandFeatures(),
    ATRFeatures(),
    VolumeFeatures(),
    RSITrajectory(),
    MACDTrajectory(),
    SentimentTrajectory(),
    FundamentalMomentum(),
]


class FeatureCache:
    """因子结果缓存到 SQLite，避免重复计算。"""

    def __init__(self, db_path: str = "data/features.db"):
        self._conn = sqlite3.connect(db_path)
        self._create_table()

    def _create_table(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS feature_cache (
                symbol       TEXT PRIMARY KEY,
                features     TEXT NOT NULL,
                kline_end_ts INTEGER NOT NULL,
                computed_at  INTEGER NOT NULL
            )
        """)
        self._conn.commit()

    def get(self, symbol: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT features FROM feature_cache WHERE symbol=?", (symbol,)
        ).fetchone()
        if row:
            return json.loads(row[0])
        return None

    def upsert(self, symbol: str, factors: dict) -> None:
        with self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO feature_cache
                   (symbol, features, kline_end_ts, computed_at)
                   VALUES (?, ?, ?, ?)""",
                (
                    symbol,
                    safe_json_dumps(factors),
                    factors.get("_kline_end_ts", 0),
                    factors.get("_computed_at", int(time.time())),
                ),
            )

    def close(self):
        self._conn.close()


class FeaturePipeline:
    """统筹所有因子计算 + 缓存管理。

    支持增量更新：对比最新K线时间戳，一致则跳过重算。
    """

    def __init__(self, kline_store, fund_store=None, news_store=None, cache_db: str = "data/features.db"):
        self._features = FEATURE_REGISTRY
        self._kline_store = kline_store
        self._fund_store = fund_store
        self._news_store = news_store
        self._cache = FeatureCache(cache_db)

    def compute_one(self, symbol: str) -> dict:
        """单个品种增量计算。缓存命中则直接返回。"""
        cached = self._cache.get(symbol)
        latest_ts = self._kline_store.get_latest_ts(symbol)
        if cached and cached.get("_kline_end_ts") == latest_ts and latest_ts > 0:
            return cached
        return self._compute_one(symbol)

    def compute_all(self, symbols: list[str]) -> dict[str, dict]:
        """全量计算所有品种。"""
        results = {}
        for symbol in symbols:
            results[symbol] = self._compute_one(symbol)
            self._cache.upsert(symbol, results[symbol])
        return results

    def _compute_one(self, symbol: str) -> dict:
        """实际执行单个品种的所有因子计算。"""
        # 1. 加载 1H K线 → DataFrame
        rows = self._kline_store.get(symbol, "1H", limit=200)
        df = pd.DataFrame(rows) if rows else pd.DataFrame()

        # 2. 加载基本面历史
        fund_history = []
        if self._fund_store:
            fund_history = self._fund_store.get_history(symbol, limit=4)

        # 3. 加载情绪历史
        sentiment_history = []
        if self._news_store:
            sentiment_history = self._news_store.get_history(symbol, days=7)

        # 4. 逐个执行因子函数
        factors = {}
        context = {
            "fund_history": fund_history,
            "sentiment_history": sentiment_history,
            "symbol": symbol,
        }

        for feat in self._features:
            try:
                result = feat.compute(df, **context)
                factors.update(result)
            except Exception:
                logger.warning("Feature %s failed for %s", feat.name, symbol, exc_info=True)

        # 5. 元数据
        factors["_kline_end_ts"] = int(df["timestamp"].iloc[-1]) if len(df) > 0 else 0
        factors["_computed_at"] = int(time.time())
        return factors
