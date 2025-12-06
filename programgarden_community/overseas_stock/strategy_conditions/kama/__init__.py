"""
KAMA (Kaufman's Adaptive Moving Average) 전략 조건

변동성에 따라 민감도가 자동 조절되는 적응형 이동평균선입니다.
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
class KAMASnapshot:
    date: str
    close: float
    kama: Optional[float]
    efficiency_ratio: Optional[float]
    trend: Literal["bullish", "bearish", "sideways"]
    signal: Literal["buy", "sell", "hold"]


class KAMAParams(BaseModel):
    """KAMA 파라미터"""

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
    er_period: int = Field(
        10,
        title="효율비 기간",
        description="Efficiency Ratio를 계산할 기간입니다. 방향성 대비 변동성을 측정합니다.",
        ge=2,
    )
    fast_period: int = Field(
        2,
        title="빠른 EMA 기간",
        description="추세가 강할 때 적용되는 민감한 EMA 기간입니다.",
        ge=2,
    )
    slow_period: int = Field(
        30,
        title="느린 EMA 기간",
        description="횡보 구간에서 적용되는 둔감한 EMA 기간입니다.",
        ge=5,
    )
    signal_threshold: float = Field(
        0.01,
        title="신호 임계값",
        description="가격이 KAMA를 몇 % 이상 돌파해야 신호로 인식할지 설정합니다.",
        ge=0,
    )
    timeframe: Literal["days", "weeks", "months"] = Field("days", title="시간 단위")
    qrycnt: int = Field(200, title="조회 캔들 수", ge=50)


class KAMA(BaseStrategyConditionOverseasStock):
    """카우프만 적응 이동평균 기반 추세 전략"""

    id: str = "KAMA"
    name: str = "해외주식 KAMA 적응 이동평균"
    description: str = (
        "KAMA는 시장 상황에 따라 민감도가 자동 조절되는 이동평균입니다. "
        "추세가 강하면 빠르게 반응하고, 횡보 구간에서는 노이즈를 걸러냅니다. "
        "가격이 KAMA를 상향 돌파하면 매수, 하향 돌파하면 매도 신호입니다."
    )
    securities: List[str] = ["ls-sec.co.kr"]
    weight: float = 0.55
    parameter_schema: Dict[str, object] = KAMAParams.model_json_schema()

    def __init__(
        self,
        *,
        appkey: Optional[str] = None,
        appsecretkey: Optional[str] = None,
        er_period: int = 10,
        fast_period: int = 2,
        slow_period: int = 30,
        signal_threshold: float = 0.01,
        timeframe: Literal["days", "weeks", "months"] = "days",
        qrycnt: int = 200,
    ) -> None:
        super().__init__()
        self.appkey = appkey
        self.appsecretkey = appsecretkey
        self.er_period = er_period
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.signal_threshold = signal_threshold
        self.timeframe = timeframe
        self.qrycnt = max(qrycnt, er_period + slow_period + 50)
        self._ls = None

    async def execute(self) -> BaseStrategyConditionResponseOverseasStockType:
        candles = await self._load_candles()
        if len(candles) < self.er_period + 10:
            raise ValueError("KAMA 계산에 필요한 데이터가 부족합니다")

        snapshots = self._calculate_kama(candles)
        last = snapshots[-1] if snapshots else None

        # 최근 신호 이벤트
        signals = [s for s in snapshots if s.signal != "hold"][-5:]

        success = last is not None and last.signal != "hold"

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
                "current_trend": last.trend if last else "sideways",
                "current_signal": last.signal if last else "hold",
                "signal_events": [asdict(s) for s in signals],
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

    def _calculate_kama(self, candles: List[Candle]) -> List[KAMASnapshot]:
        """
        KAMA 계산
        
        공식:
        1. Efficiency Ratio (ER) = Direction / Volatility
           - Direction = |Close[t] - Close[t-n]|
           - Volatility = Σ|Close[i] - Close[i-1]| (n기간)
        2. Smoothing Constant (SC) = [ER × (fast - slow) + slow]²
           - fast = 2/(fast_period+1), slow = 2/(slow_period+1)
        3. KAMA = KAMA[t-1] + SC × (Close - KAMA[t-1])
        """
        closes = [c["close"] for c in candles]
        snapshots: List[KAMASnapshot] = []

        fast_sc = 2 / (self.fast_period + 1)
        slow_sc = 2 / (self.slow_period + 1)

        kama: Optional[float] = None
        prev_kama: Optional[float] = None

        for i, candle in enumerate(candles):
            if i < self.er_period:
                snapshots.append(
                    KAMASnapshot(
                        date=candle["date"],
                        close=candle["close"],
                        kama=None,
                        efficiency_ratio=None,
                        trend="sideways",
                        signal="hold",
                    )
                )
                continue

            # Efficiency Ratio 계산
            direction = abs(closes[i] - closes[i - self.er_period])
            volatility = sum(
                abs(closes[j] - closes[j - 1])
                for j in range(i - self.er_period + 1, i + 1)
            )
            er = direction / volatility if volatility > 0 else 0

            # Smoothing Constant
            sc = (er * (fast_sc - slow_sc) + slow_sc) ** 2

            # KAMA 계산
            if kama is None:
                kama = closes[i]
            else:
                prev_kama = kama
                kama = kama + sc * (closes[i] - kama)

            # 추세 및 신호 판정
            trend: Literal["bullish", "bearish", "sideways"] = "sideways"
            signal: Literal["buy", "sell", "hold"] = "hold"

            if prev_kama is not None:
                if kama > prev_kama * (1 + 0.001):
                    trend = "bullish"
                elif kama < prev_kama * (1 - 0.001):
                    trend = "bearish"

            # 가격이 KAMA를 돌파할 때 신호
            threshold = kama * self.signal_threshold
            if closes[i] > kama + threshold and (i == 0 or closes[i-1] <= kama):
                signal = "buy"
            elif closes[i] < kama - threshold and (i == 0 or closes[i-1] >= kama):
                signal = "sell"

            snapshots.append(
                KAMASnapshot(
                    date=candle["date"],
                    close=candle["close"],
                    kama=round(kama, 4),
                    efficiency_ratio=round(er, 4),
                    trend=trend,
                    signal=signal,
                )
            )

        return snapshots


__all__ = ["KAMA", "KAMAParams"]
