"""
AD (Accumulation/Distribution) 전략 조건

매집/분산 지표로 자금 흐름을 분석합니다.
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
class ADSnapshot:
    date: str
    close: float
    volume: int
    money_flow_multiplier: Optional[float]
    money_flow_volume: Optional[float]
    ad_line: float
    signal: Literal["accumulation", "distribution", "neutral"]
    divergence: Literal["bullish", "bearish", "none"]


class ADParams(BaseModel):
    """AD 파라미터"""

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
        title="AD EMA 기간",
        description="AD 라인의 이동평균 기간입니다.",
        ge=5,
    )
    divergence_lookback: int = Field(
        20,
        title="다이버전스 감지 기간",
        description="가격과 AD의 다이버전스를 확인할 기간입니다.",
        ge=5,
    )
    timeframe: Literal["days", "weeks", "months"] = Field("days", title="시간 단위")
    qrycnt: int = Field(200, title="조회 캔들 수", ge=50)


class AD(BaseStrategyConditionOverseasStock):
    """Accumulation/Distribution Line 기반 자금 흐름 분석"""

    id: str = "AD"
    name: str = "해외주식 AD 매집/분산"
    description: str = (
        "A/D Line(Accumulation/Distribution Line)은 종가 위치와 거래량을 결합하여 "
        "자금의 유입(매집)과 유출(분산)을 측정합니다. "
        "OBV보다 정교하게 종가가 고가/저가 범위 내 어디에 있는지 반영합니다."
    )
    securities: List[str] = ["ls-sec.co.kr"]
    weight: float = 0.55
    parameter_schema: Dict[str, object] = ADParams.model_json_schema()

    def __init__(
        self,
        *,
        appkey: Optional[str] = None,
        appsecretkey: Optional[str] = None,
        ema_period: int = 20,
        divergence_lookback: int = 20,
        timeframe: Literal["days", "weeks", "months"] = "days",
        qrycnt: int = 200,
    ) -> None:
        super().__init__()
        self.appkey = appkey
        self.appsecretkey = appsecretkey
        self.ema_period = ema_period
        self.divergence_lookback = divergence_lookback
        self.timeframe = timeframe
        self.qrycnt = max(qrycnt, ema_period + divergence_lookback + 50)
        self._ls = None

    async def execute(self) -> BaseStrategyConditionResponseOverseasStockType:
        candles = await self._load_candles()
        if len(candles) < self.ema_period + 10:
            raise ValueError("AD 계산에 필요한 데이터가 부족합니다")

        snapshots = self._calculate_ad(candles)
        last = snapshots[-1] if snapshots else None

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
                "current_ad": last.ad_line if last else None,
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

    def _calculate_ad(self, candles: List[Candle]) -> List[ADSnapshot]:
        """
        A/D Line 계산
        
        Money Flow Multiplier (MFM) = [(Close - Low) - (High - Close)] / (High - Low)
        Money Flow Volume (MFV) = MFM × Volume
        A/D Line = 누적(MFV)
        """
        snapshots: List[ADSnapshot] = []
        ad_line = 0.0
        ad_list: List[float] = []
        
        for candle in candles:
            high = candle["high"]
            low = candle["low"]
            close = candle["close"]
            volume = candle["volume"]
            
            # Money Flow Multiplier
            if high != low:
                mfm = ((close - low) - (high - close)) / (high - low)
            else:
                mfm = 0.0
            
            # Money Flow Volume
            mfv = mfm * volume
            
            # A/D Line (누적)
            ad_line += mfv
            ad_list.append(ad_line)
        
        # A/D EMA 계산
        ad_ema_list = self._ema(ad_list, self.ema_period)
        
        # 스냅샷 생성
        for i, candle in enumerate(candles):
            high = candle["high"]
            low = candle["low"]
            close = candle["close"]
            volume = candle["volume"]
            
            if high != low:
                mfm = ((close - low) - (high - close)) / (high - low)
            else:
                mfm = 0.0
            
            mfv = mfm * volume
            ad_val = ad_list[i]
            ad_ema = ad_ema_list[i]
            
            # 신호 판정
            signal: Literal["accumulation", "distribution", "neutral"] = "neutral"
            if ad_ema is not None:
                if ad_val > ad_ema:
                    signal = "accumulation"
                elif ad_val < ad_ema:
                    signal = "distribution"
            
            # 다이버전스 감지
            divergence: Literal["bullish", "bearish", "none"] = "none"
            if i >= self.divergence_lookback:
                divergence = self._detect_divergence(candles, ad_list, i)
            
            snapshots.append(
                ADSnapshot(
                    date=candle["date"],
                    close=close,
                    volume=volume,
                    money_flow_multiplier=round(mfm, 4),
                    money_flow_volume=round(mfv, 2),
                    ad_line=round(ad_val, 2),
                    signal=signal,
                    divergence=divergence,
                )
            )
        
        return snapshots

    def _detect_divergence(
        self,
        candles: List[Candle],
        ad_list: List[float],
        current_idx: int
    ) -> Literal["bullish", "bearish", "none"]:
        """가격과 A/D의 다이버전스 감지"""
        start_idx = current_idx - self.divergence_lookback
        
        price_start = candles[start_idx]["close"]
        price_end = candles[current_idx]["close"]
        ad_start = ad_list[start_idx]
        ad_end = ad_list[current_idx]
        
        price_trend = price_end - price_start
        ad_trend = ad_end - ad_start
        
        # Bullish divergence: 가격 하락 + AD 상승
        if price_trend < 0 and ad_trend > 0:
            return "bullish"
        
        # Bearish divergence: 가격 상승 + AD 하락
        if price_trend > 0 and ad_trend < 0:
            return "bearish"
        
        return "none"

    def _ema(self, values: List[float], period: int) -> List[Optional[float]]:
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


__all__ = ["AD", "ADParams"]
