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

        def overseas_stock(self) -> "_DummyLS":  # pragma: no cover
            return self

        def chart(self) -> "_DummyLS":  # pragma: no cover
            return self

        def g3204(self, *_args, **_kwargs) -> None:  # pragma: no cover
            raise AssertionError("Network calls should be stubbed in oscillator tests")

    class _DummyG3204InBlock:  # pragma: no cover - compatibility shim
        def __init__(self, **_kwargs) -> None:
            self.payload = _kwargs

    module.LS = _DummyLS
    module.g3204 = types.SimpleNamespace(G3204InBlock=_DummyG3204InBlock)
    sys.modules["programgarden_finance"] = module


_ensure_programgarden_finance_stub()

from programgarden_community.overseas_stock.strategy_conditions.rsi_stochastic_oscillator import (  # noqa: E402
    StockRSIStochastic,
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


def test_stock_rsi_stochastic_flags_latest_oversold_signal() -> None:
    candles = _make_oversold_candles()
    strategy = StockRSIStochastic()
    strategy._set_symbol({"symbol": "AMZN", "exchcd": "82"})

    async def _fake_load(self) -> List[Dict[str, float]]:
        return candles

    strategy._load_candles = types.MethodType(_fake_load, strategy)

    response = asyncio.run(strategy.execute())

    assert response["condition_id"] == StockRSIStochastic.id
    assert response["product"] == "overseas_stock"
    assert response["success"] is True
    assert response["data"]["last_signal"] == "oversold"
    assert response["data"]["signal_events"][-1]["signal"] == "oversold"
    assert response["data"]["focus_signal"] == "both"
    assert response["symbol"] == "AMZN"
    assert response["exchcd"] == "82"


def test_stock_rsi_stochastic_respects_focus_signal_filter() -> None:
    candles = _make_oversold_candles()
    strategy = StockRSIStochastic(focus_signal="overbought")
    strategy._set_symbol({"symbol": "MSFT", "exchcd": "82"})

    async def _fake_load(self) -> List[Dict[str, float]]:
        return candles

    strategy._load_candles = types.MethodType(_fake_load, strategy)

    response = asyncio.run(strategy.execute())

    assert response["success"] is False
    assert response["data"]["last_signal"] == "neutral"
    assert response["data"]["signal_events"] == []
    assert response["data"]["focus_signal"] == "overbought"
