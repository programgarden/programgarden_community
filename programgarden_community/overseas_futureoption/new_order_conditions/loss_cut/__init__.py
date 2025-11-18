"""
해외선물 손절매 전략
"""
from __future__ import annotations

from datetime import datetime
from typing import List

from pydantic import BaseModel, Field

from programgarden_core import (
    BaseNewOrderOverseasFutures,
    BaseNewOrderOverseasFuturesResponseType,
)


class FuturesLossCutParams(BaseModel):
    """해외선물 손절매 전략 설정."""

    losscut: float = Field(
        -5.0,
        title="손절매 비율",
        description="손절매를 실행할 손익률 임계값 (예: -5.0 = -5%)",
        le=0,
        json_schema_extra={"example": -5.0}
    )


class FuturesLossCutManager(BaseNewOrderOverseasFutures):
    """보유 선물 포지션의 손익률이 임계값을 밑돌면 자동 청산 주문을 내는 전략."""

    id: str = "FuturesLossCutManager"
    name: str = "해외선물 손절매 매니저"
    description: str = "손익률이 기준보다 낮아지면 반대 주문을 내어 포지션을 정리합니다."
    securities: List[str] = ["ls-sec.co.kr"]
    order_types = ["new_buy", "new_sell"]
    parameter_schema: dict = FuturesLossCutParams.model_json_schema()

    def __init__(self, losscut: float = -5.0) -> None:
        super().__init__()
        self.losscut = losscut

    async def execute(self) -> List[BaseNewOrderOverseasFuturesResponseType]:
        orders: List[BaseNewOrderOverseasFuturesResponseType] = []
        today = datetime.now().strftime("%Y%m%d")

        for held in self.held_symbols:
            symbol = held.get("IsuCodeVal")
            side = str(held.get("BnsTpCode", "")).strip()
            current_price = float(held.get("OvrsDrvtNowPrc", 0.0) or 0.0)
            entry_price = float(held.get("PchsPrc", 0.0) or 0.0)
            quantity = int(float(held.get("BalQty", 0) or 0))

            if not symbol or side not in ("1", "2") or quantity <= 0:
                continue

            if entry_price <= 0 or current_price <= 0:
                continue

            if side == "2":  # 보유 포지션이 매수(Long)
                pnl_pct = ((current_price - entry_price) / entry_price) * 100
                exit_side = "1"
            else:  # side == "1" -> 매도(Short)
                pnl_pct = ((entry_price - current_price) / entry_price) * 100
                exit_side = "2"

            if pnl_pct > self.losscut:
                continue

            orders.append({
                "success": True,
                "ord_dt": today,
                "isu_code_val": symbol,
                "futs_ord_tp_code": "1",
                "bns_tp_code": exit_side,
                "abrd_futs_ord_ptn_code": "1",  # 시장가
                "ovrs_drvt_ord_prc": current_price,
                "cndi_ord_prc": 0.0,
                "ord_qty": quantity,
                "exch_code": held.get("ExchCode", ""),
                "prdt_code": held.get("PrdtCode") or held.get("OvrsDrvtPrdtCode", ""),
                "due_yymm": held.get("DueYymm") or held.get("DueYyMm", ""),
                "crcy_code": held.get("CrcyCodeVal") or held.get("CrcyCode", ""),
            })

        return orders

    async def on_real_order_receive(self, order_type, response):
        return None


__all__ = [
    "FuturesLossCutManager",
]
