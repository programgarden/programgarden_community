from types import SimpleNamespace

import pytest

from programgarden_community.overseas_futureoption.new_order_conditions.future_split_funds import (
    FuturesSplitFunds,
)


class DummyQuoteResponse:
    def __init__(self, price: float) -> None:
        self.block = SimpleNamespace(
            offerho1=price,
            price=price,
            bidho1=price,
        )


class DummyO3106Request:
    def __init__(self, price_map, symbol: str) -> None:
        self._price = price_map[symbol]

    async def req_async(self):
        return DummyQuoteResponse(self._price)


class DummyMarket:
    def __init__(self, price_map) -> None:
        self._price_map = price_map

    def o3106(self, body):
        return DummyO3106Request(self._price_map, body.symbol)


class DummyFuturesAPI:
    def __init__(self, price_map) -> None:
        self._price_map = price_map

    def market(self) -> DummyMarket:
        return DummyMarket(self._price_map)


class DummyLS:
    def __init__(self, price_map) -> None:
        self._api = DummyFuturesAPI(price_map)

    def is_logged_in(self) -> bool:
        return True

    async def async_login(self, **_):
        return None

    def overseas_futureoption(self) -> DummyFuturesAPI:
        return self._api


def _patch_ls(monkeypatch: pytest.MonkeyPatch, price_map) -> None:
    dummy_ls = DummyLS(price_map)
    monkeypatch.setattr(
        "programgarden_community.overseas_futureoption.new_order_conditions.future_split_funds.LS.get_instance",
        lambda: dummy_ls,
    )


@pytest.mark.asyncio
async def test_execute_allocates_budget_across_symbols(monkeypatch: pytest.MonkeyPatch):
    _patch_ls(monkeypatch, {"ESZ25": 450.0, "NQZ25": 200.0})

    strategy = FuturesSplitFunds(
        percent_balance=0.5,
        max_symbols=2,
        contracts_per_symbol=3,
        margin_buffer=0.1,
        min_remaining_balance_ratio=0.1,
        estimated_fee_per_contract=50.0,
        slippage_ratio=0.0,
    )
    strategy.available_symbols = [
        {
            "symbol": "ESZ25",
            "opening_margin": 8000,
            "exchange": "CME",
            "prdt_code": "ES",
        },
        {
            "symbol": "NQZ25",
            "opening_margin": 4000,
            "exchange": "CME",
            "prdt_code": "NQ",
        },
    ]
    strategy.dps = [{"orderable_amount": 100000}]

    orders = await strategy.execute()

    assert len(orders) == 2
    assert orders[0]["ord_qty"] == 2  # limited by per-symbol budget after margin buffer
    assert orders[1]["ord_qty"] == 3  # capped by contracts_per_symbol even though budget allows more

    total_margin_used = (
        2 * (8000 * 1.1 + 50.0)
        + 3 * (4000 * 1.1 + 50.0)
    )
    assert total_margin_used <= 100000 * 0.5


@pytest.mark.asyncio
async def test_execute_respects_remaining_balance(monkeypatch: pytest.MonkeyPatch):
    _patch_ls(monkeypatch, {"ESZ25": 300.0})

    strategy = FuturesSplitFunds(
        percent_balance=0.2,
        max_symbols=1,
        contracts_per_symbol=1,
        margin_buffer=0.0,
        min_remaining_balance_ratio=0.9,
        estimated_fee_per_contract=0.0,
        slippage_ratio=0.0,
    )
    strategy.available_symbols = [
        {
            "symbol": "ESZ25",
            "opening_margin": 60,
        }
    ]
    strategy.dps = [{"orderable_amount": 1000}]

    orders = await strategy.execute()

    assert orders == []  # reserved 잔액을 제외하면 예산이 부족하여 주문을 만들지 않는다.