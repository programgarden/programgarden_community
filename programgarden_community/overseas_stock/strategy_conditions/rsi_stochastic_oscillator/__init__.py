from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Literal, Optional, TypedDict

from programgarden_core import (
    BaseStrategyConditionOverseasStock,
    BaseStrategyConditionResponseOverseasStockType,
)
from programgarden_finance import LS, g3204
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


class StockRSIStochasticParams(BaseModel):
    """StockRSIStochastic 전략 파라미터 정의"""

    rsi_period: int = Field(
        14,
        title="RSI 기간",
        description=(
            "최근 N개 종가의 상승폭과 하락폭을 비교해 해외주식의 상대 강도를 계산합니다. "
            "숫자를 줄이면 민감해지고, 키우면 노이즈가 줄어듭니다. 최소 2보다 크게 입력하세요."
        ),
        gt=1,
        json_schema_extra={"example": 14},
    )
    stoch_k: int = Field(
        14,
        title="스토캐스틱 %K 기간",
        description=(
            "최근 N개 봉 범위 안에서 현재 종가가 어느 위치인지 0~100 사이로 보여줍니다. "
            "짧으면 미세 진동을 민감하게, 길면 추세형 신호만 남깁니다."
        ),
        gt=1,
        json_schema_extra={"example": 14},
    )
    stoch_d: int = Field(
        3,
        title="스토캐스틱 %D 기간",
        description=(
            "%K를 한 번 더 평균 내서 완만하게 만든 선입니다. 기간을 늘릴수록 잦은 교차를 줄입니다."
        ),
        gt=0,
        json_schema_extra={"example": 3},
    )
    stoch_smooth: int = Field(
        3,
        title="%K 평활화",
        description=(
            "원시 %K를 계산하기 전에 몇 개씩 묶어 평균낼지 정합니다. 작으면 민감하지만 요동이 크고, "
            "크면 안정적인 대신 신호가 느립니다."
        ),
        gt=0,
        json_schema_extra={"example": 3},
    )
    overbought: float = Field(
        70.0,
        title="과매수 임계값",
        description=(
            "RSI와 %D가 동시에 이 수준을 넘으면 과열로 판단합니다. 값을 낮추면 더 잦은 경고를 받습니다."
        ),
        ge=0,
        le=100,
        json_schema_extra={"example": 70},
    )
    oversold: float = Field(
        30.0,
        title="과매도 임계값",
        description=(
            "RSI와 %D가 동시에 이 값 아래로 내려오면 침체로 판단해 반등 시나리오를 확인합니다."
        ),
        ge=0,
        le=100,
        json_schema_extra={"example": 30},
    )
    timeframe: Literal["days", "weeks", "months"] = Field(
        "days",
        title="시간 단위",
        description="일봉·주봉·월봉 중 어떤 캔들을 사용할지 선택합니다.",
    )
    focus_signal: Literal["overbought", "oversold", "both"] = Field(
        "both",
        title="관심 신호 방향",
        description=(
            "과매수(overbought)만 추적할지, 과매도(oversold)만 볼지, 아니면 둘 다 확인할지 고릅니다. "
            "기본값 both는 기존 동작과 동일하게 두 신호를 모두 기록합니다."
        ),
        json_schema_extra={"example": "oversold"},
    )
    qrycnt: int = Field(
        200,
        title="조회 캔들 수",
        description=(
            "얼마나 긴 히스토리를 불러올지 결정합니다. 내부적으로 필요한 길이보다 작으면 자동 보정됩니다."
        ),
        gt=0,
        json_schema_extra={"example": 200},
    )
    appkey: Optional[str] = Field(
        None,
        title="LS증권 앱키",
        description="LS증권 해외주식 Open API에서 발급받은 앱키입니다.",
    )
    appsecretkey: Optional[str] = Field(
        None,
        title="LS증권 앱시크릿",
        description="앱키와 한 쌍으로 쓰이는 시크릿입니다.",
    )


class StockRSIStochastic(BaseStrategyConditionOverseasStock):
    """RSI와 스토캐스틱 슬로우로 해외주식 과열·침체 구간을 탐지합니다."""

    id: str = "StockRSIStochastic"
    name: str = "해외주식 RSI·스토캐스틱 과열 감시"
    description: str = (
        "RSI와 스토캐스틱 슬로우 값이 동시에 과열(또는 침체) 영역에 진입하는 순간을 모아, "
        "차익 실현/저점 매수 여부를 객관적으로 판단하도록 돕습니다."
    )
    securities: List[str] = ["LS"]
    weight: float = 0.5
    parameter_schema: Dict[str, object] = StockRSIStochasticParams.model_json_schema()

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
        focus_signal: Literal["overbought", "oversold", "both"] = "both",
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
        self.focus_signal = focus_signal
        min_required = rsi_period + stoch_k + stoch_d + stoch_smooth + 5
        self.qrycnt = max(qrycnt, min_required)
        self.appkey = appkey
        self.appsecretkey = appsecretkey
        self._ls = LS.get_instance()

    async def execute(self) -> BaseStrategyConditionResponseOverseasStockType:
        candles = await self._load_candles()
        snapshots = self._calculate_indicators(candles)
        last_snapshot_signal = snapshots[-1].signal if snapshots else "neutral"
        highlighted = [snap for snap in snapshots if snap.signal != "neutral"]
        focus_events = [snap for snap in highlighted if self._matches_focus(snap.signal)]

        if self.focus_signal == "both":
            last_signal = last_snapshot_signal
            success = last_signal in {"overbought", "oversold"}
            signal_events = [asdict(item) for item in highlighted[-self.stoch_d :]]
        else:
            last_signal = focus_events[-1].signal if focus_events else "neutral"
            success = bool(snapshots) and self._matches_focus(last_snapshot_signal)
            signal_events = [asdict(item) for item in focus_events[-self.stoch_d :]]

        data = {
            "signals": [asdict(item) for item in snapshots],
            "last_signal": last_signal,
            "signal_events": signal_events,
            "focus_signal": self.focus_signal,
        }
        symbol = self.symbol.get("symbol", "") if self.symbol else ""
        exchcd = self.symbol.get("exchcd", "") if self.symbol else ""
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

    def _matches_focus(self, signal: Literal["overbought", "oversold", "neutral"]) -> bool:
        if signal not in {"overbought", "oversold"}:
            return False
        if self.focus_signal == "both":
            return True
        return signal == self.focus_signal

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
        return self._sma(raw_k, self.stoch_smooth)

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


__all__ = ["StockRSIStochastic", "StockRSIStochasticParams"]
