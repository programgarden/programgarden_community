from __future__ import annotations

import asyncio
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional


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
            raise AssertionError("Real LS data access should be stubbed in tests")

    class _DummyO3108InBlock:  # pragma: no cover - compatibility shim
        def __init__(self, **_kwargs) -> None:
            self.payload = _kwargs

    module.LS = _DummyLS
    module.o3108 = types.SimpleNamespace(O3108InBlock=_DummyO3108InBlock)
    sys.modules["programgarden_finance"] = module


_ensure_programgarden_finance_stub()

from programgarden_community.overseas_futureoption.strategy_conditions.sma_ema_trend_cross import (  # noqa: E402
    FuturesSMAEMACross,
)


def _make_recent_bullish_cross_candles() -> List[Dict[str, float]]:
    base = datetime(2023, 1, 1)
    price = 150.0
    candles: List[Dict[str, float]] = []
    for idx in range(64):
        if idx < 60:
            price -= 1.0
        else:
            price += 5.0
        candles.append(
            {
                "date": (base + timedelta(days=idx)).strftime("%Y%m%d"),
                "open": price,
                "high": price + 1.0,
                "low": price - 1.0,
                "close": price,
                "volume": 8_000 + idx,
            }
        )
    return candles


def _make_recent_bearish_cross_candles() -> List[Dict[str, float]]:
    base = datetime(2023, 6, 1)
    price = 80.0
    candles: List[Dict[str, float]] = []
    for idx in range(80):
        if idx < 45:
            price += 1.2
        else:
            price -= 3.5
        candles.append(
            {
                "date": (base + timedelta(days=idx)).strftime("%Y%m%d"),
                "open": price,
                "high": price + 1.0,
                "low": price - 1.0,
                "close": price,
                "volume": 7_000 + idx,
            }
        )
    return candles


def test_sma_ema_cross_reports_recent_bullish_cross() -> None:
    candles = _make_recent_bullish_cross_candles()
    strategy = FuturesSMAEMACross(period_sma=10, period_ema=5)
    strategy._set_symbol({"symbol": "ESZ25", "exchcd": "CME"})

    async def _fake_load(self) -> List[Dict[str, float]]:
        return candles

    strategy._load_candles = types.MethodType(_fake_load, strategy)

    response = asyncio.run(strategy.execute())

    assert response["condition_id"] == FuturesSMAEMACross.id
    assert response["success"] is True
    assert response["position_side"] == "long"
    assert response["data"]["last_signal"] in {"bullish_cross", "trend_up"}
    assert response["data"]["signal_events"][-1]["signal"] in {"bullish_cross", "trend_up"}
    assert response["symbol"] == "ESZ25"
    assert response["exchcd"] == "CME"


def test_sma_ema_cross_handles_bearish_trend_shift() -> None:
    candles = _make_recent_bearish_cross_candles()
    strategy = FuturesSMAEMACross(period_sma=12, period_ema=5)
    strategy._set_symbol({"symbol": "NQZ25", "exchcd": "CME"})

    async def _fake_load(self) -> List[Dict[str, float]]:
        return candles

    strategy._load_candles = types.MethodType(_fake_load, strategy)

    response = asyncio.run(strategy.execute())

    assert response["condition_id"] == FuturesSMAEMACross.id
    assert response["success"] is True
    assert response["position_side"] == "short"
    assert response["data"]["last_signal"] in {"bearish_cross", "trend_down"}
    assert response["data"]["signal_events"][-1]["signal"] in {"bearish_cross", "trend_down"}
    assert response["symbol"] == "NQZ25"
    assert response["exchcd"] == "CME"
