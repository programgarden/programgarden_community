"""
해외선물 균등 배분 매수 전략
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from programgarden_core import (
    BaseNewOrderOverseasFutures,
    BaseNewOrderOverseasFuturesResponseType,
)
from programgarden_finance import LS, o3106


class FuturesSplitFundsParams(BaseModel):
    """해외선물 신규 매수 전략 파라미터."""

    appkey: Optional[str] = Field(
        None,
        title="LS증권 앱키",
        description="LS증권 API 인증용 앱키"
    )

    appsecretkey: Optional[str] = Field(
        None,
        title="LS증권 앱시크릿키",
        description="LS증권 API 인증용 앱시크릿키"
    )

    percent_balance: float = Field(
        0.3,
        title="사용할 증거금 비율",
        description="주문가능 증거금 중 사용할 비율 (0.3 = 30%)",
        gt=0,
        le=1,
        json_schema_extra={"example": 0.3}
    )

    max_symbols: int = Field(
        3,
        title="최대 대상 종목 수",
        description="한 번에 매수할 월물/종목의 최대 개수",
        gt=0,
        json_schema_extra={"example": 3}
    )

    contracts_per_symbol: int = Field(
        1,
        title="종목당 최대 계약 수",
        description="각 종목에 배정할 최대 계약 수",
        gt=0,
        json_schema_extra={"example": 1}
    )

    margin_buffer: float = Field(
        0.15,
        title="증거금 안전 버퍼",
        description="계약당 요구 증거금에 추가로 적립할 버퍼 비율",
        ge=0,
        json_schema_extra={"example": 0.15}
    )

    min_remaining_balance_ratio: float = Field(
        0.1,
        title="최소 잔여 증거금 비율",
        description="전략 실행 후 남겨둘 주문가능 증거금의 최소 비율",
        ge=0,
        lt=1,
        json_schema_extra={"example": 0.1}
    )

    estimated_fee_per_contract: float = Field(
        0.0,
        title="계약당 추정 수수료",
        description="한 계약 체결 시 발생하는 수수료 및 거래소 비용 추정치",
        ge=0,
        json_schema_extra={"example": 2.5}
    )

    slippage_ratio: float = Field(
        0.001,
        title="슬리피지 보정 비율",
        description="주문을 넣은 뒤 체결가가 순간적으로 더 비싸질 수 있다는 점을 감안해 미리 매수가격을 올려 잡는 여유 비율입니다. 예를 들어 0.001이면 최신 호가보다 0.1% 높은 금액으로 필요한 증거금과 계약 수를 계산하여 예산 초과를 방지합니다.",
        ge=0,
        json_schema_extra={"example": 0.002}
    )


class FuturesSplitFunds(BaseNewOrderOverseasFutures):
    """여러 선물 월물에 증거금을 균등하게 배분하여 신규 매수를 실행합니다."""

    id: str = "FuturesSplitFunds"
    name: str = "해외선물 균등 배분 매수"
    description: str = "사용자가 정한 비율만큼 증거금을 나눠 여러 종목을 동시에 매수합니다."
    securities: List[str] = ["ls-sec.co.kr"]
    order_types = ["new_buy"]
    parameter_schema: dict = FuturesSplitFundsParams.model_json_schema()

    def __init__(
        self,
        appkey: Optional[str] = None,
        appsecretkey: Optional[str] = None,
        percent_balance: float = 0.3,
        max_symbols: int = 3,
        contracts_per_symbol: int = 1,
        margin_buffer: float = 0.15,
        min_remaining_balance_ratio: float = 0.1,
        estimated_fee_per_contract: float = 0.0,
        slippage_ratio: float = 0.001,
    ) -> None:
        super().__init__()

        self.appkey = appkey
        self.appsecretkey = appsecretkey
        self.percent_balance = percent_balance
        self.max_symbols = max_symbols
        self.contracts_per_symbol = contracts_per_symbol
        self.margin_buffer = margin_buffer
        self.min_remaining_balance_ratio = min_remaining_balance_ratio
        self.estimated_fee_per_contract = estimated_fee_per_contract
        self.slippage_ratio = slippage_ratio

    async def execute(self) -> List[BaseNewOrderOverseasFuturesResponseType]:
        ls = LS.get_instance()
        if not ls.is_logged_in():
            await ls.async_login(appkey=self.appkey, appsecretkey=self.appsecretkey)

        dps = (self.dps or [{"orderable_amount": 0.0}])[0]
        orderable_amount = float(dps.get("orderable_amount", 0.0) or 0.0)
        if orderable_amount <= 0:
            return []

        budget = orderable_amount * self.percent_balance
        if budget <= 0:
            return []

        minimum_reserved = orderable_amount * self.min_remaining_balance_ratio
        allowed_budget = max(budget - minimum_reserved, 0.0)
        if allowed_budget <= 0:
            return []

        remaining_budget = allowed_budget
        futures_api = ls.overseas_futureoption()

        orders: List[BaseNewOrderOverseasFuturesResponseType] = []
        for symbol_info in self.available_symbols:
            if len(orders) >= self.max_symbols:
                break

            if remaining_budget <= 0:
                break

            remaining_slots = max(self.max_symbols - len(orders), 1)
            per_symbol_budget = remaining_budget / remaining_slots

            symbol = symbol_info.get("symbol") or symbol_info.get("IsuCodeVal")
            if not symbol:
                continue

            quote = await futures_api.market().o3106(
                body=o3106.O3106InBlock(symbol=symbol)
            ).req_async()

            if not quote or quote.block is None:
                continue

            preferred_prices = [
                getattr(quote.block, "offerho1", 0.0),
                getattr(quote.block, "price", 0.0),
                getattr(quote.block, "bidho1", 0.0),
            ]
            price = float(next((p for p in preferred_prices if p and p > 0), 0.0))
            if price <= 0:
                continue

            price *= (1 + self.slippage_ratio)

            contract_size = float(symbol_info.get("contract_size") or symbol_info.get("CtrtPrAmt") or 1.0)
            estimated_cost = price * contract_size
            if estimated_cost <= 0:
                continue

            raw_margin = float(
                symbol_info.get("opening_margin")
                or symbol_info.get("maintenance_margin")
                or estimated_cost
            )
            margin_per_contract = raw_margin * (1 + self.margin_buffer)
            total_per_contract = margin_per_contract + self.estimated_fee_per_contract
            if total_per_contract <= 0:
                continue

            symbol_budget = min(per_symbol_budget, remaining_budget)
            max_affordable = int(symbol_budget // total_per_contract)
            qty = min(max_affordable, self.contracts_per_symbol)
            if qty < 1:
                continue

            orders.append({
                "success": True,
                "ord_dt": datetime.now().strftime("%Y%m%d"),
                "isu_code_val": symbol,
                "futs_ord_tp_code": "1",
                "bns_tp_code": "2",
                "abrd_futs_ord_ptn_code": "2",
                "ovrs_drvt_ord_prc": price,
                "cndi_ord_prc": 0.0,
                "ord_qty": qty,
                "exch_code": symbol_info.get("exchcd") or symbol_info.get("exchange") or "",
                "prdt_code": symbol_info.get("prdt_code") or symbol_info.get("product_code") or "",
                "due_yymm": symbol_info.get("due_yymm") or symbol_info.get("dueYymm") or "",
                "crcy_code": symbol_info.get("currency_code") or symbol_info.get("crcy_code") or "",
            })

            remaining_budget -= qty * total_per_contract
            if remaining_budget <= 0:
                break

        return orders

    async def on_real_order_receive(self, order_type, response):
        return None


__all__ = [
    "FuturesSplitFunds",
]
