"""
캔들 패턴 인식 (Candlestick Pattern Recognition) 전략 조건

주요 캔들 패턴을 감지하여 추세 전환 신호를 제공합니다.
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


PatternSignal = Literal["bullish", "bearish", "neutral"]


@dataclass
class PatternSnapshot:
    date: str
    close: float
    patterns: List[str]  # 감지된 패턴 목록
    primary_signal: PatternSignal
    pattern_strength: int  # 패턴 개수 (신뢰도)


class CandlePatternsParams(BaseModel):
    """캔들 패턴 파라미터"""

    body_threshold: float = Field(
        0.001,
        title="몸통 임계값",
        description="도지 판정을 위한 몸통 크기 임계값 (가격 대비 비율)",
        gt=0,
    )
    shadow_ratio: float = Field(
        2.0,
        title="꼬리 비율",
        description="망치형/유성형 판정을 위한 꼬리 대 몸통 비율",
        gt=0,
    )
    engulfing_margin: float = Field(
        1.01,
        title="장악형 마진",
        description="장악형 패턴에서 현재 캔들이 이전 캔들을 얼마나 초과해야 하는지",
        gt=1.0,
    )
    timeframe: Literal["days", "weeks", "months"] = Field("days", title="시간 단위")
    qrycnt: int = Field(200, title="조회 캔들 수", ge=50)
    appkey: Optional[str] = Field(None, title="LS증권 앱키")
    appsecretkey: Optional[str] = Field(None, title="LS증권 앱시크릿")


class CandlePatterns(BaseStrategyConditionOverseasStock):
    """주요 캔들 패턴 감지 전략"""

    id: str = "CandlePatterns"
    name: str = "해외주식 캔들 패턴 인식"
    description: str = (
        "도지, 망치형, 유성형, 장악형, 샛별/석별형, 적삼병/흑삼병 등 "
        "주요 캔들 패턴을 자동으로 감지하여 추세 전환 가능성을 알려줍니다."
    )
    securities: List[str] = ["ls-sec.co.kr"]
    weight: float = 0.45
    parameter_schema: Dict[str, object] = CandlePatternsParams.model_json_schema()

    def __init__(
        self,
        *,
        appkey: Optional[str] = None,
        appsecretkey: Optional[str] = None,
        body_threshold: float = 0.001,
        shadow_ratio: float = 2.0,
        engulfing_margin: float = 1.01,
        timeframe: Literal["days", "weeks", "months"] = "days",
        qrycnt: int = 200,
    ) -> None:
        super().__init__()
        self.appkey = appkey
        self.appsecretkey = appsecretkey
        self.body_threshold = body_threshold
        self.shadow_ratio = shadow_ratio
        self.engulfing_margin = engulfing_margin
        self.timeframe = timeframe
        self.qrycnt = qrycnt
        self._ls = None

    async def execute(self) -> BaseStrategyConditionResponseOverseasStockType:
        candles = await self._load_candles()
        if len(candles) < 10:
            raise ValueError("캔들 패턴 분석에 필요한 데이터가 부족합니다")

        snapshots = self._detect_patterns(candles)
        last = snapshots[-1] if snapshots else None

        # 패턴이 감지된 이벤트
        pattern_events = [s for s in snapshots if s.patterns][-20:]
        bullish_patterns = [s for s in pattern_events if s.primary_signal == "bullish"]
        bearish_patterns = [s for s in pattern_events if s.primary_signal == "bearish"]

        success = last is not None and bool(last.patterns)

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
                "snapshots": [asdict(s) for s in snapshots[-50:]],
                "current_patterns": last.patterns if last else [],
                "current_signal": last.primary_signal if last else "neutral",
                "pattern_events": [asdict(s) for s in pattern_events],
                "recent_bullish": [asdict(s) for s in bullish_patterns[-5:]],
                "recent_bearish": [asdict(s) for s in bearish_patterns[-5:]],
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

    def _detect_patterns(self, candles: List[Candle]) -> List[PatternSnapshot]:
        """모든 캔들 패턴 감지"""
        snapshots: List[PatternSnapshot] = []

        for i, candle in enumerate(candles):
            patterns: List[str] = []
            bullish_count = 0
            bearish_count = 0

            # 단일 캔들 패턴
            if self._is_doji(candle):
                patterns.append("DOJI")

            if self._is_hammer(candle):
                patterns.append("HAMMER")
                bullish_count += 1

            if self._is_inverted_hammer(candle):
                patterns.append("INVERTED_HAMMER")
                bullish_count += 1

            if self._is_shooting_star(candle):
                patterns.append("SHOOTING_STAR")
                bearish_count += 1

            if self._is_hanging_man(candle):
                patterns.append("HANGING_MAN")
                bearish_count += 1

            # 2개 캔들 패턴
            if i >= 1:
                prev = candles[i - 1]

                if self._is_bullish_engulfing(prev, candle):
                    patterns.append("BULLISH_ENGULFING")
                    bullish_count += 2

                if self._is_bearish_engulfing(prev, candle):
                    patterns.append("BEARISH_ENGULFING")
                    bearish_count += 2

                if self._is_piercing(prev, candle):
                    patterns.append("PIERCING")
                    bullish_count += 1

                if self._is_dark_cloud_cover(prev, candle):
                    patterns.append("DARK_CLOUD_COVER")
                    bearish_count += 1

            # 3개 캔들 패턴
            if i >= 2:
                prev2 = candles[i - 2]
                prev1 = candles[i - 1]

                if self._is_morning_star(prev2, prev1, candle):
                    patterns.append("MORNING_STAR")
                    bullish_count += 3

                if self._is_evening_star(prev2, prev1, candle):
                    patterns.append("EVENING_STAR")
                    bearish_count += 3

                if self._is_three_white_soldiers(prev2, prev1, candle):
                    patterns.append("THREE_WHITE_SOLDIERS")
                    bullish_count += 3

                if self._is_three_black_crows(prev2, prev1, candle):
                    patterns.append("THREE_BLACK_CROWS")
                    bearish_count += 3

            # 주요 신호 결정
            primary_signal: PatternSignal = "neutral"
            if bullish_count > bearish_count:
                primary_signal = "bullish"
            elif bearish_count > bullish_count:
                primary_signal = "bearish"

            snapshots.append(
                PatternSnapshot(
                    date=candle["date"],
                    close=candle["close"],
                    patterns=patterns,
                    primary_signal=primary_signal,
                    pattern_strength=len(patterns),
                )
            )

        return snapshots

    def _body_size(self, candle: Candle) -> float:
        """캔들 몸통 크기"""
        return abs(candle["close"] - candle["open"])

    def _upper_shadow(self, candle: Candle) -> float:
        """윗꼬리 크기"""
        return candle["high"] - max(candle["open"], candle["close"])

    def _lower_shadow(self, candle: Candle) -> float:
        """아랫꼬리 크기"""
        return min(candle["open"], candle["close"]) - candle["low"]

    def _is_bullish(self, candle: Candle) -> bool:
        """양봉 여부"""
        return candle["close"] > candle["open"]

    def _is_bearish(self, candle: Candle) -> bool:
        """음봉 여부"""
        return candle["close"] < candle["open"]

    # ==================== 단일 캔들 패턴 ====================

    def _is_doji(self, candle: Candle) -> bool:
        """도지: 시가 ≈ 종가"""
        body = self._body_size(candle)
        price = (candle["high"] + candle["low"]) / 2
        return body < price * self.body_threshold

    def _is_hammer(self, candle: Candle) -> bool:
        """망치형: 작은 몸통 + 긴 아랫꼬리 (바닥권 반등)"""
        body = self._body_size(candle)
        lower = self._lower_shadow(candle)
        upper = self._upper_shadow(candle)
        
        if body == 0:
            return False
        
        return (
            lower >= body * self.shadow_ratio and
            upper < body and
            self._is_bullish(candle)
        )

    def _is_inverted_hammer(self, candle: Candle) -> bool:
        """역망치형: 작은 몸통 + 긴 윗꼬리"""
        body = self._body_size(candle)
        lower = self._lower_shadow(candle)
        upper = self._upper_shadow(candle)
        
        if body == 0:
            return False
        
        return (
            upper >= body * self.shadow_ratio and
            lower < body
        )

    def _is_shooting_star(self, candle: Candle) -> bool:
        """유성형: 작은 몸통 + 긴 윗꼬리 (천장권 하락)"""
        body = self._body_size(candle)
        lower = self._lower_shadow(candle)
        upper = self._upper_shadow(candle)
        
        if body == 0:
            return False
        
        return (
            upper >= body * self.shadow_ratio and
            lower < body and
            self._is_bearish(candle)
        )

    def _is_hanging_man(self, candle: Candle) -> bool:
        """교수형: 망치형과 같지만 음봉 (천장권 하락)"""
        body = self._body_size(candle)
        lower = self._lower_shadow(candle)
        upper = self._upper_shadow(candle)
        
        if body == 0:
            return False
        
        return (
            lower >= body * self.shadow_ratio and
            upper < body and
            self._is_bearish(candle)
        )

    # ==================== 2캔들 패턴 ====================

    def _is_bullish_engulfing(self, prev: Candle, curr: Candle) -> bool:
        """상승 장악형: 음봉 후 더 큰 양봉이 감싸기"""
        return (
            self._is_bearish(prev) and
            self._is_bullish(curr) and
            curr["open"] < prev["close"] and
            curr["close"] > prev["open"] * self.engulfing_margin
        )

    def _is_bearish_engulfing(self, prev: Candle, curr: Candle) -> bool:
        """하락 장악형: 양봉 후 더 큰 음봉이 감싸기"""
        return (
            self._is_bullish(prev) and
            self._is_bearish(curr) and
            curr["open"] > prev["close"] and
            curr["close"] < prev["open"] / self.engulfing_margin
        )

    def _is_piercing(self, prev: Candle, curr: Candle) -> bool:
        """관통형: 음봉 후 양봉이 중간 이상 회복"""
        if not (self._is_bearish(prev) and self._is_bullish(curr)):
            return False
        
        mid = (prev["open"] + prev["close"]) / 2
        return curr["open"] < prev["close"] and curr["close"] > mid

    def _is_dark_cloud_cover(self, prev: Candle, curr: Candle) -> bool:
        """먹구름형: 양봉 후 음봉이 중간 이하로 하락"""
        if not (self._is_bullish(prev) and self._is_bearish(curr)):
            return False
        
        mid = (prev["open"] + prev["close"]) / 2
        return curr["open"] > prev["close"] and curr["close"] < mid

    # ==================== 3캔들 패턴 ====================

    def _is_morning_star(self, c1: Candle, c2: Candle, c3: Candle) -> bool:
        """샛별형: 음봉 + 작은 몸통 + 양봉 (바닥 반전)"""
        body1 = self._body_size(c1)
        body2 = self._body_size(c2)
        body3 = self._body_size(c3)
        
        if body1 == 0:
            return False
        
        return (
            self._is_bearish(c1) and
            body2 < body1 * 0.3 and  # 중간 캔들이 작음
            self._is_bullish(c3) and
            c3["close"] > (c1["open"] + c1["close"]) / 2
        )

    def _is_evening_star(self, c1: Candle, c2: Candle, c3: Candle) -> bool:
        """석별형: 양봉 + 작은 몸통 + 음봉 (천장 반전)"""
        body1 = self._body_size(c1)
        body2 = self._body_size(c2)
        body3 = self._body_size(c3)
        
        if body1 == 0:
            return False
        
        return (
            self._is_bullish(c1) and
            body2 < body1 * 0.3 and
            self._is_bearish(c3) and
            c3["close"] < (c1["open"] + c1["close"]) / 2
        )

    def _is_three_white_soldiers(self, c1: Candle, c2: Candle, c3: Candle) -> bool:
        """적삼병: 연속 3개의 상승 양봉"""
        return (
            self._is_bullish(c1) and
            self._is_bullish(c2) and
            self._is_bullish(c3) and
            c2["open"] > c1["open"] and c2["close"] > c1["close"] and
            c3["open"] > c2["open"] and c3["close"] > c2["close"]
        )

    def _is_three_black_crows(self, c1: Candle, c2: Candle, c3: Candle) -> bool:
        """흑삼병: 연속 3개의 하락 음봉"""
        return (
            self._is_bearish(c1) and
            self._is_bearish(c2) and
            self._is_bearish(c3) and
            c2["open"] < c1["open"] and c2["close"] < c1["close"] and
            c3["open"] < c2["open"] and c3["close"] < c2["close"]
        )


__all__ = ["CandlePatterns", "CandlePatternsParams"]
