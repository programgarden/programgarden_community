from __future__ import annotations

from typing import Dict, List, Optional, TypedDict

from programgarden_core import (
    BaseStrategyConditionOverseasStock,
    BaseStrategyConditionResponseOverseasStockType,
)
from programgarden_finance import LS, g3204
from pydantic import BaseModel, Field


class Candle(TypedDict):
    date: str
    open: float
    high: float
    low: float
    close: float


class TurtleVolatilityFilterParams(BaseModel):
    appkey: Optional[str] = Field(
        None,
        title="LS증권 앱키",
        description="LS OpenAPI 로그인을 위한 사용자 키입니다. 데이터를 받으려면 반드시 입력해야 합니다.",
    )
    appsecretkey: Optional[str] = Field(
        None,
        title="LS증권 앱시크릿",
        description="앱키와 함께 사용하는 비밀값입니다. 두 값을 함께 넣어야 인증이 완료됩니다.",
    )
    atr_period: int = Field(
        20,
        ge=5,
        title="ATR 기간",
        description="ATR을 계산할 때 참고할 일수입니다. 길게 잡을수록 부드럽고, 짧게 잡으면 신호가 민감해집니다.",
    )
    min_atr: float = Field(
        0.5,
        gt=0,
        title="최소 ATR",
        description="하루 평균 움직임이 이 값 이상일 때만 ‘변동성이 충분하다’고 보고 통과시킵니다.",
    )
    qrycnt: int = Field(
        120,
        ge=40,
        title="조회 캔들 수",
        description="서버에서 가져올 일봉 개수입니다. ATR 기간보다 넉넉하게 지정하면 계산이 안정적입니다.",
    )


class TurtleVolatilityFilter(BaseStrategyConditionOverseasStock):
    id: str = "OverseasStockTurtleVolatility"
    name: str = "터틀 변동성 필터"
    description: str = "ATR이 충분히 큰 종목만 남겨 터틀식 추세 추종이 가능한지 확인합니다."
    parameter_schema: Dict[str, object] = TurtleVolatilityFilterParams.model_json_schema()

    def __init__(
        self,
        *,
        appkey: Optional[str] = None,
        appsecretkey: Optional[str] = None,
        atr_period: int = 20,
        min_atr: float = 0.5,
        qrycnt: int = 120,
    ) -> None:
        super().__init__()
        self.appkey = appkey
        self.appsecretkey = appsecretkey
        self.atr_period = atr_period
        self.min_atr = min_atr
        self.qrycnt = max(qrycnt, atr_period * 3)
        self._ls = LS.get_instance()

    async def execute(self) -> BaseStrategyConditionResponseOverseasStockType:
        if not self.symbol:
            raise ValueError("symbol 정보가 필요합니다")
        candles = await self._load_candles()
        if len(candles) < self.atr_period + 5:
            raise ValueError("ATR 계산을 위한 캔들이 부족합니다")
        atr_value = self._atr(candles, self.atr_period)
        latest_close = candles[-1]["close"]
        success = atr_value >= self.min_atr
        exchcd = self.symbol.get("exchcd", "")
        symbol_code = self.symbol.get("symbol", "")
        data = {
            "atr": round(atr_value, 4),
            "atr_period": self.atr_period,
            "min_atr": self.min_atr,
            "atr_percent_of_price": round((atr_value / latest_close) * 100, 3) if latest_close else 0.0,
            "latest_close": latest_close,
            "history": candles[-(self.atr_period + 1) :],
        }
        return {
            "condition_id": self.id,
            "description": self.description,
            "success": success,
            "symbol": symbol_code,
            "exchcd": exchcd,
            "product": self.product_type,
            "weight": 0.6,
            "data": data,
        }

    async def _load_candles(self) -> List[Candle]:
        await self._ensure_login()
        exchcd = self.symbol.get("exchcd") if self.symbol else None
        symbol = self.symbol.get("symbol") if self.symbol else None
        if not exchcd or not symbol:
            raise ValueError("symbol/exchcd 정보를 확인하세요")
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
                    )
                )
        candles.sort(key=lambda item: item["date"])
        return candles

    async def _ensure_login(self) -> None:
        if not self.appkey or not self.appsecretkey:
            raise ValueError("appkey와 appsecretkey를 모두 입력해야 합니다")
        if not self._ls.is_logged_in():
            await self._ls.async_login(self.appkey, self.appsecretkey)

    def _atr(self, candles: List[Candle], period: int) -> float:
        trs: List[float] = []
        prev_close = candles[0]["close"]
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


__all__ = ["TurtleVolatilityFilter"]
