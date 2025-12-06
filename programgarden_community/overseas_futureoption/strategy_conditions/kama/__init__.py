"""
KAMA (Kaufman Adaptive Moving Average) 해외선물 전략 조건

시장 효율성에 적응하는 이동평균선으로 롱/숏 포지션 신호를 제공합니다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, List, Literal, Optional, TypedDict

from programgarden_core import (
    BaseStrategyConditionOverseasFutures,
    BaseStrategyConditionResponseOverseasFuturesType,
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
    smoothing_constant: Optional[float]
    trend: Literal["bullish", "bearish", "neutral"]
    crossover: Optional[Literal["golden", "death"]]


class FuturesKAMAParams(BaseModel):
    """KAMA 전략 파라미터"""

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
        title="효율성 비율 기간",
        description="Efficiency Ratio를 계산하는 기간입니다.",
        ge=2,
    )
    fast_period: int = Field(
        2,
        title="빠른 SC 기간",
        description="빠른 Smoothing Constant 계산 기간입니다.",
        ge=1,
    )
    slow_period: int = Field(
        30,
        title="느린 SC 기간",
        description="느린 Smoothing Constant 계산 기간입니다.",
        ge=10,
    )
    timeframe: Literal["days", "weeks", "months"] = Field("days", title="시간 단위")
    qrycnt: int = Field(200, title="조회 캔들 수", ge=50)


class FuturesKAMA(BaseStrategyConditionOverseasFutures):
    """KAMA 기반 해외선물 전략"""

    id: str = "FuturesKAMA"
    name: str = "해외선물 KAMA"
    description: str = (
        "KAMA로 시장 효율성에 적응하는 추세를 파악합니다. "
        "가격 > KAMA면 롱, 가격 < KAMA면 숏 포지션을 고려합니다. "
        "효율성 비율(ER)이 높으면 추세장, 낮으면 횡보장입니다."
    )
    securities: List[str] = ["ls-sec.co.kr"]
    weight: float = 0.5
    parameter_schema: Dict[str, object] = FuturesKAMAParams.model_json_schema()

    def __init__(
        self,
        *,
        appkey: Optional[str] = None,
        appsecretkey: Optional[str] = None,
        er_period: int = 10,
        fast_period: int = 2,
        slow_period: int = 30,
        timeframe: Literal["days", "weeks", "months"] = "days",
        qrycnt: int = 200,
    ) -> None:
        super().__init__()
        self.appkey = appkey
        self.appsecretkey = appsecretkey
        self.er_period = er_period
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.timeframe = timeframe
        self.qrycnt = max(qrycnt, er_period + 50)
        self._ls = None

    async def execute(self) -> BaseStrategyConditionResponseOverseasFuturesType:
        candles = await self._load_candles()
        symbol = self.symbol.get("symbol", "") if self.symbol else ""
        exchcd = self.symbol.get("exchcd", "") if self.symbol else ""

        if len(candles) < self.er_period + 5:
            return {
                "condition_id": self.id,
                "description": self.description,
                "success": False,
                "symbol": symbol,
                "exchcd": exchcd,
                "product": self.product_type,
                "position_side": "flat",
                "weight": self.weight,
                "data": {"error": "KAMA 계산에 필요한 데이터가 부족합니다", "candle_count": len(candles), "required": self.er_period + 5},
            }

        snapshots = self._calculate_kama(candles)
        last = snapshots[-1] if snapshots else None

        # 포지션 방향 결정
        position_side: Literal["long", "short", "flat"] = "flat"
        if last and last.kama is not None:
            if last.trend == "bullish":
                position_side = "long"
            elif last.trend == "bearish":
                position_side = "short"

        # 크로스오버 또는 명확한 추세 시 success
        success = last is not None and (
            last.crossover is not None or last.trend in {"bullish", "bearish"}
        )

        symbol = self.symbol.get("symbol", "") if self.symbol else ""
        exchcd = self.symbol.get("exchcd", "") if self.symbol else ""

        return {
            "condition_id": self.id,
            "description": self.description,
            "success": success,
            "symbol": symbol,
            "exchcd": exchcd,
            "product": self.product_type,
            "position_side": position_side,
            "weight": self.weight,
            "data": {
                "snapshots": [asdict(s) for s in snapshots[-100:]],
                "current": asdict(last) if last else None,
                "trend": last.trend if last else "neutral",
                "efficiency_ratio": last.efficiency_ratio if last else None,
            },
        }

    async def _load_candles(self) -> List[Candle]:
        from programgarden_finance import LS, o3108

        if not self.appkey or not self.appsecretkey:
            raise ValueError("LS증권 API 키가 필요합니다")
        if not self.symbol:
            raise ValueError("종목 정보가 필요합니다")

        if self._ls is None:
            self._ls = LS.get_instance()
        
        if not self._ls.token_manager or not self._ls.token_manager.is_token_available():
            await self._ls.async_login(self.appkey, self.appsecretkey)

        shcode = self.symbol.get("symbol", "")
        gubun_map = {"days": "0", "weeks": "1", "months": "2"}

        request = self._ls.overseas_futureoption().chart().o3108(
            body=o3108.O3108InBlock(
                shcode=shcode,
                gubun=gubun_map.get(self.timeframe, "0"),
                qrycnt=self.qrycnt,
                sdate="",
                edate="",
                cts_date="",
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
        closes = [c["close"] for c in candles]
        snapshots: List[KAMASnapshot] = []

        fast_sc = 2 / (self.fast_period + 1)
        slow_sc = 2 / (self.slow_period + 1)

        kama = None

        for i, candle in enumerate(candles):
            if i + 1 < self.er_period:
                snapshots.append(
                    KAMASnapshot(
                        date=candle["date"],
                        close=candle["close"],
                        kama=None,
                        efficiency_ratio=None,
                        smoothing_constant=None,
                        trend="neutral",
                        crossover=None,
                    )
                )
                continue

            # Efficiency Ratio 계산
            change = abs(closes[i] - closes[i - self.er_period])
            volatility = sum(
                abs(closes[j] - closes[j - 1]) for j in range(i - self.er_period + 1, i + 1)
            )
            er = change / volatility if volatility > 0 else 0

            # Smoothing Constant 계산
            sc = (er * (fast_sc - slow_sc) + slow_sc) ** 2

            # KAMA 계산
            if kama is None:
                kama = candle["close"]
            else:
                kama = kama + sc * (candle["close"] - kama)

            # 추세 판단
            trend: Literal["bullish", "bearish", "neutral"] = "neutral"
            if candle["close"] > kama * 1.001:
                trend = "bullish"
            elif candle["close"] < kama * 0.999:
                trend = "bearish"

            # 크로스오버 감지
            crossover: Optional[Literal["golden", "death"]] = None
            if i > 0 and snapshots[i - 1].kama is not None:
                prev_kama = snapshots[i - 1].kama
                prev_close = closes[i - 1]
                if prev_close <= prev_kama and candle["close"] > kama:
                    crossover = "golden"
                elif prev_close >= prev_kama and candle["close"] < kama:
                    crossover = "death"

            snapshots.append(
                KAMASnapshot(
                    date=candle["date"],
                    close=candle["close"],
                    kama=round(kama, 4),
                    efficiency_ratio=round(er, 4),
                    smoothing_constant=round(sc, 4),
                    trend=trend,
                    crossover=crossover,
                )
            )

        return snapshots


__all__ = ["FuturesKAMA", "FuturesKAMAParams"]
