"""FastAPI 依赖注入 — 全局单例初始化与获取"""

from typing import Optional

from fastapi import HTTPException

from src.config.loader import get_config
from src.trading.paper_executor import PaperExecutor
from src.trading.tracker import Tracker
from src.trading.safety import SafetySystem
from src.strategies.registry import StrategyRegistry
from src.strategies.trend_break import TrendBreakStrategy
from src.strategies.rsi_bounce import RsiBounceStrategy
from src.strategies.ai_composite import AICompositeStrategy
from src.signals.aggregator import SignalAggregator
from src.optimization.performance import PerformanceAnalyzer
from src.optimization.param_tuner import ParamTuner
from src.optimization.version import VersionManager

# ── 全局单例 ─────────────────────────────────

_executor: Optional[PaperExecutor] = None
_registry: Optional[StrategyRegistry] = None
_aggregator: Optional[SignalAggregator] = None
_performance: Optional[PerformanceAnalyzer] = None
_tuner: Optional[ParamTuner] = None
_version_mgr: Optional[VersionManager] = None


def init_all() -> None:
    """初始化所有全局单例。在 uvicorn 启动时调用。"""
    global _executor, _registry, _aggregator, _performance, _tuner, _version_mgr

    config = get_config()
    safety = SafetySystem(config.safety)

    _executor = PaperExecutor(initial_capital=10000, safety=safety)
    _registry = StrategyRegistry()
    _aggregator = SignalAggregator()
    _performance = PerformanceAnalyzer()
    _tuner = ParamTuner()
    _version_mgr = VersionManager()

    # 注册默认策略
    trend = TrendBreakStrategy()
    rsi = RsiBounceStrategy()
    ai = AICompositeStrategy(api_key=config.deepseek.get("api_key", ""))

    _registry.register(trend)
    _registry.register(rsi)
    _registry.register(ai)
    _registry.activate("trend_break")
    _registry.activate("rsi_bounce")
    _registry.activate("ai_composite")


def get_executor() -> PaperExecutor:
    if _executor is None:
        raise HTTPException(503, "Executor not initialized")
    return _executor


def get_registry() -> StrategyRegistry:
    if _registry is None:
        raise HTTPException(503, "Registry not initialized")
    return _registry


def get_aggregator() -> SignalAggregator:
    if _aggregator is None:
        raise HTTPException(503, "Aggregator not initialized")
    return _aggregator


def get_performance() -> PerformanceAnalyzer:
    if _performance is None:
        raise HTTPException(503, "Performance not initialized")
    return _performance


def get_tuner() -> ParamTuner:
    if _tuner is None:
        raise HTTPException(503, "Tuner not initialized")
    return _tuner


def get_version_mgr() -> VersionManager:
    if _version_mgr is None:
        raise HTTPException(503, "VersionManager not initialized")
    return _version_mgr
