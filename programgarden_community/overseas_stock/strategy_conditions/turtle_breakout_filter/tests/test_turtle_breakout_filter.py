from __future__ import annotations

from typing import List

import pytest

from programgarden_community.overseas_stock.strategy_conditions.turtle_breakout_filter import (
    Candle,
    TurtleBreakoutFilter,
)


def _build_candles(count: int, base_price: float, *, rising: bool = True) -> List[Candle]:
    candles: List[Candle] = []
    price = base_price
    for idx in range(count):
        step = idx * (1.0 if rising else -0.5)
        close = price + step
        candles.append(
            Candle(
                date=f"202401{idx:02d}",
                open=close - 0.5,
                high=close + 0.5,
                low=close - 1.0,
                close=close,
                volume=1_000_000,
            )
        )
    return candles


@pytest.mark.asyncio
async def test_breakout_filter_success(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = TurtleBreakoutFilter()
    strategy.symbol = {"symbol": "AAPL", "exchcd": "82"}

    async def _fake_load(self: TurtleBreakoutFilter) -> List[Candle]:
        return _build_candles(70, 100.0, rising=True)

    monkeypatch.setattr(TurtleBreakoutFilter, "_load_candles", _fake_load)

    result = await strategy.execute()

    assert result["success"] is True
    assert result["data"]["filters"]["liquidity"]["pass"] is True
    assert result["data"]["filters"]["trend"]["pass"] is True
    assert result["data"]["filters"]["exit"]["pass"] is True
    assert result["data"]["suggested_unit_qty"] >= 1


@pytest.mark.asyncio
async def test_breakout_filter_trend_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = TurtleBreakoutFilter()
    strategy.symbol = {"symbol": "MSFT", "exchcd": "82"}

    async def _fake_load(self: TurtleBreakoutFilter) -> List[Candle]:
        candles = _build_candles(70, 200.0, rising=True)
        # force last close below entry high to fail trend
        candles[-1] = Candle(
            date="20240310",
            open=180.0,
            high=185.0,
            low=175.0,
            close=180.0,
            volume=1_000_000,
        )
        return candles

    monkeypatch.setattr(TurtleBreakoutFilter, "_load_candles", _fake_load)

    result = await strategy.execute()

    assert result["success"] is False
    assert result["data"]["filters"]["trend"]["pass"] is False


@pytest.mark.asyncio
async def test_breakout_filter_exit_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = TurtleBreakoutFilter()
    strategy.symbol = {"symbol": "TSLA", "exchcd": "82"}

    async def _fake_load(self: TurtleBreakoutFilter) -> List[Candle]:
        candles = _build_candles(70, 150.0, rising=True)
        candles[-1] = Candle(
            date="20240311",
            open=80.0,
            high=85.0,
            low=75.0,
            close=80.0,
            volume=1_000_000,
        )
        return candles

    monkeypatch.setattr(TurtleBreakoutFilter, "_load_candles", _fake_load)

    result = await strategy.execute()

    assert result["success"] is False
    assert result["data"]["filters"]["exit"]["pass"] is False