"""
파라볼릭 SAR (Parabolic Stop and Reverse) 전략 조건

추세 전환 시점을 점으로 표시하여 진입/청산 타이밍을 제공합니다.
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
class SARSnapshot:
    date: str
    close: float
    high: float
    low: float
    sar: Optional[float]
    trend: Literal["bullish", "bearish", "unknown"]
    reversal: bool  # 추세 전환이 발생했는지


class ParabolicSARParams(BaseModel):
    """파라볼릭 SAR 파라미터"""

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
    af_start: float = Field(
        0.02,
        title="가속 계수 시작값",
        description="AF(Acceleration Factor)의 시작값입니다. 추세가 지속될수록 증가합니다.",
        gt=0,
        le=0.1,
    )
    af_increment: float = Field(
        0.02,
        title="가속 계수 증분",
        description="새 고점/저점 갱신 시 AF 증가량입니다.",
        gt=0,
        le=0.1,
    )
    af_max: float = Field(
        0.2,
        title="가속 계수 최대값",
        description="AF의 최대값입니다. 이 이상 증가하지 않습니다.",
        gt=0,
        le=0.5,
    )
    timeframe: Literal["days", "weeks", "months"] = Field(
        "days",
        title="시간 단위",
    )
    qrycnt: int = Field(200, title="조회 캔들 수", ge=50)


class ParabolicSAR(BaseStrategyConditionOverseasStock):
    """파라볼릭 SAR 기반 추세 전환 감지 전략"""

    id: str = "ParabolicSAR"
    name: str = "해외주식 파라볼릭 SAR"
    description: str = (
        "파라볼릭 SAR은 추세 추종 및 전환 지점을 식별하는 지표입니다. "
        "SAR 점이 가격 아래에 있으면 상승 추세, 위에 있으면 하락 추세입니다. "
        "점이 반대편으로 넘어가면 추세 전환 신호입니다."
    )
    securities: List[str] = ["ls-sec.co.kr"]
    weight: float = 0.5
    parameter_schema: Dict[str, object] = ParabolicSARParams.model_json_schema()

    def __init__(
        self,
        *,
        appkey: Optional[str] = None,
        appsecretkey: Optional[str] = None,
        af_start: float = 0.02,
        af_increment: float = 0.02,
        af_max: float = 0.2,
        timeframe: Literal["days", "weeks", "months"] = "days",
        qrycnt: int = 200,
    ) -> None:
        super().__init__()
        self.appkey = appkey
        self.appsecretkey = appsecretkey
        self.af_start = af_start
        self.af_increment = af_increment
        self.af_max = af_max
        self.timeframe = timeframe
        self.qrycnt = qrycnt
        self._ls = None

    async def execute(self) -> BaseStrategyConditionResponseOverseasStockType:
        candles = await self._load_candles()
        if len(candles) < 10:
            raise ValueError("SAR 계산에 필요한 데이터가 부족합니다")

        snapshots = self._calculate_sar(candles)
        last = snapshots[-1] if snapshots else None
        
        # 최근 전환 이벤트
        reversals = [s for s in snapshots if s.reversal][-5:]
        
        # 성공 조건: 최근에 추세 전환 발생
        success = last is not None and (last.reversal or last.trend != "unknown")

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
                "current_trend": last.trend if last else "unknown",
                "latest_reversal": last.reversal if last else False,
                "reversal_events": [asdict(s) for s in reversals],
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

    def _calculate_sar(self, candles: List[Candle]) -> List[SARSnapshot]:
        """
        파라볼릭 SAR 계산
        
        알고리즘:
        1. 초기 추세 방향 결정 (첫 5개 캔들의 방향)
        2. SAR = 이전 극값 (상승시 저점, 하락시 고점)
        3. EP (Extreme Point) = 현재 추세의 최고/최저점
        4. AF (Acceleration Factor) = 새 EP 갱신시 증가
        5. 다음 SAR = 현재 SAR + AF × (EP - 현재 SAR)
        6. 가격이 SAR을 돌파하면 추세 전환
        """
        if len(candles) < 2:
            return []

        snapshots: List[SARSnapshot] = []
        
        # 초기 추세 결정 (첫 캔들들의 방향)
        is_bullish = candles[1]["close"] > candles[0]["close"]
        
        # 초기값 설정
        if is_bullish:
            sar = candles[0]["low"]
            ep = candles[0]["high"]
        else:
            sar = candles[0]["high"]
            ep = candles[0]["low"]
        
        af = self.af_start
        
        for i, candle in enumerate(candles):
            reversal = False
            
            if i == 0:
                snapshots.append(
                    SARSnapshot(
                        date=candle["date"],
                        close=candle["close"],
                        high=candle["high"],
                        low=candle["low"],
                        sar=None,
                        trend="unknown",
                        reversal=False,
                    )
                )
                continue
            
            # 추세 전환 체크
            if is_bullish:
                if candle["low"] < sar:
                    # 하락 전환
                    is_bullish = False
                    reversal = True
                    sar = ep  # 이전 EP가 새 SAR
                    ep = candle["low"]
                    af = self.af_start
            else:
                if candle["high"] > sar:
                    # 상승 전환
                    is_bullish = True
                    reversal = True
                    sar = ep
                    ep = candle["high"]
                    af = self.af_start
            
            # EP 갱신 및 AF 조정
            if not reversal:
                if is_bullish:
                    if candle["high"] > ep:
                        ep = candle["high"]
                        af = min(af + self.af_increment, self.af_max)
                else:
                    if candle["low"] < ep:
                        ep = candle["low"]
                        af = min(af + self.af_increment, self.af_max)
            
            current_sar = sar
            
            # 다음 SAR 계산
            sar = sar + af * (ep - sar)
            
            # SAR이 최근 캔들 범위를 침범하지 않도록 조정
            if is_bullish:
                sar = min(sar, candles[i-1]["low"], candle["low"])
            else:
                sar = max(sar, candles[i-1]["high"], candle["high"])
            
            snapshots.append(
                SARSnapshot(
                    date=candle["date"],
                    close=candle["close"],
                    high=candle["high"],
                    low=candle["low"],
                    sar=round(current_sar, 4),
                    trend="bullish" if is_bullish else "bearish",
                    reversal=reversal,
                )
            )
        
        return snapshots


__all__ = ["ParabolicSAR", "ParabolicSARParams"]
