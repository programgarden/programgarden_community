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


class StockSMAEMACrossParams(BaseModel):
    """해외주식 SMA·EMA 전략 파라미터 정의"""

    period_sma: int = Field(
        55,
        title="느린 SMA 기간",
        description=(
            "해외주식의 큰 흐름을 보여주는 기준선입니다. 값을 늘리면 추세가 부드럽고, 줄이면 민감해집니다."
        ),
        gt=1,
        json_schema_extra={"example": 55},
    )
    period_ema: int = Field(
        21,
        title="빠른 EMA 기간",
        description=(
            "신호선을 담당하는 EMA가 최신 가격에 얼마나 민감할지를 정합니다. "
            "작을수록 빠르지만 노이즈가 커지고, 길수록 한 박자 늦지만 안정적입니다."
        ),
        gt=1,
        json_schema_extra={"example": 21},
    )
    lookback: int = Field(
        3,
        title="신호 확인 구간",
        description=(
            "최근 N개의 캔들 안에서 발생한 교차·추세 신호만 모아 제공합니다."
        ),
        gt=0,
        json_schema_extra={"example": 3},
    )
    timeframe: Literal["days", "weeks", "months"] = Field(
        "days",
        title="캔들 간격",
        description="일봉/주봉/월봉 중 어떤 간격으로 데이터를 불러올지 선택합니다.",
    )
    focus_direction: Literal["bullish", "bearish", "both"] = Field(
        "both",
        title="관심 추세 방향",
        description=(
            "골든/상승 신호만 볼지(bullish), 데드/하락 신호만 볼지(bearish), 혹은 모두 확인할지 선택합니다. "
            "기본값 both는 기존 동작과 동일하게 모든 신호를 제공합니다."
        ),
        json_schema_extra={"example": "bullish"},
    )
    qrycnt: int = Field(
        400,
        title="조회 캔들 수",
        description=(
            "지표 계산에 사용할 최대 캔들 수입니다. 전략에 필요한 최소치보다 작게 입력해도 자동 보정됩니다."
        ),
        gt=0,
        json_schema_extra={"example": 400},
    )
    appkey: Optional[str] = Field(
        None,
        title="LS증권 앱키",
        description="LS Open API 해외주식 인증키입니다.",
    )
    appsecretkey: Optional[str] = Field(
        None,
        title="LS증권 앱시크릿",
        description="앱키와 한 쌍으로 쓰이는 비밀키입니다.",
    )


class StockSMAEMACross(BaseStrategyConditionOverseasStock):
    """SMA와 EMA를 겹쳐 해외주식 추세 전환을 빠르게 감지합니다."""

    id: str = "StockSMAEMACross"
    name: str = "해외주식 SMA·EMA 추세 감시"
    description: str = (
        "느린 SMA(추세선)와 빠른 EMA(신호선)의 상대 위치를 비교해 골든/데드 크로스와 진행 중인 추세 방향을 한눈에 보여줍니다."
    )
    securities: List[str] = ["LS"]
    weight: float = 0.4
    parameter_schema: Dict[str, object] = StockSMAEMACrossParams.model_json_schema()

    def __init__(
        self,
        *,
        period_sma: int = 55,
        period_ema: int = 21,
        lookback: int = 3,
        timeframe: Literal["days", "weeks", "months"] = "days",
        focus_direction: Literal["bullish", "bearish", "both"] = "both",
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
        self.focus_direction = focus_direction
        self.qrycnt = max(qrycnt, period_sma + period_ema + lookback + 5)
        self.appkey = appkey
        self.appsecretkey = appsecretkey
        self._ls = LS.get_instance()

    async def execute(self) -> BaseStrategyConditionResponseOverseasStockType:
        candles = await self._load_candles()
        snapshots = self._calculate_signals(candles)
        last_snapshot_signal = snapshots[-1].signal if snapshots else "neutral"
        highlighted = [snap for snap in snapshots if snap.signal != "neutral"]
        focus_events = [snap for snap in highlighted if self._matches_focus(snap.signal)]

        if self.focus_direction == "both":
            last_signal = last_snapshot_signal
            signal_events = highlighted[-self.lookback :]
            success = last_signal in {"bullish_cross", "bearish_cross", "trend_up", "trend_down"}
        else:
            last_signal = focus_events[-1].signal if focus_events else "neutral"
            signal_events = focus_events[-self.lookback :]
            success = bool(snapshots) and self._matches_focus(last_snapshot_signal)

        symbol = self.symbol.get("symbol", "") if self.symbol else ""
        exchcd = self.symbol.get("exchcd", "") if self.symbol else ""
        data = {
            "signals": [asdict(item) for item in snapshots],
            "last_signal": last_signal,
            "signal_events": [asdict(item) for item in signal_events],
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

    def _matches_focus(self, signal: Literal["bullish_cross", "bearish_cross", "trend_up", "trend_down", "neutral"]) -> bool:
        if signal == "neutral":
            return False
        if self.focus_direction == "both":
            return True
        bullish_signals = {"bullish_cross", "trend_up"}
        bearish_signals = {"bearish_cross", "trend_down"}
        if self.focus_direction == "bullish":
            return signal in bullish_signals
        if self.focus_direction == "bearish":
            return signal in bearish_signals
        return False


__all__ = ["StockSMAEMACross", "StockSMAEMACrossParams"]
