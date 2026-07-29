"""自定义异常"""


class DataSourceError(Exception):
    """数据源异常"""
    def __init__(self, source: str, message: str):
        self.source = source
        super().__init__(f"[{source}] {message}")


class DataSourceRateLimit(DataSourceError):
    """数据源限速"""
    def __init__(self, source: str, retry_after: float):
        self.retry_after = retry_after
        super().__init__(source, f"限速，请 {retry_after:.1f}s 后重试")


class DataSourceTimeout(DataSourceError):
    """数据源超时"""
    def __init__(self, source: str, timeout: float):
        super().__init__(source, f"请求超时 ({timeout}s)")


class SymbolNotFound(DataSourceError):
    """未找到交易对"""
    def __init__(self, source: str, symbol: str):
        super().__init__(source, f"未找到交易对: {symbol}")


class NewsSourceError(Exception):
    """新闻源异常"""
    def __init__(self, source: str, message: str):
        super().__init__(f"[新闻源:{source}] {message}")


class StrategyError(Exception):
    """策略异常"""
    ...


class TradingError(Exception):
    """交易执行异常"""
    ...


class SafetyBlocked(TradingError):
    """被风控拦截"""
    def __init__(self, rule: str, reason: str):
        self.rule = rule
        super().__init__(f"[风控:{rule}] {reason}")


class ConfigurationError(Exception):
    """配置错误"""
    ...


class NotImplementedForPhase(NotImplementedError):
    """该功能在后续 Phase 实现"""
    def __init__(self, phase: str, feature: str):
        super().__init__(f"[Phase {phase}] {feature} 尚未实现")
