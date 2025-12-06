"""
ADX (Average Directional Index) 전략 조건

추세의 강도를 측정합니다. 방향이 아닌 '얼마나 강한 추세인가'를 알려줍니다.
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
class ADXSnapshot:
    date: str
    close: float
    adx: Optional[float]
    plus_di: Optional[float]
    minus_di: Optional[float]
    trend_strength: Literal["strong", "moderate", "weak", "no_trend"]
    di_signal: Literal["bullish", "bearish", "neutral"]


class ADXParams(BaseModel):
    """ADX 파라미터"""

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
        description="DI와 ADX를 계산할 기간입니다. 기본값 14는 Wilder의 원래 설정입니다.",
        ge=5,
    )
    strong_threshold: float = Field(
        25.0,
        title="강한 추세 임계값",
        description="ADX가 이 값 이상이면 추세가 강하다고 판단합니다.",
        ge=0,
        le=100,
    )
    very_strong_threshold: float = Field(
        50.0,
        title="매우 강한 추세 임계값",
        description="ADX가 이 값 이상이면 매우 강한 추세입니다.",
        ge=0,
        le=100,
    )
    timeframe: Literal["days", "weeks", "months"] = Field("days", title="시간 단위")
    qrycnt: int = Field(200, title="조회 캔들 수", ge=50)


class ADX(BaseStrategyConditionOverseasStock):
    """ADX 기반 추세 강도 측정 전략"""

    id: str = "ADX"
    name: str = "해외주식 ADX 추세 강도"
    description: str = (
        "ADX(Average Directional Index)는 추세의 '강도'를 측정합니다. "
        "25 이상이면 추세가 있고, 50 이상이면 매우 강한 추세입니다. "
        "+DI가 -DI 위에 있으면 상승 추세, 아래에 있으면 하락 추세 방향을 나타냅니다."
    )
    securities: List[str] = ["ls-sec.co.kr"]
    weight: float = 0.6
    parameter_schema: Dict[str, object] = ADXParams.model_json_schema()

    def __init__(
        self,
        *,
        appkey: Optional[str] = None,
        appsecretkey: Optional[str] = None,
        period: int = 14,
        strong_threshold: float = 25.0,
        very_strong_threshold: float = 50.0,
        timeframe: Literal["days", "weeks", "months"] = "days",
        qrycnt: int = 200,
    ) -> None:
        super().__init__()
        self.appkey = appkey
        self.appsecretkey = appsecretkey
        self.period = period
        self.strong_threshold = strong_threshold
        self.very_strong_threshold = very_strong_threshold
        self.timeframe = timeframe
        self.qrycnt = max(qrycnt, period * 3 + 50)
        self._ls = None

    async def execute(self) -> BaseStrategyConditionResponseOverseasStockType:
        candles = await self._load_candles()
        if len(candles) < self.period * 2 + 5:
            raise ValueError("ADX 계산에 필요한 데이터가 부족합니다")

        snapshots = self._calculate_adx(candles)
        last = snapshots[-1] if snapshots else None

        # 강한 추세 이벤트 수집
        strong_events = [s for s in snapshots if s.trend_strength in {"strong", "moderate"}][-10:]

        success = last is not None and last.trend_strength in {"strong", "moderate"}

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
                "current_adx": last.adx if last else None,
                "trend_strength": last.trend_strength if last else "no_trend",
                "di_signal": last.di_signal if last else "neutral",
                "strong_events": [asdict(s) for s in strong_events],
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

    def _calculate_adx(self, candles: List[Candle]) -> List[ADXSnapshot]:
        """
        ADX 계산 (Wilder's Smoothing 방식)
        
        1. True Range (TR) 계산
        2. +DM, -DM (Directional Movement) 계산
        3. Smoothed TR, +DM, -DM (Wilder's smoothing)
        4. +DI, -DI 계산
        5. DX = |+DI - -DI| / (+DI + -DI) × 100
        6. ADX = Smoothed DX
        """
        snapshots: List[ADXSnapshot] = []
        
        # 중간 계산값 저장
        tr_list: List[float] = []
        plus_dm_list: List[float] = []
        minus_dm_list: List[float] = []
        
        # 1. TR, +DM, -DM 계산
        for i, candle in enumerate(candles):
            if i == 0:
                tr_list.append(candle["high"] - candle["low"])
                plus_dm_list.append(0)
                minus_dm_list.append(0)
                continue
            
            prev = candles[i - 1]
            
            # True Range
            tr = max(
                candle["high"] - candle["low"],
                abs(candle["high"] - prev["close"]),
                abs(candle["low"] - prev["close"])
            )
            tr_list.append(tr)
            
            # Directional Movement
            up_move = candle["high"] - prev["high"]
            down_move = prev["low"] - candle["low"]
            
            plus_dm = up_move if up_move > down_move and up_move > 0 else 0
            minus_dm = down_move if down_move > up_move and down_move > 0 else 0
            
            plus_dm_list.append(plus_dm)
            minus_dm_list.append(minus_dm)
        
        # 2. Wilder's Smoothing
        smoothed_tr: List[Optional[float]] = [None] * len(candles)
        smoothed_plus_dm: List[Optional[float]] = [None] * len(candles)
        smoothed_minus_dm: List[Optional[float]] = [None] * len(candles)
        
        # 첫 번째 smoothed 값 (SMA)
        if len(candles) >= self.period:
            smoothed_tr[self.period - 1] = sum(tr_list[:self.period])
            smoothed_plus_dm[self.period - 1] = sum(plus_dm_list[:self.period])
            smoothed_minus_dm[self.period - 1] = sum(minus_dm_list[:self.period])
            
            for i in range(self.period, len(candles)):
                smoothed_tr[i] = smoothed_tr[i-1] - (smoothed_tr[i-1] / self.period) + tr_list[i]
                smoothed_plus_dm[i] = smoothed_plus_dm[i-1] - (smoothed_plus_dm[i-1] / self.period) + plus_dm_list[i]
                smoothed_minus_dm[i] = smoothed_minus_dm[i-1] - (smoothed_minus_dm[i-1] / self.period) + minus_dm_list[i]
        
        # 3. +DI, -DI, DX 계산
        plus_di_list: List[Optional[float]] = [None] * len(candles)
        minus_di_list: List[Optional[float]] = [None] * len(candles)
        dx_list: List[Optional[float]] = [None] * len(candles)
        
        for i in range(self.period - 1, len(candles)):
            if smoothed_tr[i] and smoothed_tr[i] > 0:
                plus_di_list[i] = 100 * smoothed_plus_dm[i] / smoothed_tr[i]
                minus_di_list[i] = 100 * smoothed_minus_dm[i] / smoothed_tr[i]
                
                di_sum = plus_di_list[i] + minus_di_list[i]
                if di_sum > 0:
                    dx_list[i] = 100 * abs(plus_di_list[i] - minus_di_list[i]) / di_sum
        
        # 4. ADX 계산 (DX의 Wilder's smoothing)
        adx_list: List[Optional[float]] = [None] * len(candles)
        adx_start = 2 * self.period - 2
        
        if len(candles) > adx_start:
            # 첫 ADX는 처음 period개 DX의 평균
            valid_dx = [dx for dx in dx_list[self.period-1:adx_start+1] if dx is not None]
            if valid_dx:
                adx_list[adx_start] = sum(valid_dx) / len(valid_dx)
                
                for i in range(adx_start + 1, len(candles)):
                    if adx_list[i-1] is not None and dx_list[i] is not None:
                        adx_list[i] = (adx_list[i-1] * (self.period - 1) + dx_list[i]) / self.period
        
        # 5. 스냅샷 생성
        for i, candle in enumerate(candles):
            adx = adx_list[i]
            plus_di = plus_di_list[i]
            minus_di = minus_di_list[i]
            
            # 추세 강도 판정
            trend_strength: Literal["strong", "moderate", "weak", "no_trend"] = "no_trend"
            if adx is not None:
                if adx >= self.very_strong_threshold:
                    trend_strength = "strong"
                elif adx >= self.strong_threshold:
                    trend_strength = "moderate"
                elif adx >= 20:
                    trend_strength = "weak"
            
            # DI 신호
            di_signal: Literal["bullish", "bearish", "neutral"] = "neutral"
            if plus_di is not None and minus_di is not None:
                if plus_di > minus_di:
                    di_signal = "bullish"
                elif minus_di > plus_di:
                    di_signal = "bearish"
            
            snapshots.append(
                ADXSnapshot(
                    date=candle["date"],
                    close=candle["close"],
                    adx=round(adx, 2) if adx else None,
                    plus_di=round(plus_di, 2) if plus_di else None,
                    minus_di=round(minus_di, 2) if minus_di else None,
                    trend_strength=trend_strength,
                    di_signal=di_signal,
                )
            )
        
        return snapshots


__all__ = ["ADX", "ADXParams"]
