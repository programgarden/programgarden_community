"""
CCI (Commodity Channel Index) 해외선물 전략 조건

상품 가격의 순환적 추세를 파악하여 롱/숏 포지션 신호를 제공합니다.
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
class CCISnapshot:
    date: str
    close: float
    typical_price: float
    cci: Optional[float]
    zone: Literal["overbought", "oversold", "neutral"]
    signal: Optional[Literal["buy", "sell", "zero_cross_up", "zero_cross_down"]]


class FuturesCCIParams(BaseModel):
    """CCI 전략 파라미터"""

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
        20,
        title="CCI 기간",
        description="CCI 계산에 사용할 기간입니다.",
        ge=5,
    )
    overbought: float = Field(
        100.0,
        title="과매수 임계값",
        description="CCI가 이 값 이상이면 과매수로 판단합니다.",
    )
    oversold: float = Field(
        -100.0,
        title="과매도 임계값",
        description="CCI가 이 값 이하면 과매도로 판단합니다.",
    )
    timeframe: Literal["days", "weeks", "months"] = Field("days", title="시간 단위")
    qrycnt: int = Field(200, title="조회 캔들 수", ge=50)


class FuturesCCI(BaseStrategyConditionOverseasFutures):
    """CCI 기반 해외선물 전략"""

    id: str = "FuturesCCI"
    name: str = "해외선물 CCI"
    description: str = (
        "CCI로 선물의 과매수/과매도 상태를 파악합니다. "
        "CCI < -100 후 반등 시 롱, CCI > +100 후 하락 시 숏을 고려합니다. "
        "제로라인 크로스도 중요한 신호입니다."
    )
    securities: List[str] = ["ls-sec.co.kr"]
    weight: float = 0.5
    parameter_schema: Dict[str, object] = FuturesCCIParams.model_json_schema()

    def __init__(
        self,
        *,
        appkey: Optional[str] = None,
        appsecretkey: Optional[str] = None,
        period: int = 20,
        overbought: float = 100.0,
        oversold: float = -100.0,
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
                "data": {"error": "CCI 계산에 필요한 데이터가 부족합니다", "candle_count": len(candles), "required": self.period + 5},
            }

        snapshots = self._calculate_cci(candles)
        last = snapshots[-1] if snapshots else None

        # 포지션 방향 결정
        position_side: Literal["long", "short", "flat"] = "flat"
        if last and last.cci is not None:
            if last.zone == "oversold" or last.signal == "zero_cross_up":
                position_side = "long"  # 과매도 = 반등 기대 = 롱
            elif last.zone == "overbought" or last.signal == "zero_cross_down":
                position_side = "short"  # 과매수 = 조정 기대 = 숏

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
                "cci_value": last.cci if last else None,
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

    def _calculate_cci(self, candles: List[Candle]) -> List[CCISnapshot]:
        snapshots: List[CCISnapshot] = []

        # Typical Price 계산
        typical_prices = [(c["high"] + c["low"] + c["close"]) / 3 for c in candles]

        for i, candle in enumerate(candles):
            tp = typical_prices[i]

            if i + 1 < self.period:
                snapshots.append(
                    CCISnapshot(
                        date=candle["date"],
                        close=candle["close"],
                        typical_price=round(tp, 4),
                        cci=None,
                        zone="neutral",
                        signal=None,
                    )
                )
                continue

            # SMA of Typical Price
            tp_window = typical_prices[i + 1 - self.period : i + 1]
            tp_sma = mean(tp_window)

            # Mean Deviation
            mean_dev = mean(abs(t - tp_sma) for t in tp_window)

            # CCI 계산
            cci = (tp - tp_sma) / (0.015 * mean_dev) if mean_dev > 0 else 0

            # 존 판단
            zone: Literal["overbought", "oversold", "neutral"] = "neutral"
            if cci >= self.overbought:
                zone = "overbought"
            elif cci <= self.oversold:
                zone = "oversold"

            # 신호 판단
            signal: Optional[Literal["buy", "sell", "zero_cross_up", "zero_cross_down"]] = None
            if i > 0 and snapshots[i - 1].cci is not None:
                prev_cci = snapshots[i - 1].cci
                prev_zone = snapshots[i - 1].zone

                # 제로라인 크로스
                if prev_cci < 0 and cci >= 0:
                    signal = "zero_cross_up"
                elif prev_cci > 0 and cci <= 0:
                    signal = "zero_cross_down"
                # 과매도에서 반등
                elif prev_zone == "oversold" and zone != "oversold":
                    signal = "buy"
                # 과매수에서 하락
                elif prev_zone == "overbought" and zone != "overbought":
                    signal = "sell"

            snapshots.append(
                CCISnapshot(
                    date=candle["date"],
                    close=candle["close"],
                    typical_price=round(tp, 4),
                    cci=round(cci, 2),
                    zone=zone,
                    signal=signal,
                )
            )

        return snapshots


__all__ = ["FuturesCCI", "FuturesCCIParams"]
