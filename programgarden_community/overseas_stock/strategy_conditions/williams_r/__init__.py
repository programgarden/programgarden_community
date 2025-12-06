"""
Williams %R 전략 조건

스토캐스틱과 유사하지만 역방향으로 표시되는 모멘텀 오실레이터입니다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
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
class WilliamsRSnapshot:
    date: str
    close: float
    williams_r: Optional[float]
    signal: Literal["overbought", "oversold", "neutral"]
    momentum_shift: Literal["bullish", "bearish", "none"]


class WilliamsRParams(BaseModel):
    """Williams %R 파라미터"""

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
        title="기간",
        description="최고가/최저가를 찾을 기간입니다.",
        ge=5,
    )
    overbought: float = Field(
        -20.0,
        title="과매수 임계값",
        description="%R이 이 값 이상이면 과매수입니다. (Williams %R은 음수 스케일)",
        ge=-100,
        le=0,
    )
    oversold: float = Field(
        -80.0,
        title="과매도 임계값",
        description="%R이 이 값 이하면 과매도입니다.",
        ge=-100,
        le=0,
    )
    timeframe: Literal["days", "weeks", "months"] = Field("days", title="시간 단위")
    qrycnt: int = Field(200, title="조회 캔들 수", ge=50)


class WilliamsR(BaseStrategyConditionOverseasStock):
    """Williams %R 기반 과열/침체 감지 전략"""

    id: str = "WilliamsR"
    name: str = "해외주식 Williams %R"
    description: str = (
        "Williams %R은 현재 종가가 최근 고점/저점 범위에서 어디에 위치하는지 보여줍니다. "
        "-20 이상은 과매수, -80 이하는 과매도를 나타냅니다. "
        "스토캐스틱과 유사하지만 역방향(-100~0)으로 표시됩니다."
    )
    securities: List[str] = ["ls-sec.co.kr"]
    weight: float = 0.5
    parameter_schema: Dict[str, object] = WilliamsRParams.model_json_schema()

    def __init__(
        self,
        *,
        appkey: Optional[str] = None,
        appsecretkey: Optional[str] = None,
        period: int = 14,
        overbought: float = -20.0,
        oversold: float = -80.0,
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

    async def execute(self) -> BaseStrategyConditionResponseOverseasStockType:
        candles = await self._load_candles()
        if len(candles) < self.period + 5:
            raise ValueError("Williams %R 계산에 필요한 데이터가 부족합니다")

        snapshots = self._calculate_williams_r(candles)
        last = snapshots[-1] if snapshots else None

        extreme_events = [s for s in snapshots if s.signal != "neutral"][-10:]
        shift_events = [s for s in snapshots if s.momentum_shift != "none"][-5:]

        success = last is not None and (last.signal != "neutral" or last.momentum_shift != "none")

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
                "current_williams_r": last.williams_r if last else None,
                "current_signal": last.signal if last else "neutral",
                "extreme_events": [asdict(s) for s in extreme_events],
                "shift_events": [asdict(s) for s in shift_events],
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

    def _calculate_williams_r(self, candles: List[Candle]) -> List[WilliamsRSnapshot]:
        """
        Williams %R 계산
        
        공식:
        %R = (최고가 - 종가) / (최고가 - 최저가) × -100
        
        결과: -100 ~ 0 범위
        - 0에 가까우면 과매수
        - -100에 가까우면 과매도
        """
        snapshots: List[WilliamsRSnapshot] = []
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        
        prev_r: Optional[float] = None
        
        for i, candle in enumerate(candles):
            if i + 1 < self.period:
                snapshots.append(
                    WilliamsRSnapshot(
                        date=candle["date"],
                        close=candle["close"],
                        williams_r=None,
                        signal="neutral",
                        momentum_shift="none",
                    )
                )
                continue
            
            # 기간 내 최고가/최저가
            highest_high = max(highs[i + 1 - self.period : i + 1])
            lowest_low = min(lows[i + 1 - self.period : i + 1])
            
            # Williams %R 계산
            if highest_high != lowest_low:
                williams_r = ((highest_high - candle["close"]) / (highest_high - lowest_low)) * -100
            else:
                williams_r = -50  # 범위가 0이면 중간값
            
            # 신호 판정
            signal: Literal["overbought", "oversold", "neutral"] = "neutral"
            if williams_r >= self.overbought:
                signal = "overbought"
            elif williams_r <= self.oversold:
                signal = "oversold"
            
            # 모멘텀 전환 감지
            momentum_shift: Literal["bullish", "bearish", "none"] = "none"
            if prev_r is not None:
                # 과매도에서 탈출
                if prev_r <= self.oversold and williams_r > self.oversold:
                    momentum_shift = "bullish"
                # 과매수에서 탈출
                elif prev_r >= self.overbought and williams_r < self.overbought:
                    momentum_shift = "bearish"
            
            prev_r = williams_r
            
            snapshots.append(
                WilliamsRSnapshot(
                    date=candle["date"],
                    close=candle["close"],
                    williams_r=round(williams_r, 2),
                    signal=signal,
                    momentum_shift=momentum_shift,
                )
            )
        
        return snapshots


__all__ = ["WilliamsR", "WilliamsRParams"]
