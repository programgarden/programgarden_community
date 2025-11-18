from __future__ import annotations

from typing import List

import pytest

from programgarden_community.overseas_stock.strategy_conditions.turtle_volatility_filter import (
    Candle,
    TurtleVolatilityFilter,
)


def _build_candles(close_values: List[float]) -> List[Candle]:
    candles: List[Candle] = []
    price = close_values[0]
    for idx, close in enumerate(close_values):
        candles.append(
            Candle(
                date=f"202401{idx:02d}",
                open=price,
                high=max(price, close) + 0.5,
                low=min(price, close) - 0.5,
                close=close,
            )
        )
        price = close
    return candles


@pytest.mark.asyncio
async def test_volatility_filter_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = TurtleVolatilityFilter(atr_period=5, min_atr=0.5)
    strategy.symbol = {"symbol": "NVDA", "exchcd": "82"}

    async def _fake_load(self: TurtleVolatilityFilter) -> List[Candle]:
        return _build_candles([100, 101, 103, 105, 108, 110, 112, 115, 118, 120, 123])

    monkeypatch.setattr(TurtleVolatilityFilter, "_load_candles", _fake_load)

    result = await strategy.execute()

    assert result["success"] is True
    assert result["data"]["atr"] >= 0.5
    assert result["data"]["atr_percent_of_price"] > 0


@pytest.mark.asyncio
async def test_volatility_filter_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = TurtleVolatilityFilter(atr_period=5, min_atr=5.0)
    strategy.symbol = {"symbol": "KO", "exchcd": "82"}

    async def _fake_load(self: TurtleVolatilityFilter) -> List[Candle]:
        return _build_candles([50, 50.05, 50.1, 50.15, 50.2, 50.25, 50.3, 50.35, 50.4, 50.45, 50.5])

    monkeypatch.setattr(TurtleVolatilityFilter, "_load_candles", _fake_load)

    result = await strategy.execute()

    assert result["success"] is False
    assert result["data"]["atr"] < 5.0