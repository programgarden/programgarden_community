"""
OBV (On Balance Volume) 전략 조건

거래량의 누적을 통해 가격 움직임을 선행 예측합니다.
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
class OBVSnapshot:
    date: str
    close: float
    volume: int
    obv: int
    obv_ema: Optional[float]
    signal: Literal["accumulation", "distribution", "neutral"]
    divergence: Literal["bullish", "bearish", "none"]


class OBVParams(BaseModel):
    """OBV 파라미터"""

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
    ema_period: int = Field(
        20,
        title="OBV EMA 기간",
        description="OBV의 이동평균을 계산할 기간입니다. 신호선으로 사용됩니다.",
        ge=5,
    )
    divergence_lookback: int = Field(
        20,
        title="다이버전스 감지 기간",
        description="가격과 OBV의 다이버전스를 확인할 기간입니다.",
        ge=5,
    )
    timeframe: Literal["days", "weeks", "months"] = Field("days", title="시간 단위")
    qrycnt: int = Field(200, title="조회 캔들 수", ge=50)


class OBV(BaseStrategyConditionOverseasStock):
    """OBV 기반 거래량 분석 전략"""

    id: str = "OBV"
    name: str = "해외주식 OBV 거래량 분석"
    description: str = (
        "OBV(On Balance Volume)는 '거래량이 가격에 선행한다'는 원리에 기반합니다. "
        "가격이 오르면 거래량을 더하고, 내리면 뺍니다. "
        "OBV가 상승하면 매집(accumulation), 하락하면 분산(distribution)을 나타냅니다."
    )
    securities: List[str] = ["ls-sec.co.kr"]
    weight: float = 0.55
    parameter_schema: Dict[str, object] = OBVParams.model_json_schema()

    def __init__(
        self,
        *,
        ema_period: int = 20,
        divergence_lookback: int = 20,
        timeframe: Literal["days", "weeks", "months"] = "days",
        qrycnt: int = 200,
        appkey: Optional[str] = None,
        appsecretkey: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.ema_period = ema_period
        self.divergence_lookback = divergence_lookback
        self.timeframe = timeframe
        self.qrycnt = max(qrycnt, ema_period + divergence_lookback + 50)
        self.appkey = appkey
        self.appsecretkey = appsecretkey
        self._ls: Optional[object] = None

    async def execute(self) -> BaseStrategyConditionResponseOverseasStockType:
        candles = await self._load_candles()
        if len(candles) < self.ema_period + 10:
            raise ValueError("OBV 계산에 필요한 데이터가 부족합니다")

        snapshots = self._calculate_obv(candles)
        last = snapshots[-1] if snapshots else None

        # 주요 이벤트 수집
        signal_events = [s for s in snapshots if s.signal != "neutral"][-10:]
        divergence_events = [s for s in snapshots if s.divergence != "none"][-5:]

        success = last is not None and (last.signal != "neutral" or last.divergence != "none")

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
                "current_obv": last.obv if last else None,
                "current_signal": last.signal if last else "neutral",
                "current_divergence": last.divergence if last else "none",
                "signal_events": [asdict(s) for s in signal_events],
                "divergence_events": [asdict(s) for s in divergence_events],
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

    def _calculate_obv(self, candles: List[Candle]) -> List[OBVSnapshot]:
        """
        OBV 계산
        
        규칙:
        - 종가 상승 → OBV += 거래량
        - 종가 하락 → OBV -= 거래량
        - 종가 동일 → OBV 유지
        """
        snapshots: List[OBVSnapshot] = []
        obv = 0
        obv_list: List[int] = []
        
        for i, candle in enumerate(candles):
            if i == 0:
                obv = candle["volume"]
            else:
                prev_close = candles[i - 1]["close"]
                if candle["close"] > prev_close:
                    obv += candle["volume"]
                elif candle["close"] < prev_close:
                    obv -= candle["volume"]
                # 동일하면 유지
            
            obv_list.append(obv)
        
        # OBV EMA 계산
        obv_ema_list = self._ema(obv_list, self.ema_period)
        
        # 스냅샷 생성
        for i, candle in enumerate(candles):
            obv_val = obv_list[i]
            obv_ema = obv_ema_list[i]
            
            # 신호 판정: OBV가 EMA 위면 매집, 아래면 분산
            signal: Literal["accumulation", "distribution", "neutral"] = "neutral"
            if obv_ema is not None:
                if obv_val > obv_ema:
                    signal = "accumulation"
                elif obv_val < obv_ema:
                    signal = "distribution"
            
            # 다이버전스 감지
            divergence: Literal["bullish", "bearish", "none"] = "none"
            if i >= self.divergence_lookback:
                divergence = self._detect_divergence(candles, obv_list, i)
            
            snapshots.append(
                OBVSnapshot(
                    date=candle["date"],
                    close=candle["close"],
                    volume=candle["volume"],
                    obv=obv_val,
                    obv_ema=round(obv_ema, 2) if obv_ema else None,
                    signal=signal,
                    divergence=divergence,
                )
            )
        
        return snapshots

    def _detect_divergence(
        self,
        candles: List[Candle],
        obv_list: List[int],
        current_idx: int
    ) -> Literal["bullish", "bearish", "none"]:
        """가격과 OBV의 다이버전스 감지"""
        start_idx = current_idx - self.divergence_lookback
        
        # 기간 내 가격/OBV 추세 비교
        price_start = candles[start_idx]["close"]
        price_end = candles[current_idx]["close"]
        obv_start = obv_list[start_idx]
        obv_end = obv_list[current_idx]
        
        price_trend = price_end - price_start
        obv_trend = obv_end - obv_start
        
        # Bullish divergence: 가격 하락 + OBV 상승
        if price_trend < 0 and obv_trend > 0:
            return "bullish"
        
        # Bearish divergence: 가격 상승 + OBV 하락
        if price_trend > 0 and obv_trend < 0:
            return "bearish"
        
        return "none"

    def _ema(self, values: List[int], period: int) -> List[Optional[float]]:
        """EMA 계산"""
        ema_list: List[Optional[float]] = [None] * len(values)
        multiplier = 2 / (period + 1)
        
        for i, val in enumerate(values):
            if i < period - 1:
                continue
            if i == period - 1:
                ema_list[i] = sum(values[:period]) / period
            else:
                prev_ema = ema_list[i - 1]
                if prev_ema is not None:
                    ema_list[i] = (val - prev_ema) * multiplier + prev_ema
        
        return ema_list


__all__ = ["OBV", "OBVParams"]
