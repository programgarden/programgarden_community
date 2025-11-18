from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from programgarden_core import (
    BaseCancelOrderOverseasStock,
    BaseCancelOrderOverseasStockResponseType,
)
from programgarden_finance import LS, g3106
from pydantic import BaseModel, Field


LOG_DB_FILENAME = "turtle_overseas_stock_cancel.db"


class CancelLogStore:
    def __init__(self, filename: Optional[str] = None) -> None:
        if filename:
            path = Path(filename)
            if not path.is_absolute():
                path = Path.cwd() / path
        else:
            path = Path.cwd() / LOG_DB_FILENAME
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure()

    def _ensure(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS turtle_cancel_logs (
                    ord_no TEXT,
                    reason TEXT,
                    exchcd TEXT,
                    symbol TEXT,
                    created_at TEXT
                )
                """
            )

    def log(self, ord_no: str, reason: str, exchcd: str, symbol: str) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "INSERT INTO turtle_cancel_logs VALUES(?, ?, ?, ?, ?)",
                (ord_no, reason, exchcd, symbol, datetime.now(timezone.utc).isoformat()),
            )


class TurtleSafetyCancelParams(BaseModel):
    appkey: Optional[str] = Field(None, title="LS증권 앱키")
    appsecretkey: Optional[str] = Field(None, title="LS증권 앱시크릿")
    timeout_seconds: int = Field(300, ge=60, title="시간 제한")
    panic_percent: float = Field(
        0.03,
        title="가격 급변",
        description="현재가가 주문가 대비 몇 % 이상 불리하면 취소",
        gt=0,
    )
    min_remaining: int = Field(
        1,
        title="최소 잔량",
        description="잔량이 이보다 작으면 바로 취소",
        ge=1,
    )


class TurtleSafetyCancel(BaseCancelOrderOverseasStock):
    id: str = "TurtleSafetyCancel"
    name: str = "터틀 취소 안전장치"
    description: str = "시간 초과/가격 급변/잔량 부족을 자동으로 취소합니다."
    securities = ["ls-sec.co.kr"]
    order_types = ["cancel_buy", "cancel_sell"]
    parameter_schema: Dict[str, object] = TurtleSafetyCancelParams.model_json_schema()

    def __init__(
        self,
        *,
        appkey: Optional[str] = None,
        appsecretkey: Optional[str] = None,
        timeout_seconds: int = 300,
        panic_percent: float = 0.03,
        min_remaining: int = 1,
    ) -> None:
        super().__init__()
        self.appkey = appkey
        self.appsecretkey = appsecretkey
        self.timeout_seconds = timeout_seconds
        self.panic_percent = panic_percent
        self.min_remaining = min_remaining
        self._ls = LS.get_instance()
        self._log = CancelLogStore()

    async def execute(self) -> List[BaseCancelOrderOverseasStockResponseType]:
        await self._ensure_login()
        cancels: List[BaseCancelOrderOverseasStockResponseType] = []
        for order in self.non_traded_symbols or []:
            ord_no = str(order.get("OrdNo"))
            if not ord_no:
                continue
            exchcd = order.get("OrdMktCode")
            symbol = order.get("ShtnIsuNo")
            if not exchcd or not symbol:
                continue
            reason = await self._should_cancel(order, exchcd, symbol)
            if not reason:
                continue
            payload: BaseCancelOrderOverseasStockResponseType = {
                "success": True,
                "ord_ptn_code": "08",
                "org_ord_no": int(order.get("OrdNo")),
                "ord_mkt_code": exchcd,
                "shtn_isu_no": symbol,
                "ord_qty": order.get("UnercQty") or order.get("OrdQty", 0),
                "ovrs_ord_prc": 0.0,
                "ordprc_ptn_code": "00",
                "bns_tp_code": order.get("BnsTpCode", "2"),
            }
            cancels.append(payload)
            self._log.log(ord_no, reason, exchcd, symbol)
        return cancels

    async def on_real_order_receive(self, order_type, response):
        # Nothing to update for now; logs are append-only
        return

    async def _ensure_login(self) -> None:
        if not self._ls.is_logged_in():
            await self._ls.async_login(self.appkey, self.appsecretkey)

    def _seconds_since(self, ord_time: str) -> int:
        if len(ord_time) < 6:
            return self.timeout_seconds
        hh = int(ord_time[0:2])
        mm = int(ord_time[2:4])
        ss = int(ord_time[4:6])
        ms = int(ord_time[6:9]) if len(ord_time) >= 9 else 0
        now = datetime.now(timezone.utc)
        order_dt = now.replace(hour=hh, minute=mm, second=ss, microsecond=ms * 1000)
        if order_dt > now:
            order_dt -= timedelta(days=1)
        return int((now - order_dt).total_seconds())

    def _panic_move(self, order_price: float, current_price: float, side: str) -> float:
        if order_price <= 0:
            return 0.0
        move = (current_price / order_price - 1.0)
        return move if side == "2" else -move

    async def _should_cancel(self, order, exchcd: str, symbol: str) -> Optional[str]:
        seconds_open = self._seconds_since(order.get("OrdTime", ""))
        if seconds_open >= self.timeout_seconds:
            return "timeout"
        remain_qty = int(order.get("UnercQty", 0))
        if remain_qty and remain_qty < self.min_remaining:
            return "tiny_rest"
        price = float(order.get("OvrsOrdPrc", 0.0))
        quotes = await self._fetch_quotes(exchcd, symbol)
        if not quotes:
            return None
        side = order.get("BnsTpCode", "2")
        current_price = quotes["ask"] if side == "2" else quotes["bid"]
        move = abs(self._panic_move(price, current_price, side))
        if move >= self.panic_percent:
            return "panic"
        return None

    async def _fetch_quotes(self, exchcd: str, symbol: str):
        req = self._ls.overseas_stock().market().g3106(
            body=g3106.G3106InBlock(
                keysymbol=f"{exchcd}{symbol}",
                exchcd=exchcd,
                symbol=symbol,
            )
        )
        response = await req.req_async()
        if not response or not response.block:
            return None
        return {
            "bid": float(response.block.bidho1),
            "ask": float(response.block.offerho1),
        }


__all__ = ["TurtleSafetyCancel"]
