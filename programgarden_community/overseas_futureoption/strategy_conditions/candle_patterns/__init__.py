"""
캔들스틱 패턴 (Candlestick Patterns) 해외선물 전략 조건

다양한 캔들스틱 패턴을 인식하여 롱/숏 포지션 신호를 제공합니다.
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
class PatternResult:
    date: str
    pattern_name: str
    pattern_type: Literal["bullish", "bearish", "neutral"]
    confidence: float  # 0.0 ~ 1.0
    description: str


class FuturesCandlePatternsParams(BaseModel):
    """캔들스틱 패턴 전략 파라미터"""

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
    min_confidence: float = Field(
        0.6,
        title="최소 신뢰도",
        description="패턴 인식의 최소 신뢰도입니다.",
        ge=0,
        le=1,
    )
    patterns_to_detect: List[str] = Field(
        default_factory=lambda: [
            "doji",
            "hammer",
            "inverted_hammer",
            "hanging_man",
            "shooting_star",
            "engulfing",
            "harami",
            "morning_star",
            "evening_star",
            "three_white_soldiers",
            "three_black_crows",
            "piercing_line",
            "dark_cloud_cover",
            "tweezer",
        ],
        title="감지할 패턴 목록",
    )
    timeframe: Literal["days", "weeks", "months"] = Field("days", title="시간 단위")
    qrycnt: int = Field(100, title="조회 캔들 수", ge=20)


class FuturesCandlePatterns(BaseStrategyConditionOverseasFutures):
    """캔들스틱 패턴 기반 해외선물 전략"""

    id: str = "FuturesCandlePatterns"
    name: str = "해외선물 캔들스틱 패턴"
    description: str = (
        "캔들스틱 패턴으로 선물의 반전/지속 신호를 파악합니다. "
        "강세 패턴 발생 시 롱, 약세 패턴 발생 시 숏 포지션을 고려합니다. "
        "신뢰도 기반으로 패턴 강도를 필터링합니다."
    )
    securities: List[str] = ["ls-sec.co.kr"]
    weight: float = 0.5
    parameter_schema: Dict[str, object] = FuturesCandlePatternsParams.model_json_schema()

    def __init__(
        self,
        *,
        appkey: Optional[str] = None,
        appsecretkey: Optional[str] = None,
        min_confidence: float = 0.6,
        patterns_to_detect: Optional[List[str]] = None,
        timeframe: Literal["days", "weeks", "months"] = "days",
        qrycnt: int = 100,
    ) -> None:
        super().__init__()
        self.appkey = appkey
        self.appsecretkey = appsecretkey
        self.min_confidence = min_confidence
        self.patterns_to_detect = patterns_to_detect or [
            "doji",
            "hammer",
            "inverted_hammer",
            "hanging_man",
            "shooting_star",
            "engulfing",
            "harami",
            "morning_star",
            "evening_star",
            "three_white_soldiers",
            "three_black_crows",
            "piercing_line",
            "dark_cloud_cover",
            "tweezer",
        ]
        self.timeframe = timeframe
        self.qrycnt = qrycnt
        self._ls = None

    async def execute(self) -> BaseStrategyConditionResponseOverseasFuturesType:
        candles = await self._load_candles()
        symbol = self.symbol.get("symbol", "") if self.symbol else ""
        exchcd = self.symbol.get("exchcd", "") if self.symbol else ""

        if len(candles) < 5:
            return {
                "condition_id": self.id,
                "description": self.description,
                "success": False,
                "symbol": symbol,
                "exchcd": exchcd,
                "product": self.product_type,
                "position_side": "flat",
                "weight": self.weight,
                "data": {"error": "패턴 인식에 필요한 데이터가 부족합니다", "candle_count": len(candles), "required": 5},
            }

        patterns = self._detect_patterns(candles)
        filtered = [p for p in patterns if p.confidence >= self.min_confidence]

        # 최근 패턴 기준으로 포지션 결정
        position_side: Literal["long", "short", "flat"] = "flat"
        recent_bullish = [p for p in filtered[-5:] if p.pattern_type == "bullish"]
        recent_bearish = [p for p in filtered[-5:] if p.pattern_type == "bearish"]

        if recent_bullish and (not recent_bearish or recent_bullish[-1].date >= recent_bearish[-1].date):
            position_side = "long"
        elif recent_bearish and (not recent_bullish or recent_bearish[-1].date >= recent_bullish[-1].date):
            position_side = "short"

        success = len(filtered) > 0

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
                "patterns": [asdict(p) for p in filtered],
                "recent_pattern": asdict(filtered[-1]) if filtered else None,
                "bullish_count": len(recent_bullish),
                "bearish_count": len(recent_bearish),
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

    def _detect_patterns(self, candles: List[Candle]) -> List[PatternResult]:
        patterns: List[PatternResult] = []

        for i in range(len(candles)):
            detected = self._check_patterns_at(candles, i)
            patterns.extend(detected)

        return patterns

    def _check_patterns_at(self, candles: List[Candle], idx: int) -> List[PatternResult]:
        results: List[PatternResult] = []
        c = candles[idx]
        body = abs(c["close"] - c["open"])
        upper_shadow = c["high"] - max(c["open"], c["close"])
        lower_shadow = min(c["open"], c["close"]) - c["low"]
        total_range = c["high"] - c["low"]

        if total_range == 0:
            return results

        # 도지 (Doji)
        if "doji" in self.patterns_to_detect:
            if body / total_range < 0.1:
                confidence = 1 - (body / total_range) / 0.1
                results.append(
                    PatternResult(
                        date=c["date"],
                        pattern_name="Doji",
                        pattern_type="neutral",
                        confidence=round(confidence, 2),
                        description="시가와 종가가 거의 같은 우유부단 패턴",
                    )
                )

        # 해머 (Hammer) - 하락 추세 후 강세 반전
        if "hammer" in self.patterns_to_detect:
            if (
                lower_shadow >= body * 2
                and upper_shadow < body * 0.3
                and body / total_range > 0.1
            ):
                confidence = min(lower_shadow / (body * 2), 1.0)
                results.append(
                    PatternResult(
                        date=c["date"],
                        pattern_name="Hammer",
                        pattern_type="bullish",
                        confidence=round(confidence, 2),
                        description="긴 아래꼬리, 강세 반전 신호",
                    )
                )

        # 역해머 (Inverted Hammer) - 하락 추세 후 강세 반전
        if "inverted_hammer" in self.patterns_to_detect:
            if (
                upper_shadow >= body * 2
                and lower_shadow < body * 0.3
                and body / total_range > 0.1
            ):
                confidence = min(upper_shadow / (body * 2), 1.0)
                results.append(
                    PatternResult(
                        date=c["date"],
                        pattern_name="Inverted Hammer",
                        pattern_type="bullish",
                        confidence=round(confidence, 2),
                        description="긴 윗꼬리, 강세 반전 신호 (확인 필요)",
                    )
                )

        # 교수형 (Hanging Man) - 상승 추세 후 약세 반전
        if "hanging_man" in self.patterns_to_detect and idx >= 3:
            prev_trend = sum(
                1 for j in range(idx - 3, idx) if candles[j]["close"] > candles[j]["open"]
            )
            if (
                prev_trend >= 2
                and lower_shadow >= body * 2
                and upper_shadow < body * 0.3
            ):
                confidence = min(lower_shadow / (body * 2), 1.0) * (prev_trend / 3)
                results.append(
                    PatternResult(
                        date=c["date"],
                        pattern_name="Hanging Man",
                        pattern_type="bearish",
                        confidence=round(confidence, 2),
                        description="상승 추세 후 해머 모양, 약세 반전 신호",
                    )
                )

        # 유성형 (Shooting Star) - 상승 추세 후 약세 반전
        if "shooting_star" in self.patterns_to_detect and idx >= 3:
            prev_trend = sum(
                1 for j in range(idx - 3, idx) if candles[j]["close"] > candles[j]["open"]
            )
            if (
                prev_trend >= 2
                and upper_shadow >= body * 2
                and lower_shadow < body * 0.3
            ):
                confidence = min(upper_shadow / (body * 2), 1.0) * (prev_trend / 3)
                results.append(
                    PatternResult(
                        date=c["date"],
                        pattern_name="Shooting Star",
                        pattern_type="bearish",
                        confidence=round(confidence, 2),
                        description="상승 추세 후 긴 윗꼬리, 약세 반전 신호",
                    )
                )

        # 장악형 (Engulfing) - 2봉 패턴
        if "engulfing" in self.patterns_to_detect and idx >= 1:
            prev = candles[idx - 1]
            # Bullish Engulfing
            if (
                prev["close"] < prev["open"]
                and c["close"] > c["open"]
                and c["open"] <= prev["close"]
                and c["close"] >= prev["open"]
            ):
                confidence = min((c["close"] - c["open"]) / (prev["open"] - prev["close"]), 1.0)
                results.append(
                    PatternResult(
                        date=c["date"],
                        pattern_name="Bullish Engulfing",
                        pattern_type="bullish",
                        confidence=round(confidence, 2),
                        description="음봉을 완전히 감싸는 양봉, 강세 반전",
                    )
                )
            # Bearish Engulfing
            if (
                prev["close"] > prev["open"]
                and c["close"] < c["open"]
                and c["open"] >= prev["close"]
                and c["close"] <= prev["open"]
            ):
                confidence = min((c["open"] - c["close"]) / (prev["close"] - prev["open"]), 1.0)
                results.append(
                    PatternResult(
                        date=c["date"],
                        pattern_name="Bearish Engulfing",
                        pattern_type="bearish",
                        confidence=round(confidence, 2),
                        description="양봉을 완전히 감싸는 음봉, 약세 반전",
                    )
                )

        # 잉태형 (Harami) - 2봉 패턴
        if "harami" in self.patterns_to_detect and idx >= 1:
            prev = candles[idx - 1]
            prev_body = abs(prev["close"] - prev["open"])
            # Bullish Harami
            if (
                prev["close"] < prev["open"]
                and c["close"] > c["open"]
                and c["open"] > prev["close"]
                and c["close"] < prev["open"]
                and body < prev_body * 0.5
            ):
                confidence = 1 - (body / prev_body)
                results.append(
                    PatternResult(
                        date=c["date"],
                        pattern_name="Bullish Harami",
                        pattern_type="bullish",
                        confidence=round(confidence, 2),
                        description="큰 음봉 안의 작은 양봉, 강세 반전",
                    )
                )
            # Bearish Harami
            if (
                prev["close"] > prev["open"]
                and c["close"] < c["open"]
                and c["open"] < prev["close"]
                and c["close"] > prev["open"]
                and body < prev_body * 0.5
            ):
                confidence = 1 - (body / prev_body)
                results.append(
                    PatternResult(
                        date=c["date"],
                        pattern_name="Bearish Harami",
                        pattern_type="bearish",
                        confidence=round(confidence, 2),
                        description="큰 양봉 안의 작은 음봉, 약세 반전",
                    )
                )

        # 샛별형 (Morning Star) - 3봉 패턴
        if "morning_star" in self.patterns_to_detect and idx >= 2:
            c1, c2 = candles[idx - 2], candles[idx - 1]
            c1_body = abs(c1["close"] - c1["open"])
            c2_body = abs(c2["close"] - c2["open"])
            if (
                c1["close"] < c1["open"]  # 첫째 봉 음봉
                and c2_body < c1_body * 0.3  # 둘째 봉 작은 몸통
                and c["close"] > c["open"]  # 셋째 봉 양봉
                and c["close"] > (c1["open"] + c1["close"]) / 2  # 첫째 봉 중간 이상
            ):
                confidence = min(body / c1_body, 1.0)
                results.append(
                    PatternResult(
                        date=c["date"],
                        pattern_name="Morning Star",
                        pattern_type="bullish",
                        confidence=round(confidence, 2),
                        description="하락 후 작은 봉과 강한 양봉, 강세 반전",
                    )
                )

        # 저녁별형 (Evening Star) - 3봉 패턴
        if "evening_star" in self.patterns_to_detect and idx >= 2:
            c1, c2 = candles[idx - 2], candles[idx - 1]
            c1_body = abs(c1["close"] - c1["open"])
            c2_body = abs(c2["close"] - c2["open"])
            if (
                c1["close"] > c1["open"]  # 첫째 봉 양봉
                and c2_body < c1_body * 0.3  # 둘째 봉 작은 몸통
                and c["close"] < c["open"]  # 셋째 봉 음봉
                and c["close"] < (c1["open"] + c1["close"]) / 2  # 첫째 봉 중간 이하
            ):
                confidence = min(body / c1_body, 1.0)
                results.append(
                    PatternResult(
                        date=c["date"],
                        pattern_name="Evening Star",
                        pattern_type="bearish",
                        confidence=round(confidence, 2),
                        description="상승 후 작은 봉과 강한 음봉, 약세 반전",
                    )
                )

        # 세 흰 병사 (Three White Soldiers) - 3봉 패턴
        if "three_white_soldiers" in self.patterns_to_detect and idx >= 2:
            c1, c2 = candles[idx - 2], candles[idx - 1]
            if (
                c1["close"] > c1["open"]
                and c2["close"] > c2["open"]
                and c["close"] > c["open"]
                and c2["open"] > c1["open"]
                and c2["close"] > c1["close"]
                and c["open"] > c2["open"]
                and c["close"] > c2["close"]
            ):
                results.append(
                    PatternResult(
                        date=c["date"],
                        pattern_name="Three White Soldiers",
                        pattern_type="bullish",
                        confidence=0.85,
                        description="연속 3개 양봉, 강한 상승 신호",
                    )
                )

        # 세 까마귀 (Three Black Crows) - 3봉 패턴
        if "three_black_crows" in self.patterns_to_detect and idx >= 2:
            c1, c2 = candles[idx - 2], candles[idx - 1]
            if (
                c1["close"] < c1["open"]
                and c2["close"] < c2["open"]
                and c["close"] < c["open"]
                and c2["open"] < c1["open"]
                and c2["close"] < c1["close"]
                and c["open"] < c2["open"]
                and c["close"] < c2["close"]
            ):
                results.append(
                    PatternResult(
                        date=c["date"],
                        pattern_name="Three Black Crows",
                        pattern_type="bearish",
                        confidence=0.85,
                        description="연속 3개 음봉, 강한 하락 신호",
                    )
                )

        # 관통형 (Piercing Line) - 2봉 패턴
        if "piercing_line" in self.patterns_to_detect and idx >= 1:
            prev = candles[idx - 1]
            prev_body = abs(prev["close"] - prev["open"])
            if (
                prev["close"] < prev["open"]
                and c["close"] > c["open"]
                and c["open"] < prev["low"]
                and c["close"] > (prev["open"] + prev["close"]) / 2
                and c["close"] < prev["open"]
            ):
                penetration = (c["close"] - prev["close"]) / prev_body
                results.append(
                    PatternResult(
                        date=c["date"],
                        pattern_name="Piercing Line",
                        pattern_type="bullish",
                        confidence=round(min(penetration, 1.0), 2),
                        description="음봉 후 갭다운 시작 양봉이 중간 이상 회복",
                    )
                )

        # 먹구름형 (Dark Cloud Cover) - 2봉 패턴
        if "dark_cloud_cover" in self.patterns_to_detect and idx >= 1:
            prev = candles[idx - 1]
            prev_body = abs(prev["close"] - prev["open"])
            if (
                prev["close"] > prev["open"]
                and c["close"] < c["open"]
                and c["open"] > prev["high"]
                and c["close"] < (prev["open"] + prev["close"]) / 2
                and c["close"] > prev["open"]
            ):
                penetration = (prev["close"] - c["close"]) / prev_body
                results.append(
                    PatternResult(
                        date=c["date"],
                        pattern_name="Dark Cloud Cover",
                        pattern_type="bearish",
                        confidence=round(min(penetration, 1.0), 2),
                        description="양봉 후 갭업 시작 음봉이 중간 이하 하락",
                    )
                )

        # 집게형 (Tweezer) - 2봉 패턴
        if "tweezer" in self.patterns_to_detect and idx >= 1:
            prev = candles[idx - 1]
            tolerance = total_range * 0.02
            # Tweezer Bottom
            if abs(c["low"] - prev["low"]) < tolerance:
                results.append(
                    PatternResult(
                        date=c["date"],
                        pattern_name="Tweezer Bottom",
                        pattern_type="bullish",
                        confidence=round(1 - abs(c["low"] - prev["low"]) / total_range, 2),
                        description="같은 저점에서 지지, 강세 반전",
                    )
                )
            # Tweezer Top
            if abs(c["high"] - prev["high"]) < tolerance:
                results.append(
                    PatternResult(
                        date=c["date"],
                        pattern_name="Tweezer Top",
                        pattern_type="bearish",
                        confidence=round(1 - abs(c["high"] - prev["high"]) / total_range, 2),
                        description="같은 고점에서 저항, 약세 반전",
                    )
                )

        return results


__all__ = ["FuturesCandlePatterns", "FuturesCandlePatternsParams"]
