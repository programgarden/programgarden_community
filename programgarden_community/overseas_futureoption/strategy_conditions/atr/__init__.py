"""
ATR (Average True Range) 해외선물 전략 조건

변동성을 측정하여 포지션 사이징과 손절 레벨 설정에 활용합니다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import mean
from typing import Dict, List, Literal, Optional, TypedDict

from programgarden_core import (
    BaseStrategyConditionOverseasFutures,
    BaseStrategyConditionResponseOverseasFuturesType,
)
from pydantic import BaseModel, Field


class Candle(TypedDict):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class ATRSnapshot:
    date: str
    close: float
    true_range: Optional[float]
    atr: Optional[float]
    natr: Optional[float]  # Normalized ATR (%)
    volatility_level: Literal["high", "normal", "low"]
    suggested_stop: Optional[float]


class FuturesATRParams(BaseModel):
    """ATR 전략 파라미터"""

    appkey: Optional[str] = Field(
        None,
        title="LS증권 앱키",
        description="LS OpenAPI 인증 키입니다.",
    )
    appsecretkey: Optional[str] = Field(
        None,
        title="LS증권 앱시크릿",
        description="LS OpenAPI 인증 시크릿입니다.",
    )
    period: int = Field(
        14,
        title="ATR 기간",
        description="ATR 계산에 사용할 기간입니다.",
        ge=5,
    )
    volatility_high_threshold: float = Field(
        1.5,
        title="높은 변동성 배수",
        description="ATR이 평균의 이 배수 이상이면 높은 변동성으로 판단합니다.",
        gt=1.0,
    )
    volatility_low_threshold: float = Field(
        0.7,
        title="낮은 변동성 배수",
        description="ATR이 평균의 이 배수 이하면 낮은 변동성으로 판단합니다.",
        lt=1.0,
        gt=0,
    )
    stop_multiplier: float = Field(
        2.0,
        title="손절 ATR 배수",
        description="손절가 계산 시 ATR에 곱할 배수입니다.",
        gt=0,
    )
    timeframe: Literal["days", "weeks", "months"] = Field("days", title="시간 단위")
    qrycnt: int = Field(200, title="조회 캔들 수", ge=50)


class FuturesATR(BaseStrategyConditionOverseasFutures):
    """ATR 기반 해외선물 전략"""

    id: str = "FuturesATR"
    name: str = "해외선물 ATR"
    description: str = (
        "ATR로 선물의 변동성을 측정합니다. "
        "변동성이 높을 때는 포지션 크기를 줄이고 손절폭을 넓힙니다. "
        "NATR로 상품 간 변동성을 비교할 수 있습니다."
    )
    securities: List[str] = ["ls-sec.co.kr"]
    weight: float = 0.5
    parameter_schema: Dict[str, object] = FuturesATRParams.model_json_schema()

    def __init__(
        self,
        *,
        appkey: Optional[str] = None,
        appsecretkey: Optional[str] = None,
        period: int = 14,
        volatility_high_threshold: float = 1.5,
        volatility_low_threshold: float = 0.7,
        stop_multiplier: float = 2.0,
        timeframe: Literal["days", "weeks", "months"] = "days",
        qrycnt: int = 200,
    ) -> None:
        super().__init__()
        self.appkey = appkey
        self.appsecretkey = appsecretkey
        self.period = period
        self.volatility_high_threshold = volatility_high_threshold
        self.volatility_low_threshold = volatility_low_threshold
        self.stop_multiplier = stop_multiplier
        self.timeframe = timeframe
        self.qrycnt = max(qrycnt, period * 3)
        self._ls = None

    async def execute(self) -> BaseStrategyConditionResponseOverseasFuturesType:
        candles = await self._load_candles()
        symbol = self.symbol.get("symbol", "") if self.symbol else ""
        exchcd = self.symbol.get("exchcd", "") if self.symbol else ""

        if len(candles) < self.period + 5:
            return {
                "condition_id": self.id,
                "description": self.description,
                "success": False,
                "symbol": symbol,
                "exchcd": exchcd,
                "product": self.product_type,
                "position_side": "flat",
                "weight": self.weight,
                "data": {"error": "ATR 계산에 필요한 데이터가 부족합니다", "candle_count": len(candles), "required": self.period + 5},
            }

        snapshots = self._calculate_atr(candles)
        last = snapshots[-1] if snapshots else None

        # ATR은 방향 지표가 아니므로 position_side는 neutral로 설정
        # 다른 방향성 전략과 조합하여 사용 (필터 역할)
        position_side: Literal["long", "short", "flat", "neutral"] = "neutral"
        success = last is not None and last.volatility_level in {"high", "low"}

        symbol = self.symbol.get("symbol", "") if self.symbol else ""
        exchcd = self.symbol.get("exchcd", "") if self.symbol else ""

        return {
            "condition_id": self.id,
            "description": self.description,
            "success": success,
            "symbol": symbol,
            "exchcd": exchcd,
            "product": self.product_type,
            "position_side": position_side,
            "weight": self.weight,
            "data": {
                "snapshots": [asdict(s) for s in snapshots[-100:]],
                "current": asdict(last) if last else None,
                "volatility_level": last.volatility_level if last else "normal",
                "atr": last.atr if last else None,
                "natr": last.natr if last else None,
                "suggested_stop": last.suggested_stop if last else None,
            },
        }

    async def _load_candles(self) -> List[Candle]:
        from programgarden_finance import LS, o3108

        if not self.appkey or not self.appsecretkey:
            raise ValueError("LS증권 API 키가 필요합니다")
        if not self.symbol:
            raise ValueError("종목 정보가 필요합니다")

        if self._ls is None:
            self._ls = LS.get_instance()
        
        if not self._ls.token_manager or not self._ls.token_manager.is_token_available():
            await self._ls.async_login(self.appkey, self.appsecretkey)

        shcode = self.symbol.get("symbol", "")
        gubun_map = {"days": "0", "weeks": "1", "months": "2"}

        request = self._ls.overseas_futureoption().chart().o3108(
            body=o3108.O3108InBlock(
                shcode=shcode,
                gubun=gubun_map.get(self.timeframe, "0"),
                qrycnt=self.qrycnt,
                sdate="",
                edate="",
                cts_date="",
            )
        )
        responses = await request.occurs_req_async()

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
        candles.sort(key=lambda c: c["date"])
        return candles

    def _calculate_atr(self, candles: List[Candle]) -> List[ATRSnapshot]:
        snapshots: List[ATRSnapshot] = []

        # True Range 계산
        tr_list: List[float] = []
        for i, candle in enumerate(candles):
            if i == 0:
                tr = candle["high"] - candle["low"]
            else:
                prev_close = candles[i - 1]["close"]
                tr = max(
                    candle["high"] - candle["low"],
                    abs(candle["high"] - prev_close),
                    abs(candle["low"] - prev_close),
                )
            tr_list.append(tr)

        # ATR 계산 (Wilder's smoothing)
        atr_list: List[Optional[float]] = []
        for i in range(len(candles)):
            if i + 1 < self.period:
                atr_list.append(None)
            elif i + 1 == self.period:
                atr_list.append(mean(tr_list[: self.period]))
            else:
                prev_atr = atr_list[i - 1]
                if prev_atr is not None:
                    atr_list.append((prev_atr * (self.period - 1) + tr_list[i]) / self.period)
                else:
                    atr_list.append(None)

        # 평균 ATR 계산 (최근 50일 기준)
        valid_atrs = [a for a in atr_list[-50:] if a is not None]
        avg_atr = mean(valid_atrs) if valid_atrs else 1

        # 스냅샷 생성
        for i, candle in enumerate(candles):
            atr = atr_list[i]
            tr = tr_list[i]

            natr = (atr / candle["close"] * 100) if atr and candle["close"] > 0 else None

            volatility_level: Literal["high", "normal", "low"] = "normal"
            if atr is not None:
                if atr >= avg_atr * self.volatility_high_threshold:
                    volatility_level = "high"
                elif atr <= avg_atr * self.volatility_low_threshold:
                    volatility_level = "low"

            suggested_stop = atr * self.stop_multiplier if atr else None

            snapshots.append(
                ATRSnapshot(
                    date=candle["date"],
                    close=candle["close"],
                    true_range=round(tr, 4),
                    atr=round(atr, 4) if atr else None,
                    natr=round(natr, 4) if natr else None,
                    volatility_level=volatility_level,
                    suggested_stop=round(suggested_stop, 4) if suggested_stop else None,
                )
            )

        return snapshots


__all__ = ["FuturesATR", "FuturesATRParams"]
