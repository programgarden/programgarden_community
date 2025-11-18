from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Literal, Optional, TypedDict

from programgarden_core import (
    BaseStrategyConditionResponseOverseasFuturesType,
    BaseStrategyConditionOverseasFutures
)
from programgarden_finance import LS, o3108
from pydantic import BaseModel, Field


class Candle(TypedDict):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class OscillatorSnapshot:
    date: str
    close: float
    rsi: Optional[float]
    stoch_k: Optional[float]
    stoch_d: Optional[float]
    signal: Literal["overbought", "oversold", "neutral"]


class FuturesRSIStochasticParams(BaseModel):
    """FuturesRSIStochastic 전략 파라미터 정의"""

    rsi_period: int = Field(
        14,
        title="RSI 기간",
        description=(
            "최근 N개 종가의 상승폭과 하락폭을 비교해 상대 강도를 계산합니다."
            " 숫자를 줄이면 가격 변화를 빠르게 캐치하고, 키우면 노이즈가 줄어드니 전략 성향에 맞춰 조절하세요."
            " 최소 2보다 크게 입력해야 합니다."
        ),
        gt=1,
        json_schema_extra={"example": 14},
    )
    stoch_k: int = Field(
        14,
        title="스토캐스틱 %K 기간",
        description=(
            "가장 최근 N개 봉의 고점·저점 범위 안에서 현재 종가가 어느 위치인지 백분율로 표현합니다."
            " 값이 작으면 단기 미세 진동을 더 민감하게, 크게 잡으면 추세형 신호만 남깁니다."
        ),
        gt=1,
        json_schema_extra={"example": 14},
    )
    stoch_d: int = Field(
        3,
        title="스토캐스틱 %D 기간",
        description=(
            "%K 값을 다시 한번 단순 이동평균으로 완만하게 만든 선입니다."
            " 기간을 늘릴수록 잦은 교차를 줄이고, 줄이면 즉각적인 꺾임을 빠르게 포착합니다."
        ),
        gt=0,
        json_schema_extra={"example": 3},
    )
    stoch_smooth: int = Field(
        3,
        title="%K 평활화",
        description=(
            "원시 %K를 계산하기 전에 몇 개의 값을 묶어 평균 낼지 정하는 평활화 구간입니다."
            " 작은 값은 민감하지만 요동이 크고, 큰 값은 신호가 적은 대신 안정적입니다."
        ),
        gt=0,
        json_schema_extra={"example": 3},
    )
    overbought: float = Field(
        70.0,
        title="과매수 임계값",
        description=(
            "RSI와 %D가 동시에 이 수준을 넘으면 시장이 과열됐다고 판단합니다."
            " 너무 높게 잡으면 시그널이 드물고, 낮추면 더 민감하게 차익청산 타이밍을 알려줍니다."
        ),
        ge=0,
        le=100,
        json_schema_extra={"example": 70},
    )
    oversold: float = Field(
        30.0,
        title="과매도 임계값",
        description=(
            "RSI와 %D가 동시에 이 값 아래로 내려오면 침체 구간으로 보고 반등 가능성을 살핍니다."
            " 숫자를 낮출수록 드문 강력 반등만 골라내고, 높이면 더 잦은 저점 매수 기회를 줍니다."
        ),
        ge=0,
        le=100,
        json_schema_extra={"example": 30},
    )
    timeframe: Literal["days", "weeks", "months"] = Field(
        "days",
        title="시간 단위",
        description=(
            "일봉·주봉·월봉 중 어떤 캔들을 불러올지 선택합니다."
            " 일봉은 단기 신호, 주·월봉은 큰 추세 변화를 확인할 때 유용합니다."
        ),
    )
    qrycnt: int = Field(
        200,
        title="조회 캔들 수",
        description=(
            "얼마나 긴 히스토리를 불러올지 결정합니다."
            " 값을 크게 잡으면 지표가 안정적이고, 작게 잡으면 최신 흐름에 더 집중합니다."
            " 최소 필요 길이보다 작게 입력해도 내부에서 자동으로 보정합니다."
        ),
        gt=0,
        json_schema_extra={"example": 200},
    )
    appkey: Optional[str] = Field(
        None,
        title="LS증권 앱키",
        description="LS증권 Open API에서 발급받은 앱키입니다. 실계좌·모의계좌 중 사용하는 환경에 맞는 키를 넣어주세요.",
    )
    appsecretkey: Optional[str] = Field(
        None,
        title="LS증권 앱시크릿",
        description="앱키와 한 쌍으로 쓰는 시크릿 값입니다. 외부에 노출되지 않도록 안전한 저장소에 보관해 주세요.",
    )


class FuturesRSIStochastic(BaseStrategyConditionOverseasFutures):
    """RSI와 스토캐스틱 슬로우를 함께 활용해 과매수·과매도 구간을 탐지합니다."""

    id: str = "FuturesRSIStochastic"
    name: str = "해외선물 RSI·스토캐스틱 과열 감시"
    description: str = (
        "RSI가 나타내는 상대 강도와 스토캐스틱 슬로우의 위치를 동시에 비교해 "
        "과열·침체 구간이 겹치는 순간을 빠르게 찾아냅니다."
    )
    securities: List[str] = ["LS"]
    weight: float = 0.5
    parameter_schema: Dict[str, object] = FuturesRSIStochasticParams.model_json_schema()

    def __init__(
        self,
        *,
        rsi_period: int = 14,
        stoch_k: int = 14,
        stoch_d: int = 3,
        stoch_smooth: int = 3,
        overbought: float = 70.0,
        oversold: float = 30.0,
        timeframe: Literal["days", "weeks", "months"] = "days",
        qrycnt: int = 200,
        appkey: Optional[str] = None,
        appsecretkey: Optional[str] = None,
    ) -> None:
        super().__init__()
        if rsi_period <= 1:
            raise ValueError("rsi_period must be greater than 1")
        if stoch_k <= 1:
            raise ValueError("stoch_k must be greater than 1")
        if stoch_d <= 0 or stoch_smooth <= 0:
            raise ValueError("stoch_d and stoch_smooth must be positive")
        self.rsi_period = rsi_period
        self.stoch_k = stoch_k
        self.stoch_d = stoch_d
        self.stoch_smooth = stoch_smooth
        self.overbought = overbought
        self.oversold = oversold
        self.timeframe = timeframe
        min_required = rsi_period + stoch_k + stoch_d + stoch_smooth + 5
        self.qrycnt = max(qrycnt, min_required)
        self.appkey = appkey
        self.appsecretkey = appsecretkey
        self._ls = LS.get_instance()

    async def execute(self) -> BaseStrategyConditionResponseOverseasFuturesType:
        """Execute the strategy and return the structured signal payload.

        Returns:
                BaseStrategyConditionResponseOverseasFuturesType: Dictionary with the
                following keys:

                - ``condition_id`` / ``description``: Identify which strategy produced
                    this response and include the human-readable summary.
                - ``success``: ``True`` if the latest snapshot detected an
                    ``overbought`` or ``oversold`` signal, otherwise ``False``.
                - ``symbol`` / ``exchcd``: The instrument code and exchange set via
                    ``_set_symbol`` (empty string when unset).
                - ``product``: Product group inherited from
                    ``BaseStrategyConditionOverseasFutures``.
                - ``position_side``: ``long`` when the last signal is ``oversold``,
                    ``short`` for ``overbought``, otherwise ``flat``.
                - ``weight``: Relative weight (default 0.5) for portfolio blending.
                - ``data``: Nested dictionary with ``signals`` (per-candle
                    snapshots), ``last_signal`` (latest label), and ``signal_events``
                    (recent non-neutral signals).
        """
        candles = await self._load_candles()
        snapshots = self._calculate_indicators(candles)
        last_signal = snapshots[-1].signal if snapshots else "neutral"
        success = last_signal in {"overbought", "oversold"}
        highlighted = [snap for snap in snapshots if snap.signal != "neutral"]
        data = {
                "signals": [asdict(item) for item in snapshots],
                "last_signal": last_signal,
                "signal_events": [asdict(item) for item in highlighted[-self.stoch_d :]],
        }
        position_side = "flat"
        if last_signal == "oversold":
                position_side = "long"
        elif last_signal == "overbought":
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
            "weight": self.weight,
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

    def _calculate_indicators(self, candles: List[Candle]) -> List[OscillatorSnapshot]:
        closes = [row["close"] for row in candles]
        highs = [row["high"] for row in candles]
        lows = [row["low"] for row in candles]

        rsi_values = self._compute_rsi(closes)
        stoch_k_values = self._compute_stochastic(highs, lows, closes)
        stoch_d_values = self._sma(stoch_k_values, self.stoch_d)

        snapshots: List[OscillatorSnapshot] = []
        for idx, candle in enumerate(candles):
            rsi = rsi_values[idx]
            stoch_k_val = stoch_k_values[idx]
            stoch_d_val = stoch_d_values[idx]
            signal: Literal["overbought", "oversold", "neutral"] = "neutral"
            if rsi is not None and stoch_d_val is not None:
                if rsi >= self.overbought and stoch_d_val >= self.overbought:
                    signal = "overbought"
                elif rsi <= self.oversold and stoch_d_val <= self.oversold:
                    signal = "oversold"
            snapshots.append(
                OscillatorSnapshot(
                    date=candle["date"],
                    close=candle["close"],
                    rsi=rsi,
                    stoch_k=stoch_k_val,
                    stoch_d=stoch_d_val,
                    signal=signal,
                )
            )
        return snapshots

    def _compute_rsi(self, closes: List[float]) -> List[Optional[float]]:
        period = self.rsi_period
        rsi_values: List[Optional[float]] = [None] * len(closes)
        gains: List[float] = [0.0]
        losses: List[float] = [0.0]
        for idx in range(1, len(closes)):
            delta = closes[idx] - closes[idx - 1]
            gains.append(max(delta, 0.0))
            losses.append(abs(min(delta, 0.0)))
        if len(closes) <= period:
            return rsi_values
        avg_gain = sum(gains[1 : period + 1]) / period
        avg_loss = sum(losses[1 : period + 1]) / period
        rsi_values[period] = self._rsi_from_averages(avg_gain, avg_loss)
        for idx in range(period + 1, len(closes)):
            gain = gains[idx]
            loss = losses[idx]
            avg_gain = ((avg_gain * (period - 1)) + gain) / period
            avg_loss = ((avg_loss * (period - 1)) + loss) / period
            rsi_values[idx] = self._rsi_from_averages(avg_gain, avg_loss)
        return rsi_values

    def _rsi_from_averages(self, avg_gain: float, avg_loss: float) -> float:
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss if avg_loss else 0.0
        return 100.0 - (100.0 / (1.0 + rs))

    def _compute_stochastic(self, highs: List[float], lows: List[float], closes: List[float]) -> List[Optional[float]]:
        period = self.stoch_k
        raw_k: List[Optional[float]] = [None] * len(closes)
        for idx in range(len(closes)):
            if idx + 1 < period:
                continue
            window_high = max(highs[idx + 1 - period : idx + 1])
            window_low = min(lows[idx + 1 - period : idx + 1])
            if window_high == window_low:
                raw_k[idx] = raw_k[idx - 1] if idx > 0 else 50.0
            else:
                raw_k[idx] = ((closes[idx] - window_low) / (window_high - window_low)) * 100
        smoothed_k = self._sma(raw_k, self.stoch_smooth)
        return smoothed_k

    def _sma(self, values: List[Optional[float]], period: int) -> List[Optional[float]]:
        if period <= 1:
            return values
        sma_values: List[Optional[float]] = [None] * len(values)
        buffer: List[float] = []
        for idx, value in enumerate(values):
            if value is None:
                buffer.clear()
                continue
            buffer.append(value)
            if len(buffer) < period:
                continue
            sma_values[idx] = sum(buffer[-period:]) / period
        return sma_values


__all__ = ["FuturesRSIStochastic"]
