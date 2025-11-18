from __future__ import annotations

import asyncio
from pathlib import Path

from programgarden_community.overseas_stock.new_order_conditions.turtle_pyramid_new_order import (
    TurtlePyramidNewOrder,
    TurtleEntryState,
)


class DummyLS:
    def __init__(self, candles):
        self._candles = candles

    def is_logged_in(self):
        return True

    async def async_login(self, appkey, appsecretkey):
        return None

    def overseas_stock(self):
        return self

    def chart(self):
        return self

    def g3204(self, _):
        return self

    async def occurs_req_async(self):
        class Block:
            def __init__(self, c):
                self.date = c["date"]
                self.open = c["open"]
                self.high = c["high"]
                self.low = c["low"]
                self.close = c["close"]
                self.volume = c["volume"]

        class Resp:
            def __init__(self, candles):
                self.block1 = [Block(c) for c in candles]

        return [Resp(self._candles)]


def make_breakout_candles(n=30, *, strong_close: float = 1.2):
    """단일 돌파를 만드는 캔들 시퀀스.

    마지막 캔들의 close가 이전 high를 넘도록 strong_close 만큼 위로 조정한다.
    """
    candles = []
    price = 10.0
    for i in range(n - 1):
        candles.append(
            {
                "date": f"202401{i:02d}",
                "open": price,
                "high": price + 0.5,
                "low": price - 0.5,
                "close": price,
                "volume": 1000,
            }
        )
        price += 0.1
    candles.append(
        {
            "date": f"202401{n:02d}",
            "open": price + strong_close / 2,
            "high": price + strong_close + 0.3,
            "low": price,
            "close": price + strong_close,
            "volume": 1500,
        }
    )
    return candles


def test_first_entry_breakout(tmp_path, monkeypatch):
    candles = make_breakout_candles(30)

    # LS 싱글턴을 가짜로 교체
    from programgarden_finance import LS

    dummy = DummyLS(candles)
    monkeypatch.setattr(LS, "get_instance", classmethod(lambda cls: dummy))

    # 상태 DB를 테스트 전용 위치로 사용
    dbfile = tmp_path / "entry_state.db"
    state = TurtleEntryState(str(dbfile))

    cond = TurtlePyramidNewOrder(
        risk_per_trade=0.01,
        cash_usage_ratio=0.5,
        entry_period=20,
        atr_period=20,
        pyramid_trigger_atr=0.5,
        limit_buffer_atr=0.1,
        min_trade_size=1,
        max_units_per_symbol=4,
    )

    # 테스트용 속성 주입
    cond._state = state
    cond.system_id = "test_system_1"
    cond.available_symbols = [
        {"exchcd": "NASD", "symbol": "TEST"},
    ]
    cond.dps = [
        {"currency": "USD", "orderable_amount": 100_000.0},
    ]
    cond.non_traded_symbols = []
    cond.held_symbols = []

    orders = asyncio.run(cond.execute())

    assert orders, "돌파 조건에서 최소 1개 주문이 생성되어야 합니다"
    order = orders[0]
    assert order["success"] is True
    assert order["ord_qty"] >= 1
    assert order["ovrs_ord_prc"] > 0


def test_additional_pyramid_entry(tmp_path, monkeypatch):
    # 첫 번째 실행용: 보통 강도의 돌파
    candles_first = make_breakout_candles(30, strong_close=1.2)
    # 두 번째 실행용: 더 강한 돌파(가격을 크게 띄워서 추가 진입 조건을 강제로 만족)
    candles_second = make_breakout_candles(30, strong_close=5.0)

    # LS 싱글턴을 가짜로 교체
    from programgarden_finance import LS

    dummy = DummyLS(candles_first)
    monkeypatch.setattr(LS, "get_instance", classmethod(lambda cls: dummy))

    # 상태 DB를 테스트 전용 위치로 사용
    dbfile = tmp_path / "entry_state.db"
    state = TurtleEntryState(str(dbfile))

    cond = TurtlePyramidNewOrder(
        risk_per_trade=0.01,
        cash_usage_ratio=0.5,
        entry_period=20,
        atr_period=20,
        pyramid_trigger_atr=0.5,
        limit_buffer_atr=0.1,
        min_trade_size=1,
        max_units_per_symbol=4,
    )

    # 테스트용 속성 주입
    cond._state = state
    cond.system_id = "test_system_2"
    cond.available_symbols = [
        {"exchcd": "NASD", "symbol": "TEST"},
    ]
    cond.dps = [
        {"currency": "USD", "orderable_amount": 100_000.0},
    ]
    cond.non_traded_symbols = []
    cond.held_symbols = [
        {"ShtnIsuNo": "TEST", "AstkBalQty": 10},
    ]

    # 1차 진입: 상태에 첫 유닛 기록
    first_orders = asyncio.run(cond.execute())
    assert first_orders, "첫 번째 돌파 진입 주문이 생성되어야 합니다"

    # 상태에서 마지막 진입가와 유닛 수를 확인
    units_taken, last_price, last_qty = state.get_state("test_system_2", "TEST", "NASD")
    assert units_taken == 1
    assert last_price > 0

    # 두 번째 실행 전에 더 강한 돌파 캔들로 교체
    dummy._candles = candles_second

    # 두 번째 실행: 마지막 진입가 + (pyramid_trigger_atr * ATR) 이상이 되도록 강하게 상승시켰으므로
    # 추가 진입 주문이 실제로 발생해야 한다.
    second_orders = asyncio.run(cond.execute())

    assert second_orders, "추가 진입 조건에서 최소 1개 주문이 생성되어야 합니다"
    assert len(second_orders) >= 1
    second_order = second_orders[0]
    assert second_order["ovrs_ord_prc"] >= last_price
