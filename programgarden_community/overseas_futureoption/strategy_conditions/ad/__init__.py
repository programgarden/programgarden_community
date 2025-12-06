"""
A/D (Accumulation/Distribution) 해외선물 전략 조건

자금 흐름을 추적하여 매집/분산 상태를 파악하고 롱/숏 포지션 신호를 제공합니다.
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
class ADSnapshot:
    date: str
    close: float
    volume: int
    money_flow_multiplier: Optional[float]
    money_flow_volume: Optional[float]
    ad_line: float
    ad_ma: Optional[float]
    trend: Literal["accumulation", "distribution", "neutral"]
    divergence: Optional[Literal["bullish", "bearish"]]


class FuturesADParams(BaseModel):
    """A/D 전략 파라미터"""

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
    ma_period: int = Field(
        20,
        title="이동평균 기간",
        description="A/D Line 이동평균 계산 기간입니다.",
        ge=5,
    )
    divergence_lookback: int = Field(
        10,
        title="다이버전스 확인 기간",
        description="다이버전스 판단을 위한 과거 데이터 확인 기간입니다.",
        ge=5,
    )
    timeframe: Literal["days", "weeks", "months"] = Field("days", title="시간 단위")
    qrycnt: int = Field(200, title="조회 캔들 수", ge=50)


class FuturesAD(BaseStrategyConditionOverseasFutures):
    """A/D Line 기반 해외선물 전략"""

    id: str = "FuturesAD"
    name: str = "해외선물 A/D Line"
    description: str = (
        "A/D Line으로 선물의 매집/분산 상태를 파악합니다. "
        "A/D 상승 추세면 매집(롱), 하락 추세면 분산(숏)을 고려합니다. "
        "Money Flow Multiplier로 거래량 흐름의 질을 분석합니다."
    )
    securities: List[str] = ["ls-sec.co.kr"]
    weight: float = 0.5
    parameter_schema: Dict[str, object] = FuturesADParams.model_json_schema()

    def __init__(
        self,
        *,
        appkey: Optional[str] = None,
        appsecretkey: Optional[str] = None,
        ma_period: int = 20,
        divergence_lookback: int = 10,
        timeframe: Literal["days", "weeks", "months"] = "days",
        qrycnt: int = 200,
    ) -> None:
        super().__init__()
        self.appkey = appkey
        self.appsecretkey = appsecretkey
        self.ma_period = ma_period
        self.divergence_lookback = divergence_lookback
        self.timeframe = timeframe
        self.qrycnt = max(qrycnt, ma_period + 50)
        self._ls = None

    async def execute(self) -> BaseStrategyConditionResponseOverseasFuturesType:
        candles = await self._load_candles()
        symbol = self.symbol.get("symbol", "") if self.symbol else ""
        exchcd = self.symbol.get("exchcd", "") if self.symbol else ""

        if len(candles) < self.ma_period + 10:
            return {
                "condition_id": self.id,
                "description": self.description,
                "success": False,
                "symbol": symbol,
                "exchcd": exchcd,
                "product": self.product_type,
                "position_side": "flat",
                "weight": self.weight,
                "data": {"error": "A/D 계산에 필요한 데이터가 부족합니다", "candle_count": len(candles), "required": self.ma_period + 10},
            }

        snapshots = self._calculate_ad(candles)
        last = snapshots[-1] if snapshots else None

        # 포지션 방향 결정
        position_side: Literal["long", "short", "flat"] = "flat"
        if last:
            if last.divergence == "bullish" or last.trend == "accumulation":
                position_side = "long"
            elif last.divergence == "bearish" or last.trend == "distribution":
                position_side = "short"

        success = last is not None and (
            last.divergence is not None or last.trend in {"accumulation", "distribution"}
        )

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
                "trend": last.trend if last else "neutral",
                "divergence": last.divergence if last else None,
                "ad_line": last.ad_line if last else None,
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

    def _calculate_ad(self, candles: List[Candle]) -> List[ADSnapshot]:
        snapshots: List[ADSnapshot] = []
        ad_line = 0.0

        for i, candle in enumerate(candles):
            high_low = candle["high"] - candle["low"]

            # Money Flow Multiplier 계산
            if high_low > 0:
                mfm = ((candle["close"] - candle["low"]) - (candle["high"] - candle["close"])) / high_low
            else:
                mfm = 0

            # Money Flow Volume 계산
            mfv = mfm * candle["volume"]

            # A/D Line 누적
            ad_line += mfv

            # A/D Line 이동평균
            ad_ma = None
            if i + 1 >= self.ma_period:
                ad_values = [s.ad_line for s in snapshots[i - self.ma_period + 1 :]]
                ad_values.append(ad_line)
                ad_ma = mean(ad_values)

            # 추세 판단
            trend: Literal["accumulation", "distribution", "neutral"] = "neutral"
            if ad_ma is not None:
                if ad_line > ad_ma:
                    trend = "accumulation"
                elif ad_line < ad_ma:
                    trend = "distribution"

            # 다이버전스 판단
            divergence: Optional[Literal["bullish", "bearish"]] = None
            if i >= self.divergence_lookback:
                lookback_start = i - self.divergence_lookback
                price_change = candle["close"] - candles[lookback_start]["close"]
                ad_change = ad_line - snapshots[lookback_start].ad_line

                # Bullish divergence: 가격 하락 + A/D 상승
                if price_change < 0 and ad_change > 0:
                    divergence = "bullish"
                # Bearish divergence: 가격 상승 + A/D 하락
                elif price_change > 0 and ad_change < 0:
                    divergence = "bearish"

            snapshots.append(
                ADSnapshot(
                    date=candle["date"],
                    close=candle["close"],
                    volume=candle["volume"],
                    money_flow_multiplier=round(mfm, 4),
                    money_flow_volume=round(mfv, 2),
                    ad_line=round(ad_line, 2),
                    ad_ma=round(ad_ma, 2) if ad_ma else None,
                    trend=trend,
                    divergence=divergence,
                )
            )

        return snapshots


__all__ = ["FuturesAD", "FuturesADParams"]
