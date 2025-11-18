"""
해외선물 가격 추적 정정 전략
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from programgarden_core import (
    BaseModifyOrderOverseasFutures,
    BaseModifyOrderOverseasFuturesResponseType,
)
from programgarden_finance import LS, o3106


class FuturesTrackingPriceParams(BaseModel):
    """해외선물 정정 주문을 위한 파라미터."""

    appkey: Optional[str] = Field(
        None,
        title="LS증권 앱키",
        description="LS증권 API 호출에 필요한 앱키"
    )

    appsecretkey: Optional[str] = Field(
        None,
        title="LS증권 앱시크릿키",
        description="LS증권 API 호출에 필요한 앱시크릿키"
    )

    price_gap: float = Field(
        1.0,
        title="허용 가격차",
        description="주문가격과 현재 호가의 차이가 이 값 이상이면 정정 (단위: 틱/포인트)",
        gt=0,
        json_schema_extra={"example": 1.0}
    )

    enable: Literal["buy", "sell", "all"] = Field(
        "all",
        title="활성화 대상",
        description="정정을 적용할 방향 (buy: 매수, sell: 매도, all: 전체)"
    )

    min_remaining_balance_ratio: float = Field(
        0.1,
        title="최소 잔여 증거금 비율",
        description="정정 주문 실행 후 계좌에 남겨둘 주문가능 증거금의 최소 비율",
        ge=0,
        lt=1,
        json_schema_extra={"example": 0.1}
    )


class FuturesTrackingPriceModify(BaseModifyOrderOverseasFutures):
    """현재 호가를 기준으로 가격을 다시 맞춰 주는 해외선물 정정 전략."""

    id: str = "FuturesTrackingPriceModify"
    name: str = "해외선물 가격 추적 정정"
    description: str = "가격이 일정 범위를 벗어나면 1호가로 자동 정정합니다."
    securities: List[str] = ["ls-sec.co.kr"]
    order_types = ["modify_buy", "modify_sell"]
    parameter_schema: dict = FuturesTrackingPriceParams.model_json_schema()

    def __init__(
        self,
        appkey: Optional[str] = None,
        appsecretkey: Optional[str] = None,
        price_gap: float = 1.0,
        enable: Literal["buy", "sell", "all"] = "all",
        min_remaining_balance_ratio: float = 0.1,
    ) -> None:
        super().__init__()
        self.appkey = appkey
        self.appsecretkey = appsecretkey
        self.price_gap = price_gap
        self.enable = enable
        self.min_remaining_balance_ratio = min_remaining_balance_ratio

    async def execute(self) -> List[BaseModifyOrderOverseasFuturesResponseType]:
        ls = LS.get_instance()
        if not ls.is_logged_in():
            await ls.async_login(appkey=self.appkey, appsecretkey=self.appsecretkey)

        dps = (getattr(self, "dps", None) or [{"orderable_amount": 0.0}])[0]
        orderable_amount = float(dps.get("orderable_amount", 0.0) or 0.0)
        if orderable_amount <= 0:
            return []

        minimum_reserved = orderable_amount * self.min_remaining_balance_ratio
        available_headroom = max(orderable_amount - minimum_reserved, 0.0)
        if available_headroom <= 0:
            return []

        remaining_headroom = available_headroom
        futures_api = ls.overseas_futureoption()
        orders: List[BaseModifyOrderOverseasFuturesResponseType] = []

        for pending in self.non_traded_symbols:
            symbol = pending.get("IsuCodeVal")
            if not symbol:
                continue

            bns_tp_code = str(pending.get("BnsTpCode", "")).strip()
            if bns_tp_code not in ("1", "2"):
                continue

            if self.enable == "buy" and bns_tp_code != "2":
                continue
            if self.enable == "sell" and bns_tp_code != "1":
                continue

            ord_price = float(pending.get("OvrsDrvtOrdPrc", 0.0) or 0.0)
            if ord_price <= 0:
                continue

            quote = await futures_api.market().o3106(
                body=o3106.O3106InBlock(symbol=symbol)
            ).req_async()

            if not quote or quote.block is None:
                continue

            bid_price = float(getattr(quote.block, "bidho1", 0.0) or 0.0)
            ask_price = float(getattr(quote.block, "offerho1", 0.0) or 0.0)
            target_price = ask_price if bns_tp_code == "2" else bid_price

            if target_price <= 0:
                continue

            price_diff = abs(ord_price - target_price)
            if price_diff < self.price_gap:
                continue

            ord_qty = int(pending.get("OrdQty", 0) or 0)
            if ord_qty <= 0:
                continue

            contract_size = float(
                pending.get("contract_size")
                or pending.get("CtrtPrAmt")
                or pending.get("CtrtMult")
                or 1.0
            )
            if contract_size <= 0:
                contract_size = 1.0

            if bns_tp_code == "2":
                price_delta_for_margin = max(target_price - ord_price, 0.0)
            else:
                price_delta_for_margin = max(ord_price - target_price, 0.0)

            additional_margin = price_delta_for_margin * contract_size * ord_qty
            if additional_margin > remaining_headroom:
                continue

            remaining_headroom = max(remaining_headroom - additional_margin, 0.0)

            orders.append({
                "success": True,
                "ord_dt": pending.get("OrdDt", ""),
                "ovrs_futs_org_ord_no": pending.get("OvrsFutsOrgOrdNo") or pending.get("OvrsFutsOrdNo", ""),
                "isu_code_val": symbol,
                "futs_ord_tp_code": "2",
                "bns_tp_code": bns_tp_code,
                "futs_ord_ptn_code": "2",
                "ovrs_drvt_ord_prc": target_price,
                "cndi_ord_prc": 0.0,
                "ord_qty": ord_qty,
                "crcy_code_val": pending.get("CrcyCodeVal", ""),
                "ovrs_drvt_prdt_code": pending.get("OvrsDrvtPrdtCode", ""),
                "due_yymm": pending.get("DueYymm", ""),
                "exch_code": pending.get("ExchCode", ""),
            })

            if remaining_headroom <= 0:
                break

        return orders

    async def on_real_order_receive(self, order_type, response):
        return None


__all__ = [
    "FuturesTrackingPriceModify",
]
