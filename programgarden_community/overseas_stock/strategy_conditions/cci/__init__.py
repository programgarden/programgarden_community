"""
CCI (Commodity Channel Index) 전략 조건

가격이 평균에서 얼마나 벗어났는지 측정하여 과매수/과매도를 판단합니다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import mean
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
class CCISnapshot:
    date: str
    close: float
    typical_price: float
    cci: Optional[float]
    signal: Literal["overbought", "oversold", "neutral"]
    zero_cross: Literal["bullish", "bearish", "none"]


class CCIParams(BaseModel):
    """CCI 파라미터"""

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
        title="CCI 기간",
        description="Typical Price의 이동평균과 평균편차를 계산할 기간입니다.",
        ge=5,
    )
    overbought: float = Field(
        100.0,
        title="과매수 임계값",
        description="CCI가 이 값 이상이면 과매수 상태입니다.",
    )
    oversold: float = Field(
        -100.0,
        title="과매도 임계값",
        description="CCI가 이 값 이하면 과매도 상태입니다.",
    )
    timeframe: Literal["days", "weeks", "months"] = Field("days", title="시간 단위")
    qrycnt: int = Field(200, title="조회 캔들 수", ge=50)


class CCI(BaseStrategyConditionOverseasStock):
    """CCI 기반 과열/침체 감지 전략"""

    id: str = "CCI"
    name: str = "해외주식 CCI 상품채널지수"
    description: str = (
        "CCI(Commodity Channel Index)는 현재 가격이 평균에서 얼마나 벗어났는지를 측정합니다. "
        "+100 이상은 과매수, -100 이하는 과매도 상태를 나타내며, "
        "0선 교차는 추세 전환 신호로 활용됩니다."
    )
    securities: List[str] = ["ls-sec.co.kr"]
    weight: float = 0.5
    parameter_schema: Dict[str, object] = CCIParams.model_json_schema()

    def __init__(
        self,
        *,
        appkey: Optional[str] = None,
        appsecretkey: Optional[str] = None,
        period: int = 20,
        overbought: float = 100.0,
        oversold: float = -100.0,
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
            raise ValueError("CCI 계산에 필요한 데이터가 부족합니다")

        snapshots = self._calculate_cci(candles)
        last = snapshots[-1] if snapshots else None

        # 과매수/과매도 이벤트 수집
        extreme_events = [s for s in snapshots if s.signal != "neutral"][-10:]
        cross_events = [s for s in snapshots if s.zero_cross != "none"][-5:]

        success = last is not None and (last.signal != "neutral" or last.zero_cross != "none")

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
                "current_cci": last.cci if last else None,
                "current_signal": last.signal if last else "neutral",
                "extreme_events": [asdict(s) for s in extreme_events],
                "cross_events": [asdict(s) for s in cross_events],
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

    def _calculate_cci(self, candles: List[Candle]) -> List[CCISnapshot]:
        """
        CCI 계산
        
        공식:
        Typical Price (TP) = (High + Low + Close) / 3
        SMA = SMA(TP, period)
        Mean Deviation = 평균(|TP - SMA|)
        CCI = (TP - SMA) / (0.015 × Mean Deviation)
        """
        snapshots: List[CCISnapshot] = []
        
        # Typical Price 계산
        tp_list = [(c["high"] + c["low"] + c["close"]) / 3 for c in candles]
        
        prev_cci: Optional[float] = None
        
        for i, candle in enumerate(candles):
            tp = tp_list[i]
            
            if i + 1 < self.period:
                snapshots.append(
                    CCISnapshot(
                        date=candle["date"],
                        close=candle["close"],
                        typical_price=round(tp, 4),
                        cci=None,
                        signal="neutral",
                        zero_cross="none",
                    )
                )
                continue
            
            # SMA of Typical Price
            window = tp_list[i + 1 - self.period : i + 1]
            sma = mean(window)
            
            # Mean Deviation
            mean_dev = mean(abs(tp_val - sma) for tp_val in window)
            
            # CCI 계산 (0.015는 Lambert의 상수)
            cci = (tp - sma) / (0.015 * mean_dev) if mean_dev > 0 else 0
            
            # 신호 판정
            signal: Literal["overbought", "oversold", "neutral"] = "neutral"
            if cci >= self.overbought:
                signal = "overbought"
            elif cci <= self.oversold:
                signal = "oversold"
            
            # 0선 교차 감지
            zero_cross: Literal["bullish", "bearish", "none"] = "none"
            if prev_cci is not None:
                if prev_cci < 0 and cci >= 0:
                    zero_cross = "bullish"
                elif prev_cci > 0 and cci <= 0:
                    zero_cross = "bearish"
            
            prev_cci = cci
            
            snapshots.append(
                CCISnapshot(
                    date=candle["date"],
                    close=candle["close"],
                    typical_price=round(tp, 4),
                    cci=round(cci, 2),
                    signal=signal,
                    zero_cross=zero_cross,
                )
            )
        
        return snapshots


__all__ = ["CCI", "CCIParams"]
