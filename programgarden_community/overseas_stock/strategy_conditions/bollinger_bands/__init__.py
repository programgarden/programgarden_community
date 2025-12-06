"""
볼린저 밴드 (Bollinger Bands) 전략 조건

가격이 밴드의 상/하단을 터치하거나 이탈할 때 과매수/과매도 신호를 감지합니다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import mean, stdev
from typing import Dict, List, Literal, Optional, TypedDict

from programgarden_core import (
    BaseStrategyConditionOverseasStock,
    BaseStrategyConditionResponseOverseasStockType,
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
class BollingerSnapshot:
    date: str
    close: float
    middle: Optional[float]
    upper: Optional[float]
    lower: Optional[float]
    bandwidth: Optional[float]
    percent_b: Optional[float]
    signal: Literal["upper_touch", "lower_touch", "squeeze", "expansion", "neutral"]


class BollingerBandsParams(BaseModel):
    """볼린저 밴드 전략 파라미터"""

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
        title="이동평균 기간",
        description="중심선(SMA)을 계산할 기간입니다. 기본값 20은 약 한 달치 거래일입니다.",
        ge=5,
        json_schema_extra={"example": 20},
    )
    std_dev: float = Field(
        2.0,
        title="표준편차 배수",
        description="상/하단 밴드를 중심선에서 몇 표준편차 떨어뜨릴지 설정합니다.",
        gt=0,
        json_schema_extra={"example": 2.0},
    )
    squeeze_threshold: float = Field(
        0.05,
        title="스퀴즈 임계값",
        description="밴드폭이 이 값 이하로 좁아지면 '스퀴즈(횡보 압축)' 상태로 판단합니다.",
        gt=0,
        json_schema_extra={"example": 0.05},
    )
    timeframe: Literal["days", "weeks", "months"] = Field(
        "days",
        title="시간 단위",
        description="일봉/주봉/월봉 선택",
    )
    qrycnt: int = Field(
        200,
        title="조회 캔들 수",
        description="분석에 사용할 캔들 개수",
        ge=50,
    )


class BollingerBands(BaseStrategyConditionOverseasStock):
    """볼린저 밴드 기반 과열/침체 및 스퀴즈 감지 전략"""

    id: str = "BollingerBands"
    name: str = "해외주식 볼린저 밴드"
    description: str = (
        "볼린저 밴드는 가격의 변동성을 시각화하는 지표입니다. "
        "가격이 상단 밴드를 터치하면 과열, 하단 밴드를 터치하면 침체로 판단하며, "
        "밴드폭이 좁아지는 '스퀴즈' 상태는 큰 움직임이 임박했음을 암시합니다."
    )
    securities: List[str] = ["ls-sec.co.kr"]
    weight: float = 0.5
    parameter_schema: Dict[str, object] = BollingerBandsParams.model_json_schema()

    def __init__(
        self,
        *,
        appkey: Optional[str] = None,
        appsecretkey: Optional[str] = None,
        period: int = 20,
        std_dev: float = 2.0,
        squeeze_threshold: float = 0.05,
        timeframe: Literal["days", "weeks", "months"] = "days",
        qrycnt: int = 200,
    ) -> None:
        super().__init__()
        self.appkey = appkey
        self.appsecretkey = appsecretkey
        self.period = period
        self.std_dev = std_dev
        self.squeeze_threshold = squeeze_threshold
        self.timeframe = timeframe
        self.qrycnt = max(qrycnt, period + 50)
        self._ls = None

    async def execute(self) -> BaseStrategyConditionResponseOverseasStockType:
        candles = await self._load_candles()
        if len(candles) < self.period + 5:
            raise ValueError("볼린저 밴드 계산에 필요한 데이터가 부족합니다")

        snapshots = self._calculate_bollinger(candles)
        last_snapshot = snapshots[-1] if snapshots else None
        last_signal = last_snapshot.signal if last_snapshot else "neutral"

        # 최근 신호 이벤트 수집
        signal_events = [s for s in snapshots if s.signal != "neutral"][-10:]

        success = last_signal in {"upper_touch", "lower_touch", "squeeze"}

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
            "data": {
                "snapshots": [asdict(s) for s in snapshots[-100:]],
                "last_signal": last_signal,
                "signal_events": [asdict(s) for s in signal_events],
                "current": asdict(last_snapshot) if last_snapshot else None,
            },
        }

    async def _load_candles(self) -> List[Candle]:
        from programgarden_finance import LS, g3204

        if not self.appkey or not self.appsecretkey:
            raise ValueError("LS증권 API 키가 필요합니다")
        if not self.symbol:
            raise ValueError("종목 정보가 필요합니다")

        if self._ls is None:
            self._ls = LS.get_instance()
        
        if not self._ls.token_manager or not self._ls.token_manager.is_token_available():
            await self._ls.async_login(self.appkey, self.appsecretkey)

        exchcd = self.symbol.get("exchcd", "")
        symbol = self.symbol.get("symbol", "")
        gubun_map = {"days": "2", "weeks": "3", "months": "4"}
        gubun = gubun_map.get(self.timeframe, "2")

        request = self._ls.overseas_stock().chart().g3204(
            g3204.G3204InBlock(
                sdate="",
                edate="",
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
                        open=float(block.open),
                        high=float(block.high),
                        low=float(block.low),
                        close=float(block.close),
                        volume=int(getattr(block, "volume", 0)),
                    )
                )
        candles.sort(key=lambda c: c["date"])
        return candles

    def _calculate_bollinger(self, candles: List[Candle]) -> List[BollingerSnapshot]:
        closes = [c["close"] for c in candles]
        snapshots: List[BollingerSnapshot] = []

        for i, candle in enumerate(candles):
            if i + 1 < self.period:
                snapshots.append(
                    BollingerSnapshot(
                        date=candle["date"],
                        close=candle["close"],
                        middle=None,
                        upper=None,
                        lower=None,
                        bandwidth=None,
                        percent_b=None,
                        signal="neutral",
                    )
                )
                continue

            window = closes[i + 1 - self.period : i + 1]
            middle = mean(window)
            std = stdev(window) if len(window) > 1 else 0
            upper = middle + self.std_dev * std
            lower = middle - self.std_dev * std

            # 밴드폭 (Bandwidth)
            bandwidth = (upper - lower) / middle if middle > 0 else 0

            # %B: 현재 가격이 밴드 내 어디에 위치하는지 (0~1 범위, 벗어날 수 있음)
            percent_b = (candle["close"] - lower) / (upper - lower) if upper != lower else 0.5

            # 신호 판정
            signal: Literal["upper_touch", "lower_touch", "squeeze", "expansion", "neutral"] = "neutral"
            if candle["close"] >= upper:
                signal = "upper_touch"
            elif candle["close"] <= lower:
                signal = "lower_touch"
            elif bandwidth <= self.squeeze_threshold:
                signal = "squeeze"
            elif i > 0 and snapshots[i - 1].bandwidth is not None:
                prev_bw = snapshots[i - 1].bandwidth
                if prev_bw and bandwidth > prev_bw * 1.5:
                    signal = "expansion"

            snapshots.append(
                BollingerSnapshot(
                    date=candle["date"],
                    close=candle["close"],
                    middle=round(middle, 4),
                    upper=round(upper, 4),
                    lower=round(lower, 4),
                    bandwidth=round(bandwidth, 4),
                    percent_b=round(percent_b, 4),
                    signal=signal,
                )
            )

        return snapshots


__all__ = ["BollingerBands", "BollingerBandsParams"]
