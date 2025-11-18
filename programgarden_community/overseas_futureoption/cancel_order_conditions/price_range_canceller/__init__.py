"""
해외선물 가격 범위 취소 전략
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from programgarden_core import (
    BaseCancelOrderOverseasFutures,
    BaseCancelOrderOverseasFuturesResponseType,
)
from programgarden_finance import LS, o3106


class FuturesPriceRangeCancellerParams(BaseModel):
    """해외선물 미체결 주문을 자동으로 취소하기 위한 파라미터 정의."""

    appkey: Optional[str] = Field(
        None,
        title="LS증권 앱키",
        description="LS증권 API 인증에 사용하는 앱키"
    )

    appsecretkey: Optional[str] = Field(
        None,
        title="LS증권 앱시크릿키",
        description="LS증권 API 인증에 사용하는 앱시크릿키"
    )

    price_gap: float = Field(
        1.0,
        title="허용 가격차",
        description="주문가격과 현재 호가의 차이가 이 값 이상이면 취소 (단위: 틱 또는 가격)",
        gt=0,
        json_schema_extra={"example": 1.0}
    )

    enable: Literal["buy", "sell", "all"] = Field(
        "all",
        title="활성화 대상",
        description="취소를 적용할 주문 방향 (buy: 매수만, sell: 매도만, all: 전체)"
    )


class FuturesPriceRangeCanceller(BaseCancelOrderOverseasFutures):
    """호가가 일정 범위를 벗어나면 미체결 주문을 취소하는 해외선물 전략."""

    id: str = "FuturesPriceRangeCanceller"
    name: str = "해외선물 가격범위 취소"
    description: str = "호가가 주문가에서 멀어지면 자동으로 취소하여 불리한 체결을 막습니다."
    securities: List[str] = ["ls-sec.co.kr"]
    order_types = ["cancel_buy", "cancel_sell"]
    parameter_schema: dict = FuturesPriceRangeCancellerParams.model_json_schema()

    def __init__(
        self,
        appkey: Optional[str] = None,
        appsecretkey: Optional[str] = None,
        price_gap: float = 1.0,
        enable: Literal["buy", "sell", "all"] = "all",
    ) -> None:
        super().__init__()

        self.appkey = appkey
        self.appsecretkey = appsecretkey
        self.price_gap = price_gap
        self.enable = enable

    async def execute(self) -> List[BaseCancelOrderOverseasFuturesResponseType]:
        ls = LS.get_instance()
        if not ls.is_logged_in():
            await ls.async_login(appkey=self.appkey, appsecretkey=self.appsecretkey)

        orders: List[BaseCancelOrderOverseasFuturesResponseType] = []
        futures_api = ls.overseas_futureoption()

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
            current_price = ask_price if bns_tp_code == "2" else bid_price

            if current_price <= 0:
                continue

            price_diff = abs(ord_price - current_price)
            if price_diff < self.price_gap:
                continue

            orders.append({
                "success": True,
                "ord_dt": pending.get("OrdDt", ""),
                "isu_code_val": symbol,
                "ovrs_futs_org_ord_no": pending.get("OvrsFutsOrgOrdNo") or pending.get("OvrsFutsOrdNo", ""),
                "futs_ord_tp_code": "3",
                "prdt_tp_code": pending.get("PrdtTpCode", ""),
                "exch_code": pending.get("ExchCode", ""),
            })

        return orders

    async def on_real_order_receive(self, order_type, response):
        """실시간 응답 훅. 현재는 별도 처리가 필요 없으므로 통과."""
        return None


__all__ = [
    "FuturesPriceRangeCanceller",
]
