from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from statistics import mean
from typing import Dict, List, Literal, Optional, TypedDict

from programgarden_core import (
    BaseStrategyConditionOverseasStock,
    BaseStrategyConditionResponseOverseasStockType,
)
from programgarden_finance import LS, g3204
from pydantic import BaseModel, Field, model_validator


class Candle(TypedDict):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class MACDSnapshot:
    date: str
    close: float
    macd: Optional[float]
    signal: Optional[float]
    histogram: Optional[float]
    crossover: Literal["bullish", "bearish", "none"]


class StockMACDShiftParams(BaseModel):
    """StockMACDShift 전략 파라미터 정의"""

    fast_period: int = Field(
        5,
        title="MACD 단기 EMA 기간",
        description=(
            "최근 해외주식 가격 변화를 얼마나 민감하게 추적할지 결정합니다. "
            "값이 작으면 즉각 반응하지만 노이즈가 늘고, 크게 잡으면 신호가 깔끔해집니다."
        ),
        gt=0,
        json_schema_extra={"example": 12},
    )

    slow_period: int = Field(
        10,
        title="MACD 장기 EMA 기간",
        description=(
            "전체 추세를 잡는 기준선입니다. 단기 EMA보다 길어야 하며, 값이 커질수록 "
            "해외주식의 큰 흐름만 남고 단기진동이 줄어듭니다."
        ),
        gt=0,
        json_schema_extra={"example": 26},
    )

    signal_period: int = Field(
        9,
        title="시그널 EMA 기간",
        description=(
            "MACD 선을 다시 한 번 다듬어 교차 신호를 확인하는 용도입니다. "
            "짧으면 잦은 교차를, 길면 확실한 전환만 포착합니다."
        ),
        gt=0,
        json_schema_extra={"example": 9},
    )

    lookback: int = Field(
        4,
        title="교차 탐지 구간",
        description=(
            "최근 MACD가 시그널선을 통과한 사건을 몇 개까지 기록할지 지정합니다. "
            "1이면 가장 마지막 교차만, 3이면 최근 3번의 골든/데드 크로스를 모읍니다."
        ),
        gt=0,
        json_schema_extra={"example": 4},
    )

    timeframe: Literal["days", "weeks", "months"] = Field(
        "days",
        title="시간 단위",
        description=(
            "어떤 봉 데이터를 가져올지 선택합니다 (days=일봉, weeks=주봉, months=월봉). "
            "주/월봉을 고르면 해외주식의 큰 물결을 확인할 수 있습니다."
        ),
    )

    focus_direction: Literal["bullish", "bearish", "both"] = Field(
        "both",
        title="관심 교차 방향",
        description=(
            "골든 크로스(상승)만 볼지, 데드 크로스(하락)만 볼지, 혹은 둘 다 볼지 선택합니다. "
            "기본값은 both이며 기존 동작과 동일하게 모든 교차를 기록합니다."
        ),
        json_schema_extra={"example": "bullish"},
    )

    qrycnt: int = Field(
        250,
        title="조회 캔들 수",
        description=(
            "최근 몇 개의 봉을 불러올지 결정합니다. 길게 잡을수록 EMA 초기화가 안정적이며, "
            "짧게 잡으면 최신 흐름에 더 민감해집니다."
        ),
        gt=0,
        json_schema_extra={"example": 250},
    )

    appkey: Optional[str] = Field(
        None,
        title="LS증권 앱키",
        description="LS증권 해외주식 Open API 인증용 앱키입니다.",
        json_schema_extra={"example": "your-app-key"},
    )

    appsecretkey: Optional[str] = Field(
        None,
        title="LS증권 앱시크릿",
        description="LS증권 해외주식 Open API 앱시크릿입니다.",
        json_schema_extra={"example": "your-secret-key"},
    )

    @model_validator(mode="after")
    def validate_periods(self) -> "StockMACDShiftParams":
        if self.fast_period >= self.slow_period:
            raise ValueError("fast_period는 slow_period보다 작아야 합니다")
        return self


class StockMACDShift(BaseStrategyConditionOverseasStock):
    """MACD 교차와 히스토그램 방향성으로 해외주식 모멘텀 전환을 탐지합니다."""

    id: str = "StockMACDShift"
    name: str = "해외주식 MACD 모멘텀 전환"
    description: str = (
        "해외주식의 빠른 EMA와 느린 EMA 간 간격(MACD)과 히스토그램 방향을 함께 비교해 "
        "매수/매도 에너지가 어느 쪽으로 이동 중인지 직관적으로 보여줍니다."
    )
    securities = ["LS"]
    weight: float = 0.5
    parameter_schema: Dict[str, object] = StockMACDShiftParams.model_json_schema()

    def __init__(
        self,
        *,
        fast_period: int = 5,
        slow_period: int = 10,
        signal_period: int = 9,
        lookback: int = 4,
        timeframe: Literal["days", "weeks", "months"] = "days",
        focus_direction: Literal["bullish", "bearish", "both"] = "both",
        qrycnt: int = 250,
        appkey: Optional[str] = None,
        appsecretkey: Optional[str] = None,
    ) -> None:
        super().__init__()
        if fast_period <= 0 or slow_period <= 0 or signal_period <= 0:
            raise ValueError("All MACD periods must be positive")
        if fast_period >= slow_period:
            raise ValueError("fast_period must be smaller than slow_period")
        if lookback <= 0:
            raise ValueError("lookback must be positive")
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.signal_period = signal_period
        self.lookback = lookback
        self.timeframe = timeframe
        self.focus_direction = focus_direction
        self.qrycnt = max(qrycnt, slow_period + signal_period + lookback + 5)
        self.appkey = appkey
        self.appsecretkey = appsecretkey
        self._ls = LS.get_instance()

    async def execute(self) -> BaseStrategyConditionResponseOverseasStockType:
        candles = await self._load_candles()
        snapshots = self._calculate_macd(candles)
        cross_events = [snap for snap in snapshots if snap.crossover in {"bullish", "bearish"}]
        filtered_events = [snap for snap in cross_events if self._matches_focus(snap.crossover)]
        recent_cross = filtered_events[-self.lookback :] if self.lookback else []
        success = bool(recent_cross)
        last_cross = recent_cross[-1].crossover if recent_cross else "none"
        symbol = self.symbol.get("symbol", "") if self.symbol else ""
        exchcd = self.symbol.get("exchcd", "") if self.symbol else ""
        data = {
            "signals": [asdict(item) for item in snapshots],
            "recent_crossovers": [asdict(item) for item in recent_cross],
            "last_crossover": last_cross,
            "focus_direction": self.focus_direction,
        }
        return {
            "condition_id": self.id,
            "description": self.description,
            "success": success,
            "symbol": symbol,
            "exchcd": exchcd,
            "product": self.product_type,
            "weight": self.weight,
            "data": data,
        }

    async def _load_candles(self) -> List[Candle]:
        if not self.appkey or not self.appsecretkey:
            raise ValueError("LS 데이터를 조회하려면 appkey와 appsecretkey가 필요합니다")
        if not self.symbol:
            raise ValueError("Symbol information is required for LS data fetch")

        await self._ensure_login()

        exchcd = self.symbol.get("exchcd") if self.symbol else None
        symbol = self.symbol.get("symbol") if self.symbol else None
        if not exchcd or not symbol:
            raise ValueError("Both exchcd and symbol must be provided")

        gubun_map: Dict[str, str] = {"days": "2", "weeks": "3", "months": "4"}
        gubun = gubun_map.get(self.timeframe)
        if gubun is None:
            raise ValueError("timeframe must be one of days, weeks, months")

        today = datetime.utcnow()
        end_date = today.strftime("%Y%m%d")
        start_date = (today - timedelta(days=self.qrycnt * 2)).strftime("%Y%m%d")

        request = self._ls.overseas_stock().chart().g3204(
            g3204.G3204InBlock(
                sdate=start_date,
                edate=end_date,
                keysymbol=f"{exchcd}{symbol}",
                exchcd=exchcd,
                symbol=symbol,
                gubun=gubun,
                qrycnt=self.qrycnt,
            )
        )

        responses = await request.occurs_req_async()
        candles: List[Candle] = []
        for response in responses:
            for block in response.block1:
                candles.append(
                    Candle(
                        date=block.date,
                        open=block.open,
                        high=block.high,
                        low=block.low,
                        close=block.close,
                        volume=getattr(block, "volume", 0),
                    )
                )
        candles.sort(key=lambda item: item["date"])
        return candles

    async def _ensure_login(self) -> None:
        token_manager = self._ls.token_manager
        if token_manager and token_manager.is_token_available():
            await self._ls.async_ensure_token()
            return
        await self._ls.async_login(self.appkey, self.appsecretkey)

    def _calculate_macd(self, candles: List[Candle]) -> List[MACDSnapshot]:
        closes = [row["close"] for row in candles]
        fast_ema = self._ema_series(closes, self.fast_period)
        slow_ema = self._ema_series(closes, self.slow_period)
        macd_line: List[Optional[float]] = []
        for fast, slow in zip(fast_ema, slow_ema):
            if fast is None or slow is None:
                macd_line.append(None)
            else:
                macd_line.append(fast - slow)
        signal_line = self._ema_optional(macd_line, self.signal_period)
        histogram: List[Optional[float]] = []
        for macd_value, signal_value in zip(macd_line, signal_line):
            if macd_value is None or signal_value is None:
                histogram.append(None)
            else:
                histogram.append(macd_value - signal_value)

        snapshots: List[MACDSnapshot] = []
        for idx, candle in enumerate(candles):
            macd_value = macd_line[idx]
            signal_value = signal_line[idx]
            hist_value = histogram[idx]
            crossover: Literal["bullish", "bearish", "none"] = "none"
            if macd_value is not None and signal_value is not None and idx > 0:
                prev_macd = macd_line[idx - 1]
                prev_signal = signal_line[idx - 1]
                if prev_macd is not None and prev_signal is not None:
                    if prev_macd <= prev_signal and macd_value > signal_value:
                        crossover = "bullish"
                    elif prev_macd >= prev_signal and macd_value < signal_value:
                        crossover = "bearish"
            snapshots.append(
                MACDSnapshot(
                    date=candle["date"],
                    close=candle["close"],
                    macd=macd_value,
                    signal=signal_value,
                    histogram=hist_value,
                    crossover=crossover,
                )
            )
        return snapshots

    def _matches_focus(self, crossover: Literal["bullish", "bearish", "none"]) -> bool:
        if crossover not in {"bullish", "bearish"}:
            return False
        if self.focus_direction == "both":
            return True
        return crossover == self.focus_direction

    def _ema_series(self, values: List[float], period: int) -> List[Optional[float]]:
        ema_values: List[Optional[float]] = []
        multiplier = 2 / (period + 1)
        for idx, price in enumerate(values):
            if idx + 1 < period:
                ema_values.append(None)
                continue
            if idx + 1 == period:
                ema_values.append(mean(values[:period]))
                continue
            prev = ema_values[-1]
            if prev is None:
                prev = mean(values[idx + 1 - period : idx + 1])
            ema_values.append((price - prev) * multiplier + prev)
        return ema_values

    def _ema_optional(self, values: List[Optional[float]], period: int) -> List[Optional[float]]:
        ema_values: List[Optional[float]] = [None] * len(values)
        multiplier = 2 / (period + 1)
        ema_prev: Optional[float] = None
        buffer: List[float] = []
        for idx, value in enumerate(values):
            if value is None:
                continue
            buffer.append(value)
            if ema_prev is None:
                if len(buffer) < period:
                    continue
                ema_prev = sum(buffer[-period:]) / period
            else:
                ema_prev = (value - ema_prev) * multiplier + ema_prev
            ema_values[idx] = ema_prev
        return ema_values


__all__ = ["StockMACDShift", "StockMACDShiftParams"]
