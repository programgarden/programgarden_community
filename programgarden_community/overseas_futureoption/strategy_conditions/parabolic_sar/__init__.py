"""
파라볼릭 SAR (Parabolic SAR) 해외선물 전략 조건

추세 방향과 잠재적 반전 시점을 포착하여 롱/숏 포지션 신호를 제공합니다.
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
class SARSnapshot:
    date: str
    close: float
    high: float
    low: float
    sar: float
    trend: Literal["bullish", "bearish"]
    af: float
    ep: float
    reversal: bool


class FuturesParabolicSARParams(BaseModel):
    """파라볼릭 SAR 전략 파라미터"""

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
    af_start: float = Field(
        0.02,
        title="가속계수 초기값",
        description="AF(Acceleration Factor)의 시작값입니다.",
        gt=0,
        le=0.1,
    )
    af_increment: float = Field(
        0.02,
        title="가속계수 증가량",
        description="신고/신저 갱신 시 AF 증가량입니다.",
        gt=0,
        le=0.1,
    )
    af_max: float = Field(
        0.2,
        title="가속계수 최대값",
        description="AF의 최대값입니다.",
        gt=0,
        le=0.5,
    )
    timeframe: Literal["days", "weeks", "months"] = Field("days", title="시간 단위")
    qrycnt: int = Field(200, title="조회 캔들 수", ge=50)


class FuturesParabolicSAR(BaseStrategyConditionOverseasFutures):
    """파라볼릭 SAR 기반 해외선물 전략"""

    id: str = "FuturesParabolicSAR"
    name: str = "해외선물 파라볼릭 SAR"
    description: str = (
        "SAR 포인트로 선물 추세 방향을 파악합니다. "
        "SAR이 가격 아래면 롱, 위면 숏 포지션을 고려합니다. "
        "반전 신호 발생 시 포지션 전환을 검토합니다."
    )
    securities: List[str] = ["ls-sec.co.kr"]
    weight: float = 0.5
    parameter_schema: Dict[str, object] = FuturesParabolicSARParams.model_json_schema()

    def __init__(
        self,
        *,
        appkey: Optional[str] = None,
        appsecretkey: Optional[str] = None,
        af_start: float = 0.02,
        af_increment: float = 0.02,
        af_max: float = 0.2,
        timeframe: Literal["days", "weeks", "months"] = "days",
        qrycnt: int = 200,
    ) -> None:
        super().__init__()
        self.appkey = appkey
        self.appsecretkey = appsecretkey
        self.af_start = af_start
        self.af_increment = af_increment
        self.af_max = af_max
        self.timeframe = timeframe
        self.qrycnt = qrycnt
        self._ls = None

    async def execute(self) -> BaseStrategyConditionResponseOverseasFuturesType:
        candles = await self._load_candles()
        symbol = self.symbol.get("symbol", "") if self.symbol else ""
        exchcd = self.symbol.get("exchcd", "") if self.symbol else ""

        if len(candles) < 10:
            return {
                "condition_id": self.id,
                "description": self.description,
                "success": False,
                "symbol": symbol,
                "exchcd": exchcd,
                "product": self.product_type,
                "position_side": "flat",
                "weight": self.weight,
                "data": {"error": "SAR 계산에 필요한 데이터가 부족합니다", "candle_count": len(candles), "required": 10},
            }

        snapshots = self._calculate_sar(candles)
        last = snapshots[-1] if snapshots else None

        # 포지션 방향 결정
        position_side: Literal["long", "short", "flat"] = "flat"
        if last:
            if last.trend == "bullish":
                position_side = "long"  # SAR 아래 = 상승 추세 = 롱
            else:
                position_side = "short"  # SAR 위 = 하락 추세 = 숏

        # 추세 지속 또는 반전 시 success
        recent_reversals = sum(1 for s in snapshots[-5:] if s.reversal)
        success = last is not None and (last.reversal or recent_reversals > 0)

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
                "trend": last.trend if last else "unknown",
                "recent_reversals": recent_reversals,
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

    def _calculate_sar(self, candles: List[Candle]) -> List[SARSnapshot]:
        if len(candles) < 2:
            return []

        snapshots: List[SARSnapshot] = []

        # 초기화 - 처음 몇 개 캔들로 초기 추세 결정
        initial_high = max(c["high"] for c in candles[:5])
        initial_low = min(c["low"] for c in candles[:5])
        trend: Literal["bullish", "bearish"] = (
            "bullish" if candles[4]["close"] > candles[0]["close"] else "bearish"
        )

        if trend == "bullish":
            sar = initial_low
            ep = initial_high
        else:
            sar = initial_high
            ep = initial_low

        af = self.af_start

        for i, candle in enumerate(candles):
            reversal = False

            if trend == "bullish":
                if candle["low"] < sar:
                    trend = "bearish"
                    reversal = True
                    sar = ep
                    ep = candle["low"]
                    af = self.af_start
                else:
                    if candle["high"] > ep:
                        ep = candle["high"]
                        af = min(af + self.af_increment, self.af_max)
                    next_sar = sar + af * (ep - sar)
                    sar = min(next_sar, candle["low"], candles[i - 1]["low"] if i > 0 else candle["low"])
            else:
                if candle["high"] > sar:
                    trend = "bullish"
                    reversal = True
                    sar = ep
                    ep = candle["high"]
                    af = self.af_start
                else:
                    if candle["low"] < ep:
                        ep = candle["low"]
                        af = min(af + self.af_increment, self.af_max)
                    next_sar = sar + af * (ep - sar)
                    sar = max(next_sar, candle["high"], candles[i - 1]["high"] if i > 0 else candle["high"])

            snapshots.append(
                SARSnapshot(
                    date=candle["date"],
                    close=candle["close"],
                    high=candle["high"],
                    low=candle["low"],
                    sar=round(sar, 4),
                    trend=trend,
                    af=round(af, 4),
                    ep=round(ep, 4),
                    reversal=reversal,
                )
            )

        return snapshots


__all__ = ["FuturesParabolicSAR", "FuturesParabolicSARParams"]
