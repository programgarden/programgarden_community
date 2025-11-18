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
            raise AssertionError("Network calls should be stubbed in oscillator tests")

    class _DummyO3108InBlock:  # pragma: no cover - compatibility shim
        def __init__(self, **_kwargs) -> None:
            self.payload = _kwargs

    module.LS = _DummyLS
    module.o3108 = types.SimpleNamespace(O3108InBlock=_DummyO3108InBlock)
    sys.modules["programgarden_finance"] = module


_ensure_programgarden_finance_stub()

from programgarden_community.overseas_futureoption.strategy_conditions.rsi_stochastic_oscillator import (  # noqa: E402
    FuturesRSIStochastic,
)


def _make_oversold_candles() -> List[Dict[str, float]]:
    base = datetime(2024, 1, 1)
    price = 200.0
    candles: List[Dict[str, float]] = []
    for idx in range(100):
        price -= 2.0
        candles.append(
            {
                "date": (base + timedelta(days=idx)).strftime("%Y%m%d"),
                "open": price,
                "high": price + 1.0,
                "low": price - 1.0,
                "close": price,
                "volume": 5_000 + idx,
            }
        )
    return candles


def test_rsi_stochastic_flags_latest_oversold_signal() -> None:
    candles = _make_oversold_candles()
    strategy = FuturesRSIStochastic()
    strategy._set_symbol({"symbol": "ESZ25", "exchcd": "CME"})
    
    async def _fake_load(self) -> List[Dict[str, float]]:
        return candles

    strategy._load_candles = types.MethodType(_fake_load, strategy)

    response = asyncio.run(strategy.execute())

    assert response["condition_id"] == FuturesRSIStochastic.id
    assert response["success"] is True
    assert response["position_side"] == "long"
    assert response["data"]["last_signal"] == "oversold"
    assert response["data"]["signal_events"][-1]["signal"] == "oversold"
    assert response["symbol"] == "ESZ25"
    assert response["exchcd"] == "CME"
