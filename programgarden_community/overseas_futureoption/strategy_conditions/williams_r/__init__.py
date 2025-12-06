"""
Williams %R 해외선물 전략 조건

모멘텀 오실레이터로 과매수/과매도 상태를 파악하여 롱/숏 포지션 신호를 제공합니다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
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
class WilliamsRSnapshot:
    date: str
    close: float
    highest_high: Optional[float]
    lowest_low: Optional[float]
    williams_r: Optional[float]
    zone: Literal["overbought", "oversold", "neutral"]
    signal: Optional[Literal["buy", "sell", "momentum_shift_up", "momentum_shift_down"]]


class FuturesWilliamsRParams(BaseModel):
    """Williams %R 전략 파라미터"""

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
        title="기간",
        description="Williams %R 계산에 사용할 기간입니다.",
        ge=5,
    )
    overbought: float = Field(
        -20.0,
        title="과매수 임계값",
        description="%R이 이 값 이상이면 과매수로 판단합니다.",
        ge=-50,
        le=0,
    )
    oversold: float = Field(
        -80.0,
        title="과매도 임계값",
        description="%R이 이 값 이하면 과매도로 판단합니다.",
        ge=-100,
        le=-50,
    )
    timeframe: Literal["days", "weeks", "months"] = Field("days", title="시간 단위")
    qrycnt: int = Field(200, title="조회 캔들 수", ge=50)


class FuturesWilliamsR(BaseStrategyConditionOverseasFutures):
    """Williams %R 기반 해외선물 전략"""

    id: str = "FuturesWilliamsR"
    name: str = "해외선물 Williams %R"
    description: str = (
        "Williams %R로 선물의 과매수/과매도 상태를 파악합니다. "
        "%R이 -80 이하 후 반등 시 롱, -20 이상 후 하락 시 숏을 고려합니다."
    )
    securities: List[str] = ["ls-sec.co.kr"]
    weight: float = 0.5
    parameter_schema: Dict[str, object] = FuturesWilliamsRParams.model_json_schema()

    def __init__(
        self,
        *,
        appkey: Optional[str] = None,
        appsecretkey: Optional[str] = None,
        period: int = 14,
        overbought: float = -20.0,
        oversold: float = -80.0,
        timeframe: Literal["days", "weeks", "months"] = "days",
        qrycnt: int = 200,
    ) -> None:
        super().__init__()
        self.appkey = appkey
        self.appsecretkey = appsecretkey
        self.period = period
        self.overbought = overbought
        self.oversold = oversold
        self.timeframe = timeframe
        self.qrycnt = max(qrycnt, period + 50)
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
                "data": {"error": "Williams %R 계산에 필요한 데이터가 부족합니다", "candle_count": len(candles), "required": self.period + 5},
            }

        snapshots = self._calculate_williams_r(candles)
        last = snapshots[-1] if snapshots else None

        # 포지션 방향 결정
        position_side: Literal["long", "short", "flat"] = "flat"
        if last and last.williams_r is not None:
            if last.zone == "oversold" or last.signal in {"buy", "momentum_shift_up"}:
                position_side = "long"
            elif last.zone == "overbought" or last.signal in {"sell", "momentum_shift_down"}:
                position_side = "short"

        success = last is not None and last.signal is not None

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
                "zone": last.zone if last else "neutral",
                "williams_r": last.williams_r if last else None,
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

    def _calculate_williams_r(self, candles: List[Candle]) -> List[WilliamsRSnapshot]:
        snapshots: List[WilliamsRSnapshot] = []

        for i, candle in enumerate(candles):
            if i + 1 < self.period:
                snapshots.append(
                    WilliamsRSnapshot(
                        date=candle["date"],
                        close=candle["close"],
                        highest_high=None,
                        lowest_low=None,
                        williams_r=None,
                        zone="neutral",
                        signal=None,
                    )
                )
                continue

            # 기간 내 최고/최저
            window = candles[i + 1 - self.period : i + 1]
            highest_high = max(c["high"] for c in window)
            lowest_low = min(c["low"] for c in window)

            # Williams %R 계산
            if highest_high != lowest_low:
                williams_r = (highest_high - candle["close"]) / (highest_high - lowest_low) * -100
            else:
                williams_r = -50.0

            # 존 판단
            zone: Literal["overbought", "oversold", "neutral"] = "neutral"
            if williams_r >= self.overbought:
                zone = "overbought"
            elif williams_r <= self.oversold:
                zone = "oversold"

            # 신호 판단
            signal: Optional[Literal["buy", "sell", "momentum_shift_up", "momentum_shift_down"]] = None
            if i > 0 and snapshots[i - 1].williams_r is not None:
                prev_wr = snapshots[i - 1].williams_r
                prev_zone = snapshots[i - 1].zone

                # 과매도에서 반등
                if prev_zone == "oversold" and zone != "oversold":
                    signal = "buy"
                # 과매수에서 하락
                elif prev_zone == "overbought" and zone != "overbought":
                    signal = "sell"
                # 모멘텀 시프트 (중앙선 -50 기준)
                elif prev_wr < -50 and williams_r >= -50:
                    signal = "momentum_shift_up"
                elif prev_wr > -50 and williams_r <= -50:
                    signal = "momentum_shift_down"

            snapshots.append(
                WilliamsRSnapshot(
                    date=candle["date"],
                    close=candle["close"],
                    highest_high=round(highest_high, 4),
                    lowest_low=round(lowest_low, 4),
                    williams_r=round(williams_r, 2),
                    zone=zone,
                    signal=signal,
                )
            )

        return snapshots


__all__ = ["FuturesWilliamsR", "FuturesWilliamsRParams"]
