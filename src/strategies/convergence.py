"""策略趋同检测器 — 信号方向去重 + 权重提升"""

from collections import defaultdict

from src.core.types import Signal, EffectiveSignal


class ConvergenceDetector:
    """同方向信号权重提升，冲突方向丢弃低置信度。"""

    CONVERGENCE_BOOST = 0.15
    MAX_WEIGHT = 2.0

    def filter(self, raw_signals: list[Signal]) -> list[EffectiveSignal]:
        if not raw_signals:
            return []

        groups: dict[tuple[str, str], list[Signal]] = defaultdict(list)
        for sig in raw_signals:
            direction = "LONG" if sig.action in ("BUY", "STRONG_BUY") else "SHORT"
            groups[(sig.symbol, direction)].append(sig)

        # 冲突检测
        symbol_dirs: dict[str, set[str]] = defaultdict(set)
        for (symbol, d) in groups:
            symbol_dirs[symbol].add(d)
        conflicts = {s for s, dirs in symbol_dirs.items() if len(dirs) > 1}

        effective = []
        for (symbol, direction), signals in groups.items():
            if symbol in conflicts:
                # 保留最高置信度方向
                best_dir = max(
                    symbol_dirs[symbol],
                    key=lambda d: max(s.confidence for s in groups.get((symbol, d), [])),
                )
                if direction != best_dir:
                    continue

            n = len(signals)
            weight = min(1.0 + (n - 1) * self.CONVERGENCE_BOOST, self.MAX_WEIGHT)
            for sig in sorted(signals, key=lambda s: s.confidence, reverse=True):
                effective.append(EffectiveSignal(signal=sig, weight=weight))

        return effective
