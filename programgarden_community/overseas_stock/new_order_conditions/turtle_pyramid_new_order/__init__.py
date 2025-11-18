from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Dict, List, Optional, Tuple, TypedDict

from programgarden_core import (
    BaseNewOrderOverseasStock,
    BaseNewOrderOverseasStockResponseType,
)
import warnings
from programgarden_finance import LS, g3104, g3204
from pydantic import BaseModel, Field, model_validator

class Candle(TypedDict):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class BreakoutSnapshot:
    close: float
    entry_high: float
    atr: float
    can_long: bool


ENTRY_DB_FILENAME = "turtle_overseas_stock_entry.db"


class TurtleEntryState:
    def __init__(self, filename: Optional[str] = None) -> None:
        if filename:
            path = Path(filename)
            if not path.is_absolute():
                path = Path.cwd() / path
        else:
            path = Path.cwd() / ENTRY_DB_FILENAME
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS turtle_entries (
                    system_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    exchcd TEXT NOT NULL,
                    units INTEGER NOT NULL,
                    last_entry_price REAL NOT NULL,
                    last_qty INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (system_id, symbol, exchcd)
                )
                """
            )

    def get_state(self, system_id: str, symbol: str, exchcd: str) -> Tuple[int, float, int]:
        with sqlite3.connect(self.path) as conn:
            cur = conn.execute(
                "SELECT units, last_entry_price, last_qty FROM turtle_entries WHERE system_id=? AND symbol=? AND exchcd=?",
                (system_id, symbol, exchcd),
            )
            row = cur.fetchone()
        if not row:
            return (0, 0.0, 0)
        return (int(row[0]), float(row[1]), int(row[2]))

    def bump_units(self, system_id: str, symbol: str, exchcd: str, price: float, qty: int, *, replace: bool = False) -> None:
        units, _, _ = self.get_state(system_id, symbol, exchcd)
        if replace:
            new_units = max(units, 1)
        else:
            new_units = units + 1
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                INSERT INTO turtle_entries(system_id, symbol, exchcd, units, last_entry_price, last_qty, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(system_id, symbol, exchcd)
                DO UPDATE SET
                    units=excluded.units,
                    last_entry_price=excluded.last_entry_price,
                    last_qty=excluded.last_qty,
                    updated_at=excluded.updated_at
                """,
                (system_id, symbol, exchcd, new_units, price, qty, datetime.now(timezone.utc).isoformat()),
            )

    def reset(self, system_id: str, symbol: str, exchcd: str) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "DELETE FROM turtle_entries WHERE system_id=? AND symbol=? AND exchcd=?",
                (system_id, symbol, exchcd),
            )


class TurtlePyramidNewOrderParams(BaseModel):
    appkey: Optional[str] = Field(
        None,
        title="LS증권 앱키",
        description="LS증권 OpenAPI 앱키",
    )

    appsecretkey: Optional[str] = Field(
        None,
        title="LS증권 앱시크릿",
        description="LS증권 OpenAPI 앱시크릿",
    )

    risk_per_trade: float = Field(
        0.01,
        title="거래당 리스크",
        description="계좌 대비 1회 진입 위험 비율",
        gt=0,
        le=0.05,
    )


    cash_usage_ratio: float = Field(
        0.8,
        title="사용할 현금 비율",
        description="예수금 중 실제 주문에 사용할 퍼센트",
        gt=0.1,
        le=1.0,
    )

    entry_period: int = Field(
        20,
        title="진입 고점 기간",
        ge=10,
    )

    atr_period: int = Field(
        20,
        title="ATR 기간",
        ge=10,
    )

    pyramid_trigger_atr: float = Field(
        0.5,
        title="추가 진입 간격",
        description="마지막 진입가 대비 몇 ATR 상승 시 다음 유닛",
        gt=0.1,
    )

    limit_buffer_atr: float = Field(
        0.1,
        title="지정가 버퍼",
        description="시장가보다 얼마나 위/아래에서 주문할지",
        ge=0.0,
        le=0.5,
    )

    min_trade_size: int = Field(
        1,
        title="최소 거래 수량",
        ge=1,
    )

    max_units_per_symbol: int = Field(
        4,
        title="최대 유닛",
        ge=1,
    )

    min_price: float = Field(
        1.0,
        title="최소 주가",
        description="너무 싼 주식은 제외",
        gt=0.0,
    )

    auto_reset_on_flat: bool = Field(
        True,
        title="포지션 없으면 초기화",
        description="보유 수량이 없으면 DB 기록도 지움",
    )

    qrycnt: int = Field(
        260,
        title="조회 캔들 수",
        ge=120,
    )

    @model_validator(mode="after")
    def _validate_periods(self) -> "TurtlePyramidNewOrderParams":
        if self.entry_period < self.atr_period / 2:
            raise ValueError("entry_period는 atr_period의 절반보다 길어야 합니다")
        return self


class TurtlePyramidNewOrder(BaseNewOrderOverseasStock):
    id: str = "TurtlePyramidNewOrder"
    name: str = "터틀 피라미딩 신규 매수"
    description: str = "20일 돌파 + ATR 기반 유닛 사이징으로 해외주식을 나눠서 매수합니다."
    securities = ["ls-sec.co.kr"]
    order_types = ["new_buy"]
    parameter_schema: Dict[str, object] = TurtlePyramidNewOrderParams.model_json_schema()

    def __init__(
        self,
        *,
        appkey: Optional[str] = None,
        appsecretkey: Optional[str] = None,
        risk_per_trade: float = 0.01,
        cash_usage_ratio: float = 0.8,
        entry_period: int = 20,
        atr_period: int = 20,
        pyramid_trigger_atr: float = 0.5,
        limit_buffer_atr: float = 0.1,
        min_trade_size: int = 1,
        max_units_per_symbol: int = 4,
        min_price: float = 1.0,
        auto_reset_on_flat: bool = True,
        qrycnt: int = 260,
    ) -> None:
        super().__init__()
        self.appkey = appkey
        self.appsecretkey = appsecretkey
        self.risk_per_trade = risk_per_trade
        self.cash_usage_ratio = cash_usage_ratio
        self.entry_period = entry_period
        self.atr_period = atr_period
        self.pyramid_trigger_atr = pyramid_trigger_atr
        self.limit_buffer_atr = limit_buffer_atr
        self.min_trade_size = min_trade_size
        self.max_units_per_symbol = max_units_per_symbol
        self.min_price = min_price
        self.auto_reset_on_flat = auto_reset_on_flat
        self.qrycnt = qrycnt
        self._ls = LS.get_instance()
        self._state = TurtleEntryState()
        self._tick_size_cache: Dict[Tuple[str, str], float] = {}

    async def execute(self) -> List[BaseNewOrderOverseasStockResponseType]:
        await self._ensure_login()
        cash_pool = self._resolve_cash_pool()
        if cash_pool <= 0:
            return []
        system_id = getattr(self, "system_id", None) or "default"
        orders: List[BaseNewOrderOverseasStockResponseType] = []
        symbols = self.available_symbols or []
        for symbol in symbols:
            exchcd = symbol.get("exchcd")
            symbol_code = symbol.get("symbol")

            if not exchcd or not symbol_code:
                continue
            try:
                breakout = await self._build_snapshot(exchcd, symbol_code)
            except ValueError as exc:
                continue
            if breakout.close < self.min_price:
                continue
            if self.auto_reset_on_flat and not self._has_position(symbol_code):
                self._state.reset(system_id, symbol_code, exchcd)
            can_long = breakout.can_long
            if not can_long:
                continue
            if self._has_pending_order(symbol_code, exchcd):
                continue
            qty = self._calc_unit_qty(breakout.atr)
            if qty < self.min_trade_size:
                continue
            units_taken, last_price, _ = self._state.get_state(system_id, symbol_code, exchcd)
            if units_taken >= self.max_units_per_symbol:
                continue
            if units_taken == 0 and not breakout.can_long:
                continue
            if units_taken >= 1:
                if breakout.close < last_price + self.pyramid_trigger_atr * breakout.atr:
                    continue
            else:
                if breakout.close <= breakout.entry_high:
                    continue

            # g3204로 받은 차트 가격 범위를 기반으로 호가 단위를 추론하여 지정가를 맞춘다.
            raw_price = breakout.close + self.limit_buffer_atr * breakout.atr
            tick_size = await self._get_tick_size(exchcd, symbol_code)
            limit_price = self._normalize_price_tick(raw_price, tick_size)

            if limit_price <= 0:
                continue
            notional = limit_price * qty
            if notional > cash_pool:
                continue
            order: BaseNewOrderOverseasStockResponseType = {
                "success": True,
                "ord_ptn_code": "02",
                "ord_mkt_code": exchcd,
                "shtn_isu_no": symbol_code,
                "ord_qty": qty,
                "ovrs_ord_prc": limit_price,
                "ordprc_ptn_code": "00",
                "bns_tp_code": "2",
            }
            orders.append(order)
            cash_pool -= notional
            self._state.bump_units(system_id, symbol_code, exchcd, limit_price, qty)
            if cash_pool <= 0:
                break

        return orders

    async def on_real_order_receive(self, order_type, response):
        if order_type not in {"filled_new_buy", "cancel_complete_buy", "reject_buy"}:
            return
        symbol = response.get("shtn_isu_no") or response.get("ShtnIsuNo")
        exchcd = response.get("ord_mkt_code") or response.get("OrdMktCode")
        if not symbol or not exchcd:
            return
        system_id = getattr(self, "system_id", None) or "default"
        if order_type == "filled_new_buy":
            price = float(response.get("ovrs_ord_prc", response.get("exec_prc", 0.0)))
            qty = int(response.get("ord_qty", response.get("ExecQty", 1)))
            self._state.bump_units(system_id, symbol, exchcd, price, qty, replace=True)
        elif not self._has_position(symbol):
            self._state.reset(system_id, symbol, exchcd)

    async def _ensure_login(self) -> None:
        if not self._ls.is_logged_in():
            await self._ls.async_login(self.appkey, self.appsecretkey)

    def _resolve_cash_pool(self) -> float:
        if self.dps:
            usd_balance = sum(
                item.get("orderable_amount") or item.get("deposit", 0.0)
                for item in self.dps
                if item.get("currency") == "USD"
            )
            return usd_balance * self.cash_usage_ratio
        return 0.0

    def _calc_unit_qty(self, atr_value: float) -> int:
        if atr_value <= 0:
            return 0
        if not self.dps:
            return 0
        dollars_at_risk = sum(
            item.get("orderable_amount") or item.get("deposit", 0.0)
            for item in self.dps
            if item.get("currency") == "USD"
        ) * self.risk_per_trade
        qty = int(dollars_at_risk // max(atr_value, 1e-4))
        if qty < self.min_trade_size:
            qty = self.min_trade_size
        remainder = qty % self.min_trade_size
        if remainder:
            qty -= remainder
        return max(qty, self.min_trade_size)

    def _normalize_price_tick(self, price: float, tick_size: Optional[float]) -> float:
        """Normalize to the allowed tick size (falls back to heuristic if missing)."""
        if price <= 0:
            return 0.0
        tick = tick_size or (0.01 if price >= 1 else 0.001)
        if tick <= 0:
            tick = 0.01 if price >= 1 else 0.001
        price_dec = Decimal(str(price))
        tick_dec = Decimal(str(tick))
        steps = (price_dec / tick_dec).to_integral_value(rounding=ROUND_DOWN)
        normalized = (steps * tick_dec).quantize(tick_dec)
        return float(normalized)

    async def _get_tick_size(self, exchcd: str, symbol: str) -> Optional[float]:
        cache_key = (exchcd, symbol)
        if cache_key in self._tick_size_cache:
            return self._tick_size_cache[cache_key]
        try:
            req = (
                self._ls.overseas_stock()
                .market()
                .g3104(
                    g3104.G3104InBlock(
                        keysymbol=f"{exchcd}{symbol}",
                        exchcd=exchcd,
                        symbol=symbol,
                    )
                )
            )
            response = await req.req_async()
        except Exception as exc:
            return None
        block = getattr(response, "block", None)
        if not block:
            if response.error_msg:
                warnings.warn(f"g3104 response error for {symbol}: {response.error_msg}")

            return None
        tick_size = getattr(block, "untprc", None)
        if tick_size and tick_size > 0:
            tick_float = float(tick_size)
            self._tick_size_cache[cache_key] = tick_float
            return tick_float
        return None

    async def _build_snapshot(self, exchcd: str, symbol_code: str) -> BreakoutSnapshot:
        candles = await self._load_candles(exchcd, symbol_code)
        if len(candles) < 2:
            raise ValueError("skip due to missing candle history")
        min_needed = max(self.entry_period + 1, self.atr_period + 1)
        if len(candles) < min_needed:
            raise ValueError(
                f"need at least {min_needed} candles (have {len(candles)}) for breakout math"
            )
        entry_high = self._highest_high(candles, self.entry_period)
        atr_value = self._atr(candles, self.atr_period)
        close = candles[-1]["close"]
        can_long = close > entry_high and atr_value > 0
        return BreakoutSnapshot(
            close=close,
            entry_high=entry_high,
            atr=atr_value,
            can_long=can_long,
        )

    async def _load_candles(self, exchcd: str, symbol: str) -> List[Candle]:
        req = (
            self._ls.overseas_stock()
            .chart()
            .g3204(
                g3204.G3204InBlock(
                    sdate="",
                    edate="",
                    keysymbol=f"{exchcd}{symbol}",
                    exchcd=exchcd,
                    symbol=symbol,
                    gubun="2",
                    qrycnt=self.qrycnt,
                )
            )
        )
        responses = await req.occurs_req_async()
        candles: List[Candle] = []
        for response in responses:
            for block in response.block1:
                candles.append(
                    Candle(
                        date=block.date,
                        open=float(block.open),
                        high=float(block.high),
                        low=float(block.low),
                        close=float(block.close),
                        volume=int(getattr(block, "volume", 0)),
                    )
                )
        candles.sort(key=lambda item: item["date"])
        return candles

    def _highest_high(self, candles: List[Candle], period: int) -> float:
        if len(candles) <= period + 1:
            window = candles[:-1]
        else:
            window = candles[-(period + 1) : -1]
        if not window:
            return 0.0
        return max(candle["high"] for candle in window)

    def _atr(self, candles: List[Candle], period: int) -> float:
        if len(candles) < 2:
            return 0.0
        prev_close = candles[0]["close"]
        trs: List[float] = []
        for candle in candles[1:]:
            high_low = candle["high"] - candle["low"]
            high_close = abs(candle["high"] - prev_close)
            low_close = abs(candle["low"] - prev_close)
            trs.append(max(high_low, high_close, low_close))
            prev_close = candle["close"]
        if len(trs) < period:
            return 0.0
        recent = trs[-period:]
        return sum(recent) / period

    def _has_pending_order(self, symbol: str, exchcd: str) -> bool:
        for order in self.non_traded_symbols or []:
            if order.get("ShtnIsuNo") == symbol and order.get("OrdMktCode") == exchcd:
                if order.get("BnsTpCode") == "2":
                    return True
        return False

    def _has_position(self, symbol: str) -> bool:
        for held in self.held_symbols or []:
            if held.get("ShtnIsuNo") == symbol and held.get("AstkBalQty", 0) > 0:
                return True
        return False


__all__ = ["TurtlePyramidNewOrder"]
