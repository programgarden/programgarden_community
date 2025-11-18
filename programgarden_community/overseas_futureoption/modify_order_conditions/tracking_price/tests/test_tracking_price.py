from types import SimpleNamespace

import pytest

from programgarden_community.overseas_futureoption.modify_order_conditions.tracking_price import (
    FuturesTrackingPriceModify,
)


class DummyQuoteResponse:
    def __init__(self, bid_price: float, ask_price: float) -> None:
        self.block = SimpleNamespace(
            bidho1=bid_price,
            offerho1=ask_price,
        )


class DummyO3106Request:
    def __init__(self, quote_map, symbol: str) -> None:
        self._quote_map = quote_map
        self._symbol = symbol

    async def req_async(self):
        bid, ask = self._quote_map[self._symbol]
        return DummyQuoteResponse(bid, ask)


class DummyMarket:
    def __init__(self, quote_map) -> None:
        self._quote_map = quote_map

    def o3106(self, body):
        return DummyO3106Request(self._quote_map, body.symbol)


class DummyFuturesAPI:
    def __init__(self, quote_map) -> None:
        self._quote_map = quote_map

    def market(self) -> DummyMarket:
        return DummyMarket(self._quote_map)


class DummyLS:
    def __init__(self, quote_map) -> None:
        self._api = DummyFuturesAPI(quote_map)

    def is_logged_in(self) -> bool:
        return True

    async def async_login(self, **_):
        return None

    def overseas_futureoption(self) -> DummyFuturesAPI:
        return self._api


def _patch_ls(monkeypatch: pytest.MonkeyPatch, quote_map) -> None:
    dummy_ls = DummyLS(quote_map)
    monkeypatch.setattr(
        "programgarden_community.overseas_futureoption.modify_order_conditions.tracking_price.LS.get_instance",
        lambda: dummy_ls,
    )


@pytest.mark.asyncio
async def test_execute_creates_modify_when_headroom_sufficient(monkeypatch: pytest.MonkeyPatch):
    _patch_ls(monkeypatch, {"ESZ25": (95.0, 105.0)})

    strategy = FuturesTrackingPriceModify(
        price_gap=1.0,
        enable="buy",
        min_remaining_balance_ratio=0.2,
    )
    strategy.dps = [{"orderable_amount": 1000}]
    strategy.non_traded_symbols = [
        {
            "IsuCodeVal": "ESZ25",
            "BnsTpCode": "2",
            "OvrsDrvtOrdPrc": 100.0,
            "OrdQty": 2,
            "CtrtPrAmt": 50.0,
            "OvrsFutsOrgOrdNo": "001",
            "OrdDt": "20250101",
        }
    ]

    orders = await strategy.execute()

    assert len(orders) == 1
    assert orders[0]["ovrs_drvt_ord_prc"] == pytest.approx(105.0)
    assert orders[0]["ord_qty"] == 2


@pytest.mark.asyncio
async def test_execute_skips_when_additional_margin_exceeds_headroom(monkeypatch: pytest.MonkeyPatch):
    _patch_ls(monkeypatch, {"ESZ25": (110.0, 120.0)})

    strategy = FuturesTrackingPriceModify(
        price_gap=1.0,
        enable="buy",
        min_remaining_balance_ratio=0.9,
    )
    strategy.dps = [{"orderable_amount": 1000}]
    strategy.non_traded_symbols = [
        {
            "IsuCodeVal": "ESZ25",
            "BnsTpCode": "2",
            "OvrsDrvtOrdPrc": 100.0,
            "OrdQty": 1,
            "CtrtPrAmt": 50.0,
            "OvrsFutsOrgOrdNo": "001",
            "OrdDt": "20250101",
        }
    ]

    orders = await strategy.execute()

    assert orders == []
