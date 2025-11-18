from __future__ import annotations

import math
from collections import deque
from dataclasses import asdict, dataclass
from statistics import pstdev
from typing import Dict, List, Literal, Optional, TypedDict

from programgarden_core import (
    BaseStrategyConditionOverseasFutures,
    BaseStrategyConditionResponseOverseasFuturesType,
)
from programgarden_finance import LS, o3108
from pydantic import BaseModel, Field, model_validator


class Candle(TypedDict):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class TrendPoint:
    date: str
    close: float
    log_return: Optional[float]
    standardized_return: Optional[float]
    ema_signal: Optional[float]
    rolling_volatility: Optional[float]


class FuturesEMARiskAdjustedTrendParams(BaseModel):
    """Parameter schema exposed to UI/DSL users."""

    ema_window: int = Field(
        20,
        title="EMA 기억 길이",
        description=(
            "논문에서 사용한 신호 timescale η를 캔들 개수로 표현한 값입니다. "
            "값이 작을수록 신호가 빠르게 반응하고, 크게 잡으면 더 부드러운 추세만 남습니다."
        ),
        ge=2,
        json_schema_extra={"example": 20},
    )

    volatility_window: int = Field(
        60,
        title="표준편차 계산 구간",
        description=(
            "EMA로 평활화하기 전에 표준화할 때 사용하는 캔들 개수입니다. "
            "EMA 길이보다 길어야 안정적으로 'σ 단위' 신호를 만들 수 있습니다."
        ),
        ge=10,
        json_schema_extra={"example": 60},
    )

    signal_threshold: float = Field(
        0.6,
        title="신호 임계값(σ)",
        description=(
            "EMA 신호가 몇 σ 이상이어야 '추세 감지 성공'으로 볼지 정합니다. "
            "0.6은 논문에서 보인 비대칭 P&L을 그대로 체험할 수 있도록 설정한 값입니다."
        ),
        ge=0.1,
        json_schema_extra={"example": 0.6},
    )

    transaction_cost: float = Field(
        0.05,
        title="거래 비용 θ",
        description=(
            "왕복 수수료·슬리피지를 모두 더한 비율입니다. "
            "값이 클수록 최적 timescale이 짧아지고 순위스 조정 P&L도 감소합니다."
        ),
        ge=0.0,
        json_schema_extra={"example": 0.05},
    )

    market_lambda: float = Field(
        0.01,
        title="시장 람다 (λ)",
        description=(
            "논문에서 auto-correlation을 표현한 λ 값입니다. "
            "긴 트렌드를 가진 선물(예: 지수/통화)은 0.01 근처, 짧은 트렌드는 0.05 수준이 자주 관찰됩니다."
        ),
        gt=0.0,
        json_schema_extra={"example": 0.01},
    )

    market_beta: float = Field(
        0.1,
        title="트렌드 강도 β₀",
        description=(
            "auto-correlation이 가격 분산에 얼마나 기여하는지를 나타냅니다. "
            "0.1은 역사적인 다우지수 데이터를 그대로 차용한 기본값입니다."
        ),
        ge=0.0,
        json_schema_extra={"example": 0.1},
    )

    timeframe: Literal["days", "weeks", "months"] = Field(
        "days",
        title="봉 간격",
        description="일봉/주봉/월봉 중 어떤 데이터를 EMA에 사용할지 고릅니다.",
    )

    qrycnt: int = Field(
        400,
        title="조회 캔들 수",
        description="EMA와 표준편차를 계산하기 위해 최소 수백 개의 봉을 확보하는 것이 안전합니다.",
        ge=120,
        json_schema_extra={"example": 400},
    )

    history_limit: int = Field(
        150,
        title="응답에 포함할 데이터 길이",
        description="UI에서 보여줄 최근 TrendPoint 개수입니다. 너무 길면 렌더링이 느려질 수 있습니다.",
        ge=30,
        json_schema_extra={"example": 150},
    )

    appkey: Optional[str] = Field(
        None,
        title="LS증권 앱키",
        description="LS Open API를 직접 호출할 때 필요한 인증 키입니다.",
    )

    appsecretkey: Optional[str] = Field(
        None,
        title="LS증권 앱시크릿",
        description="앱키와 한 쌍을 이루는 시크릿 키입니다.",
    )

    @model_validator(mode="after")
    def validate_windows(self) -> "FuturesEMARiskAdjustedTrendParams":  # pragma: no cover - simple guard
        if self.volatility_window <= self.ema_window:
            raise ValueError("volatility_window는 ema_window보다 길어야 합니다")
        return self


class FuturesEMARiskAdjustedTrend(BaseStrategyConditionOverseasFutures):
    """EMA 기반 추세 추적을 Grebenkov-Serror(2013) 모델과 연결한 전략."""

    id: str = "FuturesEMARiskAdjustedTrend"
    name: str = "해외선물 EMA 리스크 조정 추세"
    description: str = (
        "첨부 논문(arXiv:1308.5658)의 EMA 추세 추종 공식을 그대로 구현해, "
        "선물 가격의 표준화 수익률을 EMA로 부드럽게 만든 뒤 신호 강도를 σ 단위로 제공하는 전략입니다. "
        "신호가 강하면 position_side를 long/short로 추천하고, λ·β₀ 조합을 이용해 최적 timescale과 예상 순위스 조정 P&L까지 안내합니다."
    )
    securities = ["LS"]
    weight: float = 0.65
    parameter_schema: Dict[str, object] = FuturesEMARiskAdjustedTrendParams.model_json_schema()

    def __init__(
        self,
        *,
        ema_window: int = 20,
        volatility_window: int = 60,
        signal_threshold: float = 0.6,
        transaction_cost: float = 0.05,
        market_lambda: float = 0.01,
        market_beta: float = 0.1,
        timeframe: Literal["days", "weeks", "months"] = "days",
        qrycnt: int = 400,
        history_limit: int = 150,
        appkey: Optional[str] = None,
        appsecretkey: Optional[str] = None,
    ) -> None:
        super().__init__()
        if ema_window < 2:
            raise ValueError("ema_window는 2 이상이어야 합니다")
        if volatility_window <= ema_window:
            raise ValueError("volatility_window는 ema_window보다 크게 설정하세요")
        self.ema_window = ema_window
        self.volatility_window = volatility_window
        self.signal_threshold = signal_threshold
        self.transaction_cost = transaction_cost
        self.market_lambda = market_lambda
        self.market_beta = market_beta
        self.timeframe = timeframe
        self.qrycnt = max(qrycnt, volatility_window + ema_window + 50)
        self.history_limit = history_limit
        self.appkey = appkey
        self.appsecretkey = appsecretkey
        self._ls = LS.get_instance()

    async def execute(self) -> BaseStrategyConditionResponseOverseasFuturesType:
        if not self.symbol:
            raise ValueError("심볼 정보가 필요합니다")
        candles = await self._load_candles()
        if len(candles) < max(self.volatility_window, self.ema_window) + 5:
            raise ValueError("EMA를 계산하기에 봉 데이터가 부족합니다")
        points = self._build_trend_points(candles)
        trimmed_points = points[-self.history_limit :]
        latest_signal = next((pt.ema_signal for pt in reversed(points) if pt.ema_signal is not None), None)
        success = latest_signal is not None and abs(latest_signal) >= self.signal_threshold
        position_side: Literal["long", "short", "flat"] = "flat"
        if success and latest_signal is not None:
            position_side = "long" if latest_signal > 0 else "short"
        symbol_code = self.symbol.get("symbol", "")
        exchcd = self.symbol.get("exchcd", "")
        eta = self._eta_from_window(self.ema_window)
        optimal_eta = self._optimal_eta()
        risk_score = self._risk_adjusted_score_for_eta(eta)
        data = {
            "trend_points": [asdict(point) for point in trimmed_points],
            "latest_signal": latest_signal,
            "signal_threshold": self.signal_threshold,
            "eta": eta,
            "recommended_eta": optimal_eta,
            "recommended_window": self._window_from_eta(optimal_eta),
            "net_risk_adjusted_score": risk_score,
            "annualized_risk_adjusted_score": risk_score * math.sqrt(255),
            "market_lambda": self.market_lambda,
            "market_beta": self.market_beta,
            "transaction_cost": self.transaction_cost,
        }
        return {
            "condition_id": self.id,
            "description": self.description,
            "success": success,
            "symbol": symbol_code,
            "exchcd": exchcd,
            "product": self.product_type,
            "position_side": position_side,
            "weight": self.weight,
            "data": data,
        }

    async def _load_candles(self) -> List[Candle]:
        if not self.appkey or not self.appsecretkey:
            raise ValueError("LS 데이터를 조회하려면 appkey/appsecretkey가 필요합니다")
        await self._ensure_login()
        shcode = self.symbol.get("symbol") if self.symbol else None
        if not shcode:
            raise ValueError("선물 종목코드(symbol)가 비어 있습니다")
        gubun_map: Dict[str, str] = {"days": "0", "weeks": "1", "months": "2"}
        gubun = gubun_map.get(self.timeframe)
        if gubun is None:
            raise ValueError("timeframe은 days/weeks/months 중 하나여야 합니다")
        chart_req = (
            self._ls.overseas_futureoption()
            .chart()
            .o3108(
                body=o3108.O3108InBlock(
                    shcode=shcode,
                    gubun=gubun,
                    qrycnt=self.qrycnt,
                    sdate="",
                    edate="",
                    cts_date="",
                )
            )
        )
        responses = await chart_req.occurs_req_async()
        candles: List[Candle] = []
        for response in responses:
            for block in response.block1:
                candles.append(
                    Candle(
                        date=block.date,
                        open=block.open,
                        high=block.high,
                        low=block.low,
                        close=block.close,
                        volume=getattr(block, "volume", 0),
                    )
                )
        candles.sort(key=lambda item: item["date"])
        return candles

    async def _ensure_login(self) -> None:
        token_manager = self._ls.token_manager
        if token_manager and token_manager.is_token_available():
            await self._ls.async_ensure_token()
            return
        await self._ls.async_login(self.appkey, self.appsecretkey)

    def _build_trend_points(self, candles: List[Candle]) -> List[TrendPoint]:
        closes = [candle["close"] for candle in candles]
        log_returns: List[Optional[float]] = []
        for idx in range(1, len(closes)):
            prev = closes[idx - 1]
            curr = closes[idx]
            if prev <= 0 or curr <= 0:
                log_returns.append(None)
            else:
                log_returns.append(math.log(curr / prev))
        standardized, rolling_vols = self._standardize_returns(log_returns)
        ema_series = self._ema_series(standardized, self._eta_from_window(self.ema_window))
        points: List[TrendPoint] = []
        for idx in range(1, len(candles)):
            candle = candles[idx]
            points.append(
                TrendPoint(
                    date=candle["date"],
                    close=candle["close"],
                    log_return=log_returns[idx - 1],
                    standardized_return=standardized[idx - 1],
                    ema_signal=ema_series[idx - 1],
                    rolling_volatility=rolling_vols[idx - 1],
                )
            )
        return points

    def _standardize_returns(
        self, values: List[Optional[float]]
    ) -> tuple[List[Optional[float]], List[Optional[float]]]:
        window = deque(maxlen=self.volatility_window)
        standardized: List[Optional[float]] = []
        rolling_vols: List[Optional[float]] = []
        min_samples = max(5, self.volatility_window // 3)
        for value in values:
            if value is None:
                standardized.append(None)
                rolling_vols.append(None)
                continue
            window.append(value)
            if len(window) < min_samples:
                standardized.append(None)
                rolling_vols.append(None)
                continue
            deviation = pstdev(window)
            if deviation <= 0:
                standardized.append(None)
                rolling_vols.append(None)
                continue
            standardized.append(value / deviation)
            rolling_vols.append(deviation)
        return standardized, rolling_vols

    def _ema_series(self, values: List[Optional[float]], alpha: float) -> List[Optional[float]]:
        ema_values: List[Optional[float]] = []
        ema_prev: Optional[float] = None
        for value in values:
            if value is None:
                ema_values.append(None if ema_prev is None else ema_prev)
                continue
            if ema_prev is None:
                ema_prev = value
            else:
                ema_prev = alpha * value + (1 - alpha) * ema_prev
            ema_values.append(ema_prev)
        return ema_values

    def _eta_from_window(self, window: int) -> float:
        eta = 2.0 / (window + 1)
        return min(max(eta, 1e-4), 0.99)

    def _window_from_eta(self, eta: float) -> int:
        if eta <= 0:
            return self.ema_window
        return max(int(round(2 / eta - 1)), 2)

    def _optimal_eta(self) -> float:
        if self.transaction_cost <= 1e-5:
            base = max(self.market_lambda, 1e-4)
            return base * math.sqrt(1 + (2 * (self.market_beta ** 2)) / base)
        lower = max(1e-3, self.market_lambda / 10)
        upper = min(0.5, self.market_lambda * 10)
        steps = 500
        best_eta = lower
        best_score = float("-inf")
        for idx in range(steps + 1):
            candidate = lower + (upper - lower) * idx / steps
            score = self._risk_adjusted_score_for_eta(candidate)
            if score > best_score:
                best_score = score
                best_eta = candidate
        return max(best_eta, 1e-4)

    def _risk_adjusted_score_for_eta(self, eta: float) -> float:
        lambda_val = max(self.market_lambda, 1e-4)
        beta = max(self.market_beta, 0.0)
        theta = max(self.transaction_cost, 0.0)
        denom = math.sqrt((lambda_val + eta) ** 2 + 2 * (beta ** 2) * (lambda_val + eta))
        numerator = (beta ** 2) * (2 * eta) - math.sqrt(2 * math.pi) * theta * eta * (lambda_val + eta)
        if denom == 0:
            return 0.0
        return numerator / denom


__all__ = ["FuturesEMARiskAdjustedTrend"]
