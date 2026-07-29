"""Bitget 限速控制

TODO[TASK-003]: 如果内置限速不够用，可以在这里扩展更复杂的限速
当前限速实现在 market.py 的 _rate_limited_get 中 (基于时间间隔)
"""

# Bitget API 默认限速: 20 req/s
# 已在 market.py 中通过 _min_interval = 1.0/20 实现
