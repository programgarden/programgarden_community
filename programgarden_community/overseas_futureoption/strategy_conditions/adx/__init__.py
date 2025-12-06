"""
ADX (Average Directional Index) 해외선물 전략 조건

추세의 강도와 방향을 측정하여 롱/숏 포지션 신호를 제공합니다.
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
class ADXSnapshot:
    date: str
    close: float
    plus_di: Optional[float]
    minus_di: Optional[float]
    adx: Optional[float]
    trend_strength: Literal["strong", "moderate", "weak", "none"]
    direction: Literal["bullish", "bearish", "neutral"]
    signal: Optional[Literal["long", "short", "exit"]]


class FuturesADXParams(BaseModel):
    """ADX 전략 파라미터"""

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
        title="ADX 기간",
        description="ADX와 DI 계산에 사용할 기간입니다.",
        ge=5,
    )
    strong_threshold: float = Field(
        25.0,
        title="강한 추세 임계값",
        description="ADX가 이 값 이상이면 강한 추세로 판단합니다.",
        gt=0,
    )
    weak_threshold: float = Field(
        20.0,
        title="약한 추세 임계값",
        description="ADX가 이 값 이하면 약한 추세로 판단합니다.",
        gt=0,
    )
    timeframe: Literal["days", "weeks", "months"] = Field("days", title="시간 단위")
    qrycnt: int = Field(200, title="조회 캔들 수", ge=50)


class FuturesADX(BaseStrategyConditionOverseasFutures):
    """ADX 기반 해외선물 전략"""

    id: str = "FuturesADX"
    name: str = "해외선물 ADX"
    description: str = (
        "ADX로 선물 추세 강도와 방향을 파악합니다. "
        "+DI > -DI면 롱, -DI > +DI면 숏 포지션을 고려합니다. "
        "ADX 25 이상에서 강한 추세로 판단합니다."
    )
    securities: List[str] = ["ls-sec.co.kr"]
    weight: float = 0.5
    parameter_schema: Dict[str, object] = FuturesADXParams.model_json_schema()

    def __init__(
        self,
        *,
        appkey: Optional[str] = None,
        appsecretkey: Optional[str] = None,
        period: int = 14,
        strong_threshold: float = 25.0,
        weak_threshold: float = 20.0,
        timeframe: Literal["days", "weeks", "months"] = "days",
        qrycnt: int = 200,
    ) -> None:
        super().__init__()
        self.appkey = appkey
        self.appsecretkey = appsecretkey
        self.period = period
        self.strong_threshold = strong_threshold
        self.weak_threshold = weak_threshold
        self.timeframe = timeframe
        self.qrycnt = max(qrycnt, period * 3)
        self._ls = None

    async def execute(self) -> BaseStrategyConditionResponseOverseasFuturesType:
        candles = await self._load_candles()
        symbol = self.symbol.get("symbol", "") if self.symbol else ""
        exchcd = self.symbol.get("exchcd", "") if self.symbol else ""

        if len(candles) < self.period * 2:
            return {
                "condition_id": self.id,
                "description": self.description,
                "success": False,
                "symbol": symbol,
                "exchcd": exchcd,
                "product": self.product_type,
                "position_side": "flat",
                "weight": self.weight,
                "data": {"error": "ADX 계산에 필요한 데이터가 부족합니다", "candle_count": len(candles), "required": self.period * 2},
            }

        snapshots = self._calculate_adx(candles)
        last = snapshots[-1] if snapshots else None

        # 포지션 방향 결정
        position_side: Literal["long", "short", "flat"] = "flat"
        if last and last.adx is not None:
            if last.signal == "long":
                position_side = "long"
            elif last.signal == "short":
                position_side = "short"

        # 강한 추세 또는 신호 발생 시 success
        success = last is not None and (
            last.trend_strength in {"strong", "moderate"} and last.signal in {"long", "short"}
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
                "trend_strength": last.trend_strength if last else "none",
                "direction": last.direction if last else "neutral",
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

    def _calculate_adx(self, candles: List[Candle]) -> List[ADXSnapshot]:
        snapshots: List[ADXSnapshot] = []

        # True Range, +DM, -DM 계산
        tr_list: List[float] = []
        plus_dm_list: List[float] = []
        minus_dm_list: List[float] = []

        for i, candle in enumerate(candles):
            if i == 0:
                tr_list.append(candle["high"] - candle["low"])
                plus_dm_list.append(0)
                minus_dm_list.append(0)
                continue

            prev = candles[i - 1]
            high_diff = candle["high"] - prev["high"]
            low_diff = prev["low"] - candle["low"]

            tr = max(
                candle["high"] - candle["low"],
                abs(candle["high"] - prev["close"]),
                abs(candle["low"] - prev["close"]),
            )
            tr_list.append(tr)

            plus_dm = high_diff if high_diff > low_diff and high_diff > 0 else 0
            minus_dm = low_diff if low_diff > high_diff and low_diff > 0 else 0
            plus_dm_list.append(plus_dm)
            minus_dm_list.append(minus_dm)

        # Smoothed TR, +DM, -DM 및 DI 계산
        smoothed_tr: List[Optional[float]] = [None] * len(candles)
        smoothed_plus_dm: List[Optional[float]] = [None] * len(candles)
        smoothed_minus_dm: List[Optional[float]] = [None] * len(candles)
        plus_di_list: List[Optional[float]] = [None] * len(candles)
        minus_di_list: List[Optional[float]] = [None] * len(candles)
        dx_list: List[Optional[float]] = [None] * len(candles)

        for i in range(len(candles)):
            if i + 1 < self.period:
                continue

            if i + 1 == self.period:
                smoothed_tr[i] = sum(tr_list[: self.period])
                smoothed_plus_dm[i] = sum(plus_dm_list[: self.period])
                smoothed_minus_dm[i] = sum(minus_dm_list[: self.period])
            else:
                prev_str = smoothed_tr[i - 1] or 0
                prev_spdm = smoothed_plus_dm[i - 1] or 0
                prev_smdm = smoothed_minus_dm[i - 1] or 0

                smoothed_tr[i] = prev_str - (prev_str / self.period) + tr_list[i]
                smoothed_plus_dm[i] = prev_spdm - (prev_spdm / self.period) + plus_dm_list[i]
                smoothed_minus_dm[i] = prev_smdm - (prev_smdm / self.period) + minus_dm_list[i]

            str_val = smoothed_tr[i] or 1
            plus_di_list[i] = (smoothed_plus_dm[i] or 0) / str_val * 100
            minus_di_list[i] = (smoothed_minus_dm[i] or 0) / str_val * 100

            pdi = plus_di_list[i] or 0
            mdi = minus_di_list[i] or 0
            di_sum = pdi + mdi
            dx_list[i] = abs(pdi - mdi) / di_sum * 100 if di_sum > 0 else 0

        # ADX 계산
        adx_list: List[Optional[float]] = [None] * len(candles)

        for i in range(len(candles)):
            if i + 1 < self.period * 2:
                continue

            if i + 1 == self.period * 2:
                valid_dx = [
                    dx for dx in dx_list[self.period : self.period * 2] if dx is not None
                ]
                adx_list[i] = mean(valid_dx) if valid_dx else None
            else:
                prev_adx = adx_list[i - 1]
                if prev_adx is not None and dx_list[i] is not None:
                    adx_list[i] = (prev_adx * (self.period - 1) + dx_list[i]) / self.period

        # 스냅샷 생성
        for i, candle in enumerate(candles):
            pdi = plus_di_list[i]
            mdi = minus_di_list[i]
            adx = adx_list[i]

            trend_strength: Literal["strong", "moderate", "weak", "none"] = "none"
            if adx is not None:
                if adx >= self.strong_threshold:
                    trend_strength = "strong"
                elif adx >= self.weak_threshold:
                    trend_strength = "moderate"
                else:
                    trend_strength = "weak"

            direction: Literal["bullish", "bearish", "neutral"] = "neutral"
            if pdi is not None and mdi is not None:
                if pdi > mdi:
                    direction = "bullish"
                elif mdi > pdi:
                    direction = "bearish"

            signal: Optional[Literal["long", "short", "exit"]] = None
            if adx is not None and adx >= self.weak_threshold:
                if direction == "bullish":
                    signal = "long"
                elif direction == "bearish":
                    signal = "short"
            elif adx is not None and adx < self.weak_threshold:
                signal = "exit"

            snapshots.append(
                ADXSnapshot(
                    date=candle["date"],
                    close=candle["close"],
                    plus_di=round(pdi, 2) if pdi is not None else None,
                    minus_di=round(mdi, 2) if mdi is not None else None,
                    adx=round(adx, 2) if adx is not None else None,
                    trend_strength=trend_strength,
                    direction=direction,
                    signal=signal,
                )
            )

        return snapshots


__all__ = ["FuturesADX", "FuturesADXParams"]
