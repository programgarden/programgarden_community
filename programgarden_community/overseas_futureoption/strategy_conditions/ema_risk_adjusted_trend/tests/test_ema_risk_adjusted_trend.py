from __future__ import annotations

import asyncio
import math
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pytest


_PROJECT_ROOT = Path(__file__).resolve().parents[7]
_SRC_ROOT = _PROJECT_ROOT / "src"
if _SRC_ROOT.exists():
    for _subdir in _SRC_ROOT.iterdir():
        if _subdir.is_dir():
            resolved = str(_subdir.resolve())
            if resolved not in sys.path:
                sys.path.insert(0, resolved)


def _ensure_programgarden_finance_stub() -> None:
    if "programgarden_finance" in sys.modules:
        return
    try:
        __import__("programgarden_finance")
        return
    except ModuleNotFoundError:
        pass

    module = types.ModuleType("programgarden_finance")

    class _DummyTokenManager:
        def is_token_available(self) -> bool:
            return True

    class _DummyLS:
        _instance: Optional["_DummyLS"] = None

        def __init__(self) -> None:
            self.token_manager = _DummyTokenManager()

        @classmethod
        def get_instance(cls) -> "_DummyLS":
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

        async def async_ensure_token(self) -> bool:  # pragma: no cover - stub only
            return True

        async def async_login(self, *_args, **_kwargs) -> bool:  # pragma: no cover
            return True

        def overseas_futureoption(self) -> None:  # pragma: no cover
            raise AssertionError("Network calls should be stubbed in EMA strategy tests")

    class _DummyO3108InBlock:  # pragma: no cover - compatibility shim
        def __init__(self, **_kwargs) -> None:
            self.payload = _kwargs

    module.LS = _DummyLS
    module.o3108 = types.SimpleNamespace(O3108InBlock=_DummyO3108InBlock)
    sys.modules["programgarden_finance"] = module


_ensure_programgarden_finance_stub()

from programgarden_community.overseas_futureoption.strategy_conditions.ema_risk_adjusted_trend import (  # noqa: E402
    FuturesEMARiskAdjustedTrend,
)


def _make_trend_candles(direction: str, count: int = 240) -> List[Dict[str, float]]:
    base = datetime(2020, 1, 1)
    price = 150.0 if direction == "down" else 100.0
    increments = [0.002, 0.0035, 0.0045, 0.0055, 0.003]
    candles: List[Dict[str, float]] = []
    for idx in range(count):
        step = increments[idx % len(increments)]
        if direction == "down":
            price *= 1 - step
        elif direction == "flat":
            price *= 1 + ((-1) ** idx) * 0.0005
        else:
            price *= 1 + step
        candles.append(
            {
                "date": (base + timedelta(days=idx)).strftime("%Y%m%d"),
                "open": price * (1 - 0.001),
                "high": price * 1.002,
                "low": price * 0.998,
                "close": price,
                "volume": 10_000 + idx,
            }
        )
    return candles


def test_ema_trend_flags_long_signal_on_uptrend() -> None:
    candles = _make_trend_candles("up", 260)
    strategy = FuturesEMARiskAdjustedTrend(
        ema_window=12,
        volatility_window=30,
        signal_threshold=0.2,
        transaction_cost=0.01,
        history_limit=120,
    )
    strategy._set_symbol({"symbol": "NGZ5", "exchcd": "NYM"})

    async def _fake_load(self) -> List[Dict[str, float]]:
        return candles

    strategy._load_candles = types.MethodType(_fake_load, strategy)

    response = asyncio.run(strategy.execute())

    assert response["condition_id"] == FuturesEMARiskAdjustedTrend.id
    assert response["success"] is True
    assert response["position_side"] == "long"
    assert response["symbol"] == "NGZ5"
    assert response["exchcd"] == "NYM"
    assert len(response["data"]["trend_points"]) == strategy.history_limit

    latest_signal = response["data"]["latest_signal"]
    assert latest_signal is not None
    assert latest_signal >= strategy.signal_threshold

    eta = strategy._eta_from_window(strategy.ema_window)
    assert response["data"]["eta"] == pytest.approx(eta)
    assert response["data"]["net_risk_adjusted_score"] == pytest.approx(
        strategy._risk_adjusted_score_for_eta(eta)
    )
    assert response["data"]["recommended_window"] == strategy._window_from_eta(
        response["data"]["recommended_eta"]
    )


def test_ema_trend_recommends_short_on_downtrend() -> None:
    candles = _make_trend_candles("down", 250)
    strategy = FuturesEMARiskAdjustedTrend(
        ema_window=14,
        volatility_window=35,
        signal_threshold=0.15,
        transaction_cost=0.0,
        market_lambda=0.02,
        market_beta=0.1,
    )
    strategy._set_symbol({"symbol": "ESZ5", "exchcd": "CME"})

    async def _fake_load(self) -> List[Dict[str, float]]:
        return candles

    strategy._load_candles = types.MethodType(_fake_load, strategy)

    response = asyncio.run(strategy.execute())

    assert response["success"] is True
    assert response["position_side"] == "short"
    latest_signal = response["data"]["latest_signal"]
    assert latest_signal is not None
    assert latest_signal <= -strategy.signal_threshold

    expected_eta = strategy.market_lambda * math.sqrt(1 + (2 * strategy.market_beta**2) / strategy.market_lambda)
    assert response["data"]["recommended_eta"] == pytest.approx(expected_eta)
    assert response["data"]["annualized_risk_adjusted_score"] == pytest.approx(
        response["data"]["net_risk_adjusted_score"] * math.sqrt(255)
    )


def test_ema_trend_stays_flat_when_signal_absent() -> None:
    candles = _make_trend_candles("flat", 200)
    strategy = FuturesEMARiskAdjustedTrend(
        ema_window=10,
        volatility_window=25,
        signal_threshold=0.8,
        transaction_cost=0.02,
        history_limit=80,
    )
    strategy._set_symbol({"symbol": "CLX5", "exchcd": "NYM"})

    async def _fake_load(self) -> List[Dict[str, float]]:
        return candles

    strategy._load_candles = types.MethodType(_fake_load, strategy)

    response = asyncio.run(strategy.execute())

    assert response["success"] is False
    assert response["position_side"] == "flat"
    latest_signal = response["data"]["latest_signal"]
    assert latest_signal is not None
    assert abs(latest_signal) < strategy.signal_threshold
    tail_signals = [point["ema_signal"] for point in response["data"]["trend_points"][-10:]]
    assert max(abs(sig or 0.0) for sig in tail_signals) < strategy.signal_threshold
