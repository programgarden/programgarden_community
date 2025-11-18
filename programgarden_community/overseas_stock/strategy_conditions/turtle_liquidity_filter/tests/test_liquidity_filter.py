from __future__ import annotations

from typing import List

import pytest

from programgarden_community.overseas_stock.strategy_conditions.turtle_liquidity_filter import (
    Candle,
    TurtleLiquidityFilter,
)


@pytest.mark.asyncio
async def test_liquidity_filter_marks_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = TurtleLiquidityFilter(lookback_days=5, min_turnover=1000, min_volume=10)
    strategy.symbol = {"symbol": "AAPL", "exchcd": "82"}

    async def _fake_load(self: TurtleLiquidityFilter) -> List[Candle]:
        candles: List[Candle] = []
        for idx in range(5):
            candles.append(
                Candle(
                    date=f"2024010{idx + 1}",
                    close=100 + idx,
                    volume=20 + idx,
                )
            )
        return candles

    monkeypatch.setattr(TurtleLiquidityFilter, "_load_candles", _fake_load)

    result = await strategy.execute()

    assert result["success"] is True
    assert result["data"]["average_turnover"] > 1000
    assert result["data"]["checks"]["turnover"] is True
    assert result["data"]["checks"]["volume"] is True


@pytest.mark.asyncio
async def test_liquidity_filter_marks_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = TurtleLiquidityFilter(lookback_days=3, min_turnover=10_000_000, min_volume=1_000_000)
    strategy.symbol = {"symbol": "TSLA", "exchcd": "82"}

    async def _fake_load(self: TurtleLiquidityFilter) -> List[Candle]:
        return [
            Candle(date="20240101", close=10.0, volume=100),
            Candle(date="20240102", close=9.5, volume=80),
            Candle(date="20240103", close=9.2, volume=75),
        ]

    monkeypatch.setattr(TurtleLiquidityFilter, "_load_candles", _fake_load)

    result = await strategy.execute()

    assert result["success"] is False
    assert result["data"]["checks"]["turnover"] is False
    assert result["data"]["checks"]["volume"] is False
