from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from programgarden_core import (
    BaseModifyOrderOverseasStock,
    BaseModifyOrderOverseasStockResponseType,
)
from programgarden_finance import LS, g3106
from pydantic import BaseModel, Field


MODIFY_DB_FILENAME = "turtle_overseas_stock_modify.db"


class ModifyAttemptStore:
    def __init__(self, filename: Optional[str] = None) -> None:
        if filename:
            path = Path(filename)
            if not path.is_absolute():
                path = Path.cwd() / path
        else:
            path = Path.cwd() / MODIFY_DB_FILENAME
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure()

    def _ensure(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS turtle_modify_attempts (
                    ord_no TEXT PRIMARY KEY,
                    attempts INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def get_attempts(self, ord_no: str) -> int:
        with sqlite3.connect(self.path) as conn:
            cur = conn.execute("SELECT attempts FROM turtle_modify_attempts WHERE ord_no=?", (ord_no,))
            row = cur.fetchone()
        return int(row[0]) if row else 0

    def bump(self, ord_no: str) -> None:
        attempts = self.get_attempts(ord_no) + 1
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                INSERT INTO turtle_modify_attempts(ord_no, attempts, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(ord_no)
                DO UPDATE SET attempts=excluded.attempts, updated_at=excluded.updated_at
                """,
                (ord_no, attempts, datetime.now(timezone.utc).isoformat()),
            )

    def clear(self, ord_no: str) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute("DELETE FROM turtle_modify_attempts WHERE ord_no=?", (ord_no,))


class TurtleAdaptiveModifyParams(BaseModel):
    appkey: Optional[str] = Field(None, title="LS증권 앱키")
    appsecretkey: Optional[str] = Field(None, title="LS증권 앱시크릿")
    price_gap: float = Field(
        0.2,
        title="가격 차이",
        description="현재 호가와 주문가격 차이가 이 값을 넘으면 정정",
        gt=0,
    )
    tick_size: float = Field(
        0.05,
        title="틱 사이즈",
        description="정정 시 이동할 최소 금액",
        gt=0,
    )
    timeout_seconds: int = Field(
        300,
        title="시간 제한",
        description="이 시간 이상 체결되지 않으면 정정",
        ge=30,
    )
    max_modify: int = Field(
        3,
        title="최대 정정 횟수",
        description="주문당 정정 허용 횟수",
        ge=1,
        le=5,
    )
    limit_padding: float = Field(
        0.0,
        title="호가 여유",
        description="1호가보다 조금 더 붙이고 싶다면 설정",
        ge=0.0,
        le=0.2,
    )


class TurtleAdaptiveModify(BaseModifyOrderOverseasStock):
    id: str = "TurtleAdaptiveModify"
    name: str = "터틀 정정 어시스턴트"
    description: str = "터틀 전략의 5분/3회 정정 원칙을 자동으로 지켜줍니다."
    securities = ["ls-sec.co.kr"]
    order_types = ["modify_buy", "modify_sell"]
    parameter_schema: Dict[str, object] = TurtleAdaptiveModifyParams.model_json_schema()

    def __init__(
        self,
        *,
        appkey: Optional[str] = None,
        appsecretkey: Optional[str] = None,
        price_gap: float = 0.2,
        tick_size: float = 0.05,
        timeout_seconds: int = 300,
        max_modify: int = 3,
        limit_padding: float = 0.0,
    ) -> None:
        super().__init__()
        self.appkey = appkey
        self.appsecretkey = appsecretkey
        self.price_gap = price_gap
        self.tick_size = tick_size
        self.timeout_seconds = timeout_seconds
        self.max_modify = max_modify
        self.limit_padding = limit_padding
        self._ls = LS.get_instance()
        self._store = ModifyAttemptStore()

    async def execute(self) -> List[BaseModifyOrderOverseasStockResponseType]:
        await self._ensure_login()
        results: List[BaseModifyOrderOverseasStockResponseType] = []
        for order in self.non_traded_symbols or []:
            ord_no = str(order.get("OrdNo"))
            if not ord_no:
                continue
            attempts = self._store.get_attempts(ord_no)
            if attempts >= self.max_modify:
                continue
            seconds_open = self._seconds_since(order.get("OrdTime", ""))
            shtn_isu_no = order.get("ShtnIsuNo")
            exchcd = order.get("OrdMktCode")
            if not shtn_isu_no or not exchcd:
                continue
            price_data = await self._fetch_quotes(exchcd, shtn_isu_no)
            if not price_data:
                continue
            order_price = float(order.get("OvrsOrdPrc", 0.0))
            price_diff = self._price_gap(order, price_data, order_price)
            should_modify = seconds_open >= self.timeout_seconds or price_diff >= self.price_gap
            if not should_modify:
                continue
            new_price = self._build_price(order, price_data, order_price, attempts)
            if new_price <= 0:
                continue
            payload: BaseModifyOrderOverseasStockResponseType = {
                "success": True,
                "ord_ptn_code": "07",
                "org_ord_no": int(order.get("OrdNo")),
                "ord_mkt_code": exchcd,
                "shtn_isu_no": shtn_isu_no,
                "ord_qty": order.get("UnercQty") or order.get("OrdQty", 0),
                "ovrs_ord_prc": round(new_price, 3),
                "ordprc_ptn_code": "00",
                "bns_tp_code": order.get("BnsTpCode", "2"),
            }
            results.append(payload)
            self._store.bump(ord_no)
        return results

    async def on_real_order_receive(self, order_type, response):
        if order_type not in {"filled_new_buy", "filled_new_sell", "cancel_complete_buy", "cancel_complete_sell"}:
            return
        ord_no = str(response.get("ord_no") or response.get("OrdNo") or "")
        if ord_no:
            self._store.clear(ord_no)

    async def _ensure_login(self) -> None:
        if not self._ls.is_logged_in():
            await self._ls.async_login(self.appkey, self.appsecretkey)

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

    def _price_gap(self, order, price_data, order_price: float) -> float:
        side = order.get("BnsTpCode", "2")
        if side == "2":  # buy
            return abs(order_price - price_data["ask"])
        return abs(price_data["bid"] - order_price)

    def _build_price(self, order, price_data, order_price: float, attempts: int) -> float:
        step = self.tick_size * (attempts + 1)
        side = order.get("BnsTpCode", "2")
        if side == "2":
            target = price_data["ask"] + self.limit_padding
            return min(order_price + step, target)
        target = price_data["bid"] - self.limit_padding
        return max(order_price - step, target)


__all__ = ["TurtleAdaptiveModify"]
