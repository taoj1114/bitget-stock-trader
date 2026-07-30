"""交易执行器 — 从 core.interfaces 导入规范接口

具体实现: paper_executor.py (纸盘), real_executor.py (实盘，未来)
"""

from src.core.interfaces import Executor

__all__ = ["Executor"]
