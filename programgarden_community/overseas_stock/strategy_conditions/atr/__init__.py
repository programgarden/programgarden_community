"""
ATR (Average True Range) 전략 조건

변동성을 측정하여 손절 라인 설정과 포지션 크기 결정에 활용합니다.
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
class ATRSnapshot:
    date: str
    close: float
    true_range: Optional[float]
    atr: Optional[float]
    natr: Optional[float]  # Normalized ATR (ATR/Close × 100)
    volatility_level: Literal["high", "normal", "low"]
    suggested_stop: Optional[float]  # ATR 기반 손절가


class ATRParams(BaseModel):
    """ATR 파라미터"""

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
        title="ATR 기간",
        description="ATR을 계산할 기간입니다. Wilder의 원래 설정은 14입니다.",
        ge=5,
    )
    atr_multiplier: float = Field(
        2.0,
        title="ATR 배수",
        description="손절 라인을 현재가에서 몇 ATR 떨어뜨릴지 설정합니다.",
        gt=0,
    )
    high_volatility_threshold: float = Field(
        2.0,
        title="고변동성 임계값 (NATR)",
        description="NATR이 이 값 이상이면 고변동성으로 판단합니다.",
        gt=0,
    )
    low_volatility_threshold: float = Field(
        0.5,
        title="저변동성 임계값 (NATR)",
        description="NATR이 이 값 이하면 저변동성으로 판단합니다.",
        gt=0,
    )
    timeframe: Literal["days", "weeks", "months"] = Field("days", title="시간 단위")
    qrycnt: int = Field(200, title="조회 캔들 수", ge=50)


class ATR(BaseStrategyConditionOverseasStock):
    """ATR 기반 변동성 분석 전략"""

    id: str = "ATR"
    name: str = "해외주식 ATR 변동성 분석"
    description: str = (
        "ATR(Average True Range)은 가격의 변동성을 측정하는 핵심 지표입니다. "
        "손절 라인 설정, 포지션 크기 결정, 변동성 돌파 전략에 필수적으로 사용됩니다. "
        "NATR(정규화 ATR)은 서로 다른 가격대의 종목을 비교할 때 유용합니다."
    )
    securities: List[str] = ["ls-sec.co.kr"]
    weight: float = 0.6
    parameter_schema: Dict[str, object] = ATRParams.model_json_schema()

    def __init__(
        self,
        *,
        appkey: Optional[str] = None,
        appsecretkey: Optional[str] = None,
        period: int = 14,
        atr_multiplier: float = 2.0,
        high_volatility_threshold: float = 2.0,
        low_volatility_threshold: float = 0.5,
        timeframe: Literal["days", "weeks", "months"] = "days",
        qrycnt: int = 200,
    ) -> None:
        super().__init__()
        self.appkey = appkey
        self.appsecretkey = appsecretkey
        self.period = period
        self.atr_multiplier = atr_multiplier
        self.high_volatility_threshold = high_volatility_threshold
        self.low_volatility_threshold = low_volatility_threshold
        self.timeframe = timeframe
        self.qrycnt = max(qrycnt, period + 50)
        self._ls = None

    async def execute(self) -> BaseStrategyConditionResponseOverseasStockType:
        candles = await self._load_candles()
        if len(candles) < self.period + 5:
            raise ValueError("ATR 계산에 필요한 데이터가 부족합니다")

        snapshots = self._calculate_atr(candles)
        last = snapshots[-1] if snapshots else None

        # 변동성 수준 분석
        volatility_events = [s for s in snapshots if s.volatility_level != "normal"][-10:]

        success = last is not None and last.atr is not None

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
                "current_atr": last.atr if last else None,
                "current_natr": last.natr if last else None,
                "volatility_level": last.volatility_level if last else "normal",
                "suggested_stop_long": last.suggested_stop if last else None,
                "suggested_stop_short": round(last.close + last.atr * self.atr_multiplier, 4) if last and last.atr else None,
                "volatility_events": [asdict(s) for s in volatility_events],
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

    def _calculate_atr(self, candles: List[Candle]) -> List[ATRSnapshot]:
        """
        ATR 계산 (Wilder's Smoothing)
        
        True Range (TR) = max(
            High - Low,
            |High - Prev_Close|,
            |Low - Prev_Close|
        )
        
        ATR = Wilder's Smoothed TR
        NATR = (ATR / Close) × 100
        """
        snapshots: List[ATRSnapshot] = []
        tr_list: List[float] = []
        
        # True Range 계산
        for i, candle in enumerate(candles):
            if i == 0:
                tr = candle["high"] - candle["low"]
            else:
                prev_close = candles[i - 1]["close"]
                tr = max(
                    candle["high"] - candle["low"],
                    abs(candle["high"] - prev_close),
                    abs(candle["low"] - prev_close)
                )
            tr_list.append(tr)
        
        # ATR 계산 (Wilder's Smoothing)
        atr_values: List[Optional[float]] = [None] * len(candles)
        
        if len(candles) >= self.period:
            # 첫 ATR은 TR의 SMA
            atr_values[self.period - 1] = sum(tr_list[:self.period]) / self.period
            
            # 이후는 Wilder's smoothing
            for i in range(self.period, len(candles)):
                prev_atr = atr_values[i - 1]
                if prev_atr is not None:
                    atr_values[i] = (prev_atr * (self.period - 1) + tr_list[i]) / self.period
        
        # 스냅샷 생성
        for i, candle in enumerate(candles):
            tr = tr_list[i]
            atr = atr_values[i]
            
            # NATR 계산
            natr = (atr / candle["close"]) * 100 if atr and candle["close"] > 0 else None
            
            # 변동성 수준 판정
            volatility_level: Literal["high", "normal", "low"] = "normal"
            if natr is not None:
                if natr >= self.high_volatility_threshold:
                    volatility_level = "high"
                elif natr <= self.low_volatility_threshold:
                    volatility_level = "low"
            
            # 손절가 계산 (롱 포지션 기준)
            suggested_stop = round(candle["close"] - atr * self.atr_multiplier, 4) if atr else None
            
            snapshots.append(
                ATRSnapshot(
                    date=candle["date"],
                    close=candle["close"],
                    true_range=round(tr, 4),
                    atr=round(atr, 4) if atr else None,
                    natr=round(natr, 4) if natr else None,
                    volatility_level=volatility_level,
                    suggested_stop=suggested_stop,
                )
            )
        
        return snapshots


__all__ = ["ATR", "ATRParams"]
