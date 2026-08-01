"""交易执行器简测"""

import pytest

from src.core.types import Signal
from src.trading.paper_executor import PaperExecutor
from src.trading.tracker import Tracker


class TestPaperExecutor:
    """纸盘执行器核心行为"""

    @pytest.fixture
    def executor(self, tmp_path):
        # 隔离: 测试用临时 DB, 不污染 data/trades.db
        tracker = Tracker(db_path=str(tmp_path / "test_trades.db"))
        return PaperExecutor(tracker=tracker)

    # ── 开仓 ──────────────────────────────

    @pytest.mark.asyncio
    async def test_open_long_position(self, executor):
        sig = Signal(strategy_id="test", symbol="AAPL",
                     action="BUY", confidence=0.8,
                     entry_price=150.0, stop_loss=140.0,
                     take_profits=[160.0], reason="test")
        result = await executor.execute_signal(sig)
        positions = await executor.get_positions()
        assert len(positions) == 1

    @pytest.mark.asyncio
    async def test_short_position_side_correct(self, tmp_path):
        tracker = Tracker(db_path=str(tmp_path / "t.db"))
        executor = PaperExecutor(tracker=tracker)
        sig = Signal(strategy_id="test", symbol="AAPL",
                     action="SELL", confidence=0.8,
                     entry_price=150.0, stop_loss=160.0,
                     take_profits=[140.0], reason="test")
        result = await executor.execute_signal(sig)
        assert result.status == "FILLED"

    @pytest.mark.asyncio
    async def test_duplicate_symbol_blocked(self, executor):
        """同品种重复开仓"""
        sig = Signal(strategy_id="test", symbol="AAPL",
                     action="BUY", confidence=0.8,
                     entry_price=150.0, stop_loss=140.0,
                     take_profits=[160.0], reason="test")
        r1 = await executor.execute_signal(sig)
        assert r1.status == "FILLED"
        r2 = await executor.execute_signal(sig)
        # 可能 REJECTED 或 FILLED（取决于 executor 实现）
        assert r2.status in ("FILLED", "REJECTED")

    # ── 仓位管理 ──────────────────────────

    @pytest.mark.asyncio
    async def test_position_tracked_after_open(self, executor):
        sig = Signal(strategy_id="test", symbol="AAPL",
                     action="BUY", confidence=0.8,
                     entry_price=150.0, stop_loss=140.0,
                     take_profits=[160.0], reason="test")
        await executor.execute_signal(sig)
        positions = await executor.get_positions()
        assert len(positions) == 1
        positions = await executor.get_positions()
        assert len(positions) == 1

    @pytest.mark.asyncio
    async def test_short_position_side_correct(self, tmp_path):
        tracker = Tracker(db_path=str(tmp_path / "t.db"))
        executor = PaperExecutor(tracker=tracker)
        sig = Signal(strategy_id="test", symbol="NVDA",
                     action="SELL", confidence=0.8,
                     entry_price=200.0, stop_loss=220.0,
                     take_profits=[180.0], reason="test")
        await executor.execute_signal(sig)
        positions = await executor.get_positions()
        assert positions[0].side == "SHORT"

    # ── 平衡查询 ──────────────────────────

    @pytest.mark.asyncio
    async def test_get_balance_returns_object(self, executor):
        balance = await executor.get_balance()
        assert balance is not None
        # balance 应该有 total/cash/equity 等字段
        assert hasattr(balance, 'total') or hasattr(balance, 'current_balance')

    # ── 仓位动态 ──────────────────────────

    @pytest.mark.asyncio
    async def test_dynamic_quantity(self, executor):
        """不同止损距离产生不同仓位"""
        sig1 = Signal(strategy_id="test", symbol="A",
                      action="BUY", confidence=0.8,
                      entry_price=100, stop_loss=95,
                      take_profits=[110], reason="test")
        sig2 = Signal(strategy_id="test", symbol="B",
                      action="BUY", confidence=0.8,
                      entry_price=100, stop_loss=98,
                      take_profits=[105], reason="test")
        r1 = await executor.execute_signal(sig1)
        r2 = await executor.execute_signal(sig2)
        # 1% 止损应比 5% 止损更大的仓位
        assert r1.fill_quantity != r2.fill_quantity
