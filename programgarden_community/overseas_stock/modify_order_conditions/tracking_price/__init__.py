"""
가격 추적 정정매수
"""
from typing import List, Literal, Optional
from pydantic import BaseModel, Field
from programgarden_core import (
    BaseModifyOrderOverseasStock,
    BaseModifyOrderOverseasStockResponseType,
)
from programgarden_finance import LS, g3101, g3106


class PricingRangeCancellerParams(BaseModel):
    """
    가격 추적 정정매수 파라미터

    외부 사용자가 이 전략을 사용할 때 필요한 파라미터를 정의합니다.
    """

    appkey: Optional[str] = Field(
        None,
        title="LS증권 앱키",
        description="LS증권 API 인증을 위한 앱키"
    )

    appsecretkey: Optional[str] = Field(
        None,
        title="LS증권 앱시크릿키",
        description="LS증권 API 인증을 위한 앱시크릿키"
    )

    price_gap: float = Field(
        0.1,
        title="가격 차이 임계값",
        description="주문가격과 현재 호가의 차이가 이 값 이상이면 정정 (단위: 달러)",
        gt=0,
        json_schema_extra={"example": 0.1}
    )

    enable: Literal["buy", "sell", "all"] = Field(
        "all",
        title="활성화 모드",
        description="정정 대상 (buy: 매수만, sell: 매도만, all: 모두)"
    )


class PricingRangeCanceller(BaseModifyOrderOverseasStock):

    id: str = "PricingRangeCanceller"
    name: str = "가격 추적 정정매수"
    description: str = "가격 추적 정정매수이며 가격 차이에 따라 주문을 정정합니다."
    securities: List[str] = ["ls-sec.co.kr"]
    order_types = ["modify_buy", "modify_sell"]
    parameter_schema: dict = PricingRangeCancellerParams.model_json_schema()

    def __init__(
        self,
        appkey: Optional[str] = None,
        appsecretkey: Optional[str] = None,
        price_gap: float = 0.1,
        enable: Literal["buy", "sell", "all"] = "all",
    ):
        """
        가격 추적 정정매매 초기화

        Args:
            appkey (Optional[str]): LS증권 앱키
            appsecretkey (Optional[str]): LS증권 앱시크릿키
            price_gap (float): 가격 차이
            enable (Literal["buy", "sell", "all"]): 정정매수, 정정매도, 모두 활성화 여부
        """
        super().__init__()

        self.appkey = appkey
        self.appsecretkey = appsecretkey
        self.price_gap = price_gap
        self.enable = enable

    async def execute(self) -> List[BaseModifyOrderOverseasStockResponseType]:
        ls = LS.get_instance()
        if not ls.is_logged_in():
            await ls.async_login(
                appkey=self.appkey,
                appsecretkey=self.appsecretkey
            )

        orders: List[BaseModifyOrderOverseasStockResponseType] = []
        for non in self.non_traded_symbols:
            shtn_isu_no = non.get("ShtnIsuNo")
            fcurr_mkt_code = non.get("OrdMktCode")
            bns_tp_code = non.get("BnsTpCode")
            keysymbol = fcurr_mkt_code + shtn_isu_no

            # 기존 주문 가격 (가정: held에 ord_prc 필드가 있음)
            ord_prc = float(non.get("OvrsOrdPrc", 0))

            # 현재 가격 조회
            cur = await ls.overseas_stock().market().g3101(
                body=g3101.G3101InBlock(
                    keysymbol=keysymbol,
                    exchcd=fcurr_mkt_code,
                    symbol=shtn_isu_no
                )
            ).req_async()

            if cur.block.sellonly == "2":
                continue

            hoga = await ls.overseas_stock().market().g3106(
                body=g3106.G3106InBlock(
                    keysymbol=keysymbol,
                    exchcd=fcurr_mkt_code,
                    symbol=shtn_isu_no
                )
            ).req_async()

            bidho1 = round(float(hoga.block.bidho1), 3)
            offerho1 = round(float(hoga.block.offerho1), 3)

            # 가격 차이 계산
            price_diff = 0.0
            current_price = 0.0
            if bns_tp_code == "2" and self.enable in ["buy", "all"]:  # 매수 주문인 경우
                price_diff = abs(ord_prc - offerho1)
                current_price = offerho1
            elif bns_tp_code == "1" and self.enable in ["sell", "all"]:  # 매도 주문인 경우
                price_diff = abs(current_price - ord_prc)
                current_price = bidho1

            if price_diff >= self.price_gap:
                # 1호가로 정정 주문 생성
                order: BaseModifyOrderOverseasStockResponseType = {
                    "success": True,
                    "ord_ptn_code": "07",  # 정정
                    "org_ord_no": int(non.get("OrdNo")),
                    "ord_mkt_code": fcurr_mkt_code,
                    "shtn_isu_no": shtn_isu_no,
                    "ord_qty": non.get("OrdQty", 0),  # 기존 수량
                    "ovrs_ord_prc": current_price,  # 1호가 가격
                    "ordprc_ptn_code": "00",  # 지정가
                    "bns_tp_code": bns_tp_code,
                }

                orders.append(order)

        return orders

    async def on_real_order_receive(self, order_type, response):
        pass


__all__ = [
    "TrackingPriceModifyBuy"
]
