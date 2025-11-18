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
from pydantic import BaseModel, Field


class Candle(TypedDict):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class MovingAverageSnapshot:
    date: str
    close: float
    sma: Optional[float]
    ema: Optional[float]
    signal: Literal["bullish_cross", "bearish_cross", "trend_up", "trend_down", "neutral"]


class FuturesSMAEMACrossParams(BaseModel):
    """사용자가 조정할 수 있는 SMA·EMA 전략 파라미터"""

    period_sma: int = Field(
        55,
        title="느린 SMA 기간",
        description=(
            "가격의 큰 흐름을 보여주는 기준선입니다. 숫자를 늘리면 추세가 더 부드럽게 보이고,"
            " 줄이면 최근 변화를 더 빠르게 반영합니다. 최소 2 이상 값을 입력하세요."
        ),
        gt=1,
        json_schema_extra={"example": 55},
    )
    period_ema: int = Field(
        21,
        title="빠른 EMA 기간",
        description=(
            "신호선을 담당하는 EMA가 최신 가격에 얼마나 민감할지를 정합니다."
            " 값이 작으면 응답이 빠른 대신 노이즈가 커지고, 길면 한 박자 느리지만 안정됩니다."
        ),
        gt=1,
        json_schema_extra={"example": 21},
    )
    lookback: int = Field(
        3,
        title="신호 확인 구간",
        description=(
            "최근 N개의 캔들 안에서 발생한 교차·추세 신호만 골라서 보여 줍니다."
            " 숫자를 늘리면 더 오래된 교차도 남겨 두고, 줄이면 방금 나온 신호만 추적합니다."
        ),
        gt=0,
        json_schema_extra={"example": 3},
    )
    timeframe: Literal["days", "weeks", "months"] = Field(
        "days",
        title="캔들 간격",
        description=(
            "일봉/주봉/월봉 중 어떤 간격으로 데이터를 불러올지 고릅니다."
            " 단타 확인은 일봉, 큰 추세 확인은 주·월봉이 적합합니다."
        ),
    )
    qrycnt: int = Field(
        400,
        title="조회 캔들 수",
        description=(
            "지표 계산에 사용할 최대 캔들 수입니다."
            " 전략에 필요한 최소치보다 작게 입력해도 내부에서 자동 보정합니다."
        ),
        gt=0,
        json_schema_extra={"example": 400},
    )
    appkey: Optional[str] = Field(
        None,
        title="LS증권 앱키",
        description="LS Open API에서 발급받은 인증키입니다. 실계좌/모의계좌에 맞는 값을 넣어 주세요.",
    )
    appsecretkey: Optional[str] = Field(
        None,
        title="LS증권 앱시크릿",
        description="앱키와 한 쌍으로 쓰이는 비밀키입니다. 외부에 노출되지 않도록 안전하게 보관하세요.",
    )


class FuturesSMAEMACross(BaseStrategyConditionOverseasFutures):
    """SMA와 EMA를 겹쳐 추세가 바뀌는 순간을 초보자도 쉽게 확인할 수 있는 전략."""

    id: str = "FuturesSMAEMACross"
    name: str = "해외선물 SMA·EMA 추세 감시"
    description: str = (
        "느린 SMA(추세선)와 빠른 EMA(신호선)를 동시에 비교해 "
        "막 생긴 골든/데드크로스와 진행 중인 추세 방향을 색깔처럼 보여줍니다."
    )
    securities: List[str] = ["LS"]
    weight: float = 0.4
    parameter_schema: Dict[str, object] = FuturesSMAEMACrossParams.model_json_schema()

    def __init__(
        self,
        *,
        period_sma: int = 55,
        period_ema: int = 21,
        lookback: int = 3,
        timeframe: Literal["days", "weeks", "months"] = "days",
        qrycnt: int = 400,
        appkey: Optional[str] = None,
        appsecretkey: Optional[str] = None,
    ) -> None:
        super().__init__()
        if period_sma <= 1 or period_ema <= 1:
            raise ValueError("period_sma and period_ema must be greater than 1")
        if lookback <= 0:
            raise ValueError("lookback must be positive")
        self.period_sma = period_sma
        self.period_ema = period_ema
        self.lookback = lookback
        self.timeframe = timeframe
        self.qrycnt = max(qrycnt, period_sma + period_ema + lookback + 5)
        self.appkey = appkey
        self.appsecretkey = appsecretkey
        self._ls = LS.get_instance()

    async def execute(self) -> BaseStrategyConditionResponseOverseasFuturesType:
        candles = await self._load_candles()
        snapshots = self._calculate_signals(candles)
        last_signal = snapshots[-1].signal if snapshots else "neutral"
        highlighted = [snap for snap in snapshots if snap.signal != "neutral"]
        signal_events = highlighted[-self.lookback :]
        success = last_signal in {"bullish_cross", "bearish_cross", "trend_up", "trend_down"}
        data = {
            "signals": [asdict(item) for item in snapshots],
            "last_signal": last_signal,
            "signal_events": [asdict(item) for item in signal_events],
        }
        position_side = "flat"
        if last_signal in {"bullish_cross", "trend_up"}:
            position_side = "long"
        elif last_signal in {"bearish_cross", "trend_down"}:
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
            o3108.O3108InBlock(
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

    def _calculate_signals(self, candles: List[Candle]) -> List[MovingAverageSnapshot]:
        closes = [row["close"] for row in candles]
        sma_values: List[Optional[float]] = []
        ema_values: List[Optional[float]] = []
        multiplier = 2 / (self.period_ema + 1)
        for idx, price in enumerate(closes):
            sma = None
            if idx + 1 >= self.period_sma:
                sma = mean(closes[idx + 1 - self.period_sma : idx + 1])
            sma_values.append(sma)

            ema = None
            if idx + 1 == self.period_ema:
                ema = mean(closes[: self.period_ema])
            elif idx + 1 > self.period_ema:
                prev = ema_values[-1]
                if prev is None:
                    prev = mean(closes[idx + 1 - self.period_ema : idx + 1])
                ema = (price - prev) * multiplier + prev
            ema_values.append(ema)

        snapshots: List[MovingAverageSnapshot] = []
        for idx, candle in enumerate(candles):
            sma = sma_values[idx]
            ema = ema_values[idx]
            signal: Literal["bullish_cross", "bearish_cross", "trend_up", "trend_down", "neutral"] = "neutral"
            if sma is not None and ema is not None and idx > 0:
                prev_sma = sma_values[idx - 1]
                prev_ema = ema_values[idx - 1]
                if prev_sma is not None and prev_ema is not None:
                    if prev_ema <= prev_sma and ema > sma:
                        signal = "bullish_cross"
                    elif prev_ema >= prev_sma and ema < sma:
                        signal = "bearish_cross"
                    elif ema > sma:
                        signal = "trend_up"
                    elif ema < sma:
                        signal = "trend_down"
            snapshots.append(
                MovingAverageSnapshot(
                    date=candle["date"],
                    close=candle["close"],
                    sma=sma,
                    ema=ema,
                    signal=signal,
                )
            )
        return snapshots


__all__ = ["FuturesSMAEMACross"]
