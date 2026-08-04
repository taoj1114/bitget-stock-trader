"""交易执行器 — 从 core.interfaces 导入规范接口

具体实现: real_executor.py (实盘)
"""

from src.core.interfaces import Executor

__all__ = ["Executor"]
