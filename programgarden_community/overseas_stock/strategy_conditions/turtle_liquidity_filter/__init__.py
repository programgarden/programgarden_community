from __future__ import annotations

from statistics import mean
from typing import Dict, List, Optional, TypedDict

from programgarden_core import (
    BaseStrategyConditionOverseasStock,
    BaseStrategyConditionResponseOverseasStockType,
)
from programgarden_finance import LS, g3204
from pydantic import BaseModel, Field


class Candle(TypedDict):
    date: str
    close: float
    volume: float


class TurtleLiquidityFilterParams(BaseModel):
    appkey: Optional[str] = Field(
        None,
        title="LS증권 앱키",
        description="LS OpenAPI에 로그인할 때 쓰는 열쇠입니다. 모의/실계좌 어디든 데이터를 받으려면 꼭 넣어야 합니다.",
    )
    appsecretkey: Optional[str] = Field(
        None,
        title="LS증권 앱시크릿",
        description="앱키와 짝을 이루는 비밀번호입니다. 앱키만 넣으면 인증이 완료되지 않으므로 항상 함께 입력해야 합니다.",
    )
    lookback_days: int = Field(
        20,
        ge=5,
        title="평균 산정 일수",
        description="최근 며칠 동안의 데이터를 보고 거래대금/거래량 평균을 낼지 정합니다. 길수록 지표가 부드럽고, 짧을수록 민감합니다.",
    )
    min_turnover: float = Field(
        1000000,
        gt=0,
        title="최소 거래대금",
        description="하루 평균 얼마만큼의 돈이 오갔으면 하는지 금액으로 지정합니다. 너무 낮으면 체결이 어렵다고 판단합니다.",
    )
    min_volume: int = Field(
        300000,
        ge=1,
        title="최소 거래량",
        description="하루 평균 몇 주 이상이 거래되었으면 하는지를 정합니다. 기준보다 적으면 유동성이 부족하다고 표시합니다.",
    )
    qrycnt: int = Field(
        80,
        ge=30,
        title="조회 캔들 수",
        description="서버에서 몇 개의 일봉을 가져올지 정합니다. 평균을 낼 일수보다 조금 더 크게 설정하면 안전합니다.",
    )


class TurtleLiquidityFilter(BaseStrategyConditionOverseasStock):
    id: str = "OverseasStockTurtleLiquidity"
    name: str = "터틀 유동성 필터"
    description: str = "20일 평균 거래대금/거래량 기준을 통과한 종목만 남깁니다."
    parameter_schema: Dict[str, object] = TurtleLiquidityFilterParams.model_json_schema()

    def __init__(
        self,
        *,
        appkey: Optional[str] = None,
        appsecretkey: Optional[str] = None,
        lookback_days: int = 20,
        min_turnover: float = 1_000_000,
        min_volume: int = 300_000,
        qrycnt: int = 80,
    ) -> None:
        super().__init__()
        self.appkey = appkey
        self.appsecretkey = appsecretkey
        self.lookback_days = lookback_days
        self.min_turnover = min_turnover
        self.min_volume = min_volume
        self.qrycnt = max(qrycnt, lookback_days + 5)
        self._ls = LS.get_instance()

    async def execute(self) -> BaseStrategyConditionResponseOverseasStockType:
        if not self.symbol:
            raise ValueError("symbol 정보가 필요합니다")
        candles = await self._load_candles()
        if len(candles) < self.lookback_days:
            raise ValueError("유동성 체크에 필요한 일수가 부족합니다")
        recent = candles[-self.lookback_days :]
        avg_turnover = self._avg_turnover(recent)
        avg_volume = self._avg_volume(recent)
        pass_turnover = avg_turnover >= self.min_turnover
        pass_volume = avg_volume >= self.min_volume
        success = pass_turnover and pass_volume
        exchcd = self.symbol.get("exchcd", "")
        symbol_code = self.symbol.get("symbol", "")
        data = {
            "average_turnover": round(avg_turnover, 2),
            "average_volume": round(avg_volume, 2),
            "thresholds": {
                "turnover": self.min_turnover,
                "volume": self.min_volume,
            },
            "lookback_days": self.lookback_days,
            "history": recent,
            "checks": {
                "turnover": pass_turnover,
                "volume": pass_volume,
            },
        }
        return {
            "condition_id": self.id,
            "description": self.description,
            "success": success,
            "symbol": symbol_code,
            "exchcd": exchcd,
            "product": self.product_type,
            "weight": 0.15,
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
                        close=float(block.close),
                        volume=int(getattr(block, "volume", 0)),
                    )
                )
        candles.sort(key=lambda item: item["date"])
        return candles

    async def _ensure_login(self) -> None:
        if not self.appkey or not self.appsecretkey:
            raise ValueError("appkey와 appsecretkey를 모두 입력해야 합니다")
        if not self._ls.is_logged_in():
            await self._ls.async_login(self.appkey, self.appsecretkey)

    def _avg_turnover(self, candles: List[Candle]) -> float:
        return mean(candle["close"] * candle["volume"] for candle in candles)

    def _avg_volume(self, candles: List[Candle]) -> float:
        return mean(candle["volume"] for candle in candles)


__all__ = ["TurtleLiquidityFilter"]
