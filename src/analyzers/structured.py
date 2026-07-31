"""结构化分析层 — 预计算信号，供 AI 直接读取

不依赖 AI，纯数值计算：
    - 趋势判断 (多周期合并)
    - 关键信号 (金叉/死叉/背离/放量)
    - 风险评分
"""

from dataclasses import dataclass


@dataclass
class [...[truncated]