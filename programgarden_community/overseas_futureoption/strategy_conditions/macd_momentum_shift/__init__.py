from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from statistics import mean
from typing import Dict, List, Literal, Optional, TypedDict

from programgarden_core import (
    BaseStrategyConditionOverseasFutures,
    BaseStrategyConditionResponseOverseasFuturesType,
)
from programgarden_finance import LS, o3108
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

class FuturesMACDShiftParams(BaseModel):
    """FuturesMACDShift 전략 파라미터 정의"""

    fast_period: int = Field(
        5,
        title="MACD 단기 EMA 기간",
        description=(
            "최근 가격 변화를 얼마나 민감하게 따라갈지 결정합니다. 숫자가 작을수록 더 빠르게 반응하고, "
            "값을 키우면 노이즈를 줄이는 대신 반응이 느려집니다."
        ),
        gt=0,
        json_schema_extra={"example": 12},
    )

    slow_period: int = Field(
        10,
        title="MACD 장기 EMA 기간",
        description=(
            "큰 흐름을 잡는 기준선입니다. 단기 EMA보다 길어야 하며, 값을 늘릴수록 더욱 완만한 추세선을 얻을 수 있습니다."
        ),
        gt=0,
        json_schema_extra={"example": 26},
    )

    signal_period: int = Field(
        9,
        title="시그널 EMA 기간",
        description=(
            "MACD 선을 한 번 더 매끈하게 만든 값으로, 작게 설정하면 교차 신호가 잦아지고 크게 설정하면 중요한 교차만 포착합니다."
        ),
        gt=0,
        json_schema_extra={"example": 9},
    )

    lookback: int = Field(
        4,
        title="교차 탐지 구간",
        description=(
            "최근 MACD가 시그널선을 위/아래로 통과한 사건을 몇 개까지 기억할지 정합니다. "
            "값이 1이면 가장 마지막 교차만 보고, 3이면 최근 3번의 골든/데드 크로스를 모아 흐름을 판단합니다."
        ),
        gt=0,
        json_schema_extra={"example": 4},
    )

    timeframe: Literal["days", "weeks", "months"] = Field(
        "days",
        title="시간 단위",
        description="어떤 봉 데이터를 가져올지 선택합니다 (days=일봉, weeks=주봉, months=월봉). 주/월봉을 고르면 같은 기간이어도 훨씬 부드러운 흐름을 확인할 수 있습니다.",
    )

    qrycnt: int = Field(
        250,
        title="조회 캔들 수",
        description=(
            "최근 몇 개의 봉을 불러올지 지정합니다. 값이 클수록 더 긴 히스토리를 확보해 신뢰도 높은 MACD를 계산할 수 있지만 조회 시간이 늘어납니다."
        ),
        gt=0,
        json_schema_extra={"example": 250},
    )

    appkey: Optional[str] = Field(
        None,
        title="LS증권 앱키",
        description="LS증권 API 발급받은 앱키를 입력합니다. 실거래 계정과 연결된 키여야 데이터 조회가 가능합니다.",
        json_schema_extra={"example": "your-app-key"},
    )

    appsecretkey: Optional[str] = Field(
        None,
        title="LS증권 앱시크릿키",
        description="앱키와 짝을 이루는 LS증권 시크릿키입니다. 외부에 노출되지 않도록 안전하게 보관해 주세요.",
        json_schema_extra={"example": "your-secret-key"},
    )

    @model_validator(mode="after")
    def validate_periods(self) -> FuturesMACDShiftParams:
        if self.fast_period >= self.slow_period:
            raise ValueError("fast_period는 slow_period보다 작아야 합니다")
        return self


class FuturesMACDShift(BaseStrategyConditionOverseasFutures):
    """MACD 교차와 히스토그램 방향성을 통해 해외선물 모멘텀 변화를 포착합니다."""

    id: str = "FuturesMACDShift"
    name: str = "해외선물 MACD 모멘텀 전환"
    description: str = (
        "이 전략은 최근 가격과 과거 평균 가격의 간격(MACD)을 통해 매수/매도 에너지가 어느 쪽으로 강해지는지 쉽게 보여주고, "
        "MACD선이 시그널선을 위·아래로 교차할 때와 히스토그램이 양(+)·음(-)으로 바뀌는 흐름을 함께 확인해 해외선물이 추세를 "
        "바꿀 가능성이 커지는 지점을 조기에 알려줍니다. 초보 투자자도 '지금은 상승(롱) 쪽 힘이 센지, 하락(숏) 쪽이 우세한지'를 "
        "한눈에 파악해 진입·청산 타이밍을 단순하게 결정할 수 있도록 설계된 모멘텀 추적 전략입니다."
    )
    securities = ["LS"]
    parameter_schema: dict = FuturesMACDShiftParams.model_json_schema()


    def __init__(
        self,
        *,
        fast_period: int = 5,
        slow_period: int = 10,
        signal_period: int = 9,
        lookback: int = 4,
        timeframe: Literal["days", "weeks", "months"] = "days",
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
        self.qrycnt = max(qrycnt, slow_period + signal_period + lookback + 5)
        self.appkey = appkey
        self.appsecretkey = appsecretkey
        self._ls = LS.get_instance()

    async def execute(self) -> BaseStrategyConditionResponseOverseasFuturesType:
        # LS API 또는 외부 입력으로부터 가격 데이터를 확보
        candles = await self._load_candles()
      
        # MACD/시그널/히스토그램 값과 교차 여부를 스냅샷으로 계산
        snapshots = self._calculate_macd(candles)
        cross_events = [
            snap for snap in snapshots if snap.crossover in {"bullish", "bearish"}
        ]
        recent_cross = cross_events[-self.lookback :] if self.lookback else []
        success = bool(recent_cross)
        last_cross = recent_cross[-1].crossover if recent_cross else "none"
        data = {
            "signals": [asdict(item) for item in snapshots],
            "recent_crossovers": [asdict(item) for item in recent_cross],
            "last_crossover": last_cross,
        }
        # 최근 교차 방향에 따라 포지션 시나리오를 단순화
        position_side = "flat"
        if last_cross == "bullish":
            position_side = "long"
        elif last_cross == "bearish":
            position_side = "short"
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
            "weight": 0.5,
            "data": data,
        }
    

    async def _load_candles(self) -> List[Candle]:
        if not self.appkey or not self.appsecretkey:
            raise ValueError("LS 데이터를 조회하려면 appkey와 appsecretkey가 필요합니다")
        if not self.symbol:
            raise ValueError("Symbol information is required for LS data fetch")

        await self._ensure_login()

        shcode = self.symbol.get("symbol")
        if not shcode:
            raise ValueError("Symbol code is missing")

        gubun_map: Dict[str, str] = {"days": "0", "weeks": "1", "months": "2"}
        gubun = gubun_map.get(self.timeframe)
        if gubun is None:
            raise ValueError("timeframe must be one of days, weeks, months")

        # 조회 기간은 가장 최근 거래일 기준으로 자동 계산 (qrycnt 기반 히스토리 확보)
        today = datetime.utcnow()
        end_date = today.strftime("%Y%m%d")
        start_date = (today - timedelta(days=self.qrycnt * 2)).strftime("%Y%m%d")

        request = self._ls.overseas_futureoption().chart().o3108(
            body=o3108.O3108InBlock(
                shcode=shcode,
                gubun=gubun,
                qrycnt=self.qrycnt,
                sdate=start_date,
                edate=end_date,
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
                        open=block.open,
                        high=block.high,
                        low=block.low,
                        close=block.close,
                        volume=block.volume,
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
        # 종가 배열을 기반으로 MACD 핵심 구성 요소를 순차 계산
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
            # 이전 구간 대비 MACD와 시그널의 상대 위치 변화를 통해 교차 방향 결정
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

    def _ema_series(self, values: List[float], period: int) -> List[Optional[float]]:
        ema_values: List[Optional[float]] = []
        multiplier = 2 / (period + 1)
        for idx, price in enumerate(values):
            if idx + 1 < period:
                # 초기 구간은 EMA 계산을 위해 누락 처리
                ema_values.append(None)
                continue
            if idx + 1 == period:
                # 첫 EMA seed는 단순 이동평균으로 설정
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
                # 충분한 길이가 쌓일 때까지 버퍼를 사용해 초기 EMA 값을 정의
                ema_prev = sum(buffer[-period:]) / period
            else:
                ema_prev = (value - ema_prev) * multiplier + ema_prev
            ema_values[idx] = ema_prev
        return ema_values


__all__ = ["FuturesMACDShift"]
