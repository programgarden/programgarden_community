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
    """Provide a lightweight programgarden_finance stub when dependencies are absent."""
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
        def get_instance(cls) -> _DummyLS:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

        async def async_ensure_token(self) -> bool:  # pragma: no cover - networkless stub
            return True

        async def async_login(self, *_args, **_kwargs) -> bool:  # pragma: no cover
            return True

        def overseas_futureoption(self) -> None:  # pragma: no cover
            raise AssertionError("LS data fetch should remain stubbed in tests")

    class _DummyO3108InBlock:  # pragma: no cover - compatibility shim
        def __init__(self, **_kwargs) -> None:
            self.payload = _kwargs

    module.LS = _DummyLS
    module.o3108 = types.SimpleNamespace(O3108InBlock=_DummyO3108InBlock)
    sys.modules["programgarden_finance"] = module


_ensure_programgarden_finance_stub()

from programgarden_community.overseas_futureoption.strategy_conditions.macd_momentum_shift import (  # noqa: E402
    FuturesMACDShift,
)


SymbolInfo = Dict[str, str]


def _make_bullish_reversal_candles() -> List[Dict[str, float]]:
    base = datetime(2022, 1, 1)
    price = 100.0
    candles: List[Dict[str, float]] = []
    for idx in range(163):
        if idx < 160:
            price -= 0.7
        else:
            price += 5.0
        candles.append(
            {
                "date": (base + timedelta(days=idx)).strftime("%Y%m%d"),
                "open": price,
                "high": price + 0.5,
                "low": price - 0.5,
                "close": price,
                "volume": 10_000 + idx,
            }
        )
    return candles


def test_macd_shift_detects_recent_bullish_crossover() -> None:
    candles = _make_bullish_reversal_candles()
    strategy = FuturesMACDShift()

    async def _fake_load_candles(self) -> List[Dict[str, float]]:
        return candles

    strategy._load_candles = types.MethodType(_fake_load_candles, strategy)
    strategy._set_symbol({"symbol": "ESZ25", "exchcd": "CME"})

    response = asyncio.run(strategy.execute())

    assert response["condition_id"] == FuturesMACDShift.id
    assert response["success"] is True
    assert response["position_side"] == "long"
    assert response["data"]["last_crossover"] == "bullish"
    assert len(response["data"]["signals"]) == len(candles)
    assert response["data"]["recent_crossovers"][-1]["crossover"] == "bullish"
    assert response["symbol"] == "ESZ25"
    assert response["exchcd"] == "CME"


def test_macd_shift_survives_quiet_tail_after_crossover() -> None:
    candles = [
        {"date": "20250924", "open": 2592.4, "high": 2617.0, "low": 2587.4, "close": 2614.6, "volume": 42},
        {"date": "20250925", "open": 2616.0, "high": 2651.0, "low": 2613.0, "close": 2648.0, "volume": 93},
        {"date": "20250926", "open": 2635.2, "high": 2638.0, "low": 2584.8, "close": 2584.8, "volume": 64},
        {"date": "20250929", "open": 2585.6, "high": 2630.2, "low": 2585.6, "close": 2630.2, "volume": 35},
        {"date": "20250930", "open": 2629.0, "high": 2631.0, "low": 2625.0, "close": 2629.4, "volume": 16},
        {"date": "20251001", "open": 2635.8, "high": 2635.8, "low": 2635.8, "close": 2635.8, "volume": 0},
        {"date": "20251002", "open": 2677.8, "high": 2677.8, "low": 2677.8, "close": 2677.8, "volume": 0},
        {"date": "20251003", "open": 2644.8, "high": 2644.8, "low": 2644.8, "close": 2644.8, "volume": 0},
        {"date": "20251006", "open": 2636.8, "high": 2639.6, "low": 2636.8, "close": 2639.6, "volume": 1},
        {"date": "20251007", "open": 2641.0, "high": 2641.0, "low": 2640.0, "close": 2640.0, "volume": 12},
        {"date": "20251008", "open": 2633.8, "high": 2636.2, "low": 2615.8, "close": 2633.4, "volume": 19},
        {"date": "20251009", "open": 2655.4, "high": 2685.6, "low": 2655.4, "close": 2672.4, "volume": 87},
        {"date": "20251010", "open": 2645.6, "high": 2652.0, "low": 2598.0, "close": 2605.4, "volume": 613},
        {"date": "20251013", "open": 2585.0, "high": 2585.0, "low": 2533.2, "close": 2577.8, "volume": 4639},
        {"date": "20251014", "open": 2582.0, "high": 2603.8, "low": 2520.0, "close": 2535.2, "volume": 6227},
        {"date": "20251015", "open": 2528.6, "high": 2593.0, "low": 2525.6, "close": 2587.2, "volume": 16271},
        {"date": "20251016", "open": 2595.4, "high": 2617.6, "low": 2571.4, "close": 2604.8, "volume": 9196},
        {"date": "20251017", "open": 2605.2, "high": 2608.2, "low": 2540.2, "close": 2547.4, "volume": 11626},
        {"date": "20251020", "open": 2548.2, "high": 2593.6, "low": 2542.8, "close": 2570.6, "volume": 8534},
        {"date": "20251021", "open": 2569.6, "high": 2638.0, "low": 2568.8, "close": 2620.2, "volume": 9145},
        {"date": "20251022", "open": 2620.6, "high": 2630.4, "low": 2598.0, "close": 2619.0, "volume": 8337},
        {"date": "20251023", "open": 2618.0, "high": 2629.8, "low": 2587.8, "close": 2621.6, "volume": 9417},
        {"date": "20251024", "open": 2624.0, "high": 2675.8, "low": 2620.4, "close": 2669.8, "volume": 8567},
        {"date": "20251027", "open": 2673.4, "high": 2719.8, "low": 2671.8, "close": 2715.4, "volume": 7101},
        {"date": "20251028", "open": 2717.0, "high": 2729.2, "low": 2693.2, "close": 2700.0, "volume": 7320},
        {"date": "20251029", "open": 2704.0, "high": 2735.0, "low": 2701.0, "close": 2734.0, "volume": 5510},
        {"date": "20251030", "open": 2733.4, "high": 2740.6, "low": 2698.6, "close": 2707.8, "volume": 9106},
        {"date": "20251031", "open": 2708.4, "high": 2714.4, "low": 2643.6, "close": 2646.2, "volume": 7948},
        {"date": "20251103", "open": 2645.6, "high": 2654.2, "low": 2615.8, "close": 2651.8, "volume": 6746},
        {"date": "20251104", "open": 2653.4, "high": 2663.0, "low": 2613.8, "close": 2626.2, "volume": 6164},
        {"date": "20251105", "open": 2625.8, "high": 2644.4, "low": 2589.8, "close": 2636.0, "volume": 8420},
        {"date": "20251106", "open": 2636.2, "high": 2686.6, "low": 2630.4, "close": 2682.0, "volume": 7965},
        {"date": "20251107", "open": 2684.2, "high": 2686.4, "low": 2657.6, "close": 2665.8, "volume": 5518},
        {"date": "20251110", "open": 2666.2, "high": 2674.4, "low": 2632.8, "close": 2669.4, "volume": 6001},
        {"date": "20251111", "open": 2668.4, "high": 2677.0, "low": 2630.8, "close": 2639.8, "volume": 6074},
        {"date": "20251112", "open": 2641.0, "high": 2664.4, "low": 2631.8, "close": 2660.6, "volume": 5638},
        {"date": "20251113", "open": 2657.6, "high": 2685.8, "low": 2642.0, "close": 2677.8, "volume": 5664},
        {"date": "20251114", "open": 2680.4, "high": 2682.2, "low": 2623.0, "close": 2629.4, "volume": 11222},
    ]

    strategy = FuturesMACDShift()

    async def _fake_load_candles(self) -> List[Dict[str, float]]:
        return candles

    strategy._load_candles = types.MethodType(_fake_load_candles, strategy)
    strategy._set_symbol({"symbol": "ESZ25", "exchcd": "CME"})

    response = asyncio.run(strategy.execute())

    assert response["success"] is True
    assert response["data"]["last_crossover"] == "bearish"
    assert response["position_side"] == "short"
    assert response["data"]["recent_crossovers"][-1]["date"] == "20251031"
