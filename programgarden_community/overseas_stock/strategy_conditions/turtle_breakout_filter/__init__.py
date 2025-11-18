from __future__ import annotations
 
from dataclasses import dataclass
from statistics import mean
from typing import Dict, List, Optional, TypedDict

from programgarden_core import (
    BaseStrategyConditionOverseasStock,
    BaseStrategyConditionResponseOverseasStockType,
)
from programgarden_finance import LS, g3204
from pydantic import BaseModel, Field


class Candle(TypedDict):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class FilterCheck:
    name: str
    passed: bool
    reason: object


class TurtleBreakoutFilterParams(BaseModel):
    appkey: Optional[str] = Field(
        None,
        title="LS증권 앱키",
        description="LS OpenAPI 로그인과 시세 조회를 허용하는 사용자 키입니다. 실제 계좌나 모의계좌 모두 이 값을 넣어야 데이터를 받을 수 있습니다.",
    )

    appsecretkey: Optional[str] = Field(
        None,
        title="LS증권 앱시크릿",
        description="앱키와 짝을 이루는 비밀키입니다. 함께 입력해야 인증이 완료되고 차트 데이터를 내려받을 수 있습니다.",
    )

    entry_long_period: int = Field(
        20,
        title="롱 진입 기간",
        description="최근 며칠 동안의 최고가를 돌파해야 ‘추세가 생겼다’고 인정할지 정합니다. 값이 클수록 신호가 적고 보수적으로 움직입니다.",
        ge=5,
    )

    strong_entry_period: int = Field(
        55,
        title="강한 돌파 기간",
        description="보다 강력한 추세를 확인할 때 쓰는 긴 기간입니다. 55일과 같이 긴 기간을 두면 정말 큰 흐름만 걸러집니다.",
        ge=20,
    )

    exit_period: int = Field(
        10,
        title="청산 저점 기간",
        description="진입 이후 가격이 얼마나 내려오면 빠져나올지 정하는 기준입니다. 최근 저점을 크게 이탈하면 위험 신호로 표시됩니다.",
        ge=5,
    )

    atr_period: int = Field(
        20,
        title="ATR 기간",
        description="ATR(평균 진폭)을 계산할 때 사용할 일수입니다. 값이 클수록 더 부드러운 변동성 곡선을 얻습니다.",
        ge=5,
    )

    min_turnover: float = Field(
        1000000,
        title="최소 거래대금",
        description="최근 20일 동안 하루 평균 얼마만큼의 돈이 오갔는지를 평가합니다. 이 금액을 넘지 못하면 ‘거래가 너무 얇다’고 보고 제외합니다.",
        gt=0,
    )

    min_volume: int = Field(
        300000,
        title="최소 거래량",
        description="최근 20일 기준으로 하루에 얼마나 많은 주식이 손바뀜했는지 확인합니다. 최소 기준을 만족해야만 주문이 수월하다고 판단합니다.",
        gt=0,
    )

    min_atr: float = Field(
        0.5,
        title="최소 ATR",
        description=(
            "ATR(Average True Range, 평균 진폭)은 일정 기간 동안 주가가 하루에 평균적으로 얼마나 움직이는지를 측정합니다. "
            "ATR은 변동성을 나타내며, 다음 세 가지 중 가장 큰 값의 평균으로 계산됩니다:\n"
            "1) 당일 고가 - 당일 저가\n"
            "2) |당일 고가 - 전일 종가|\n"
            "3) |당일 저가 - 전일 종가|\n"
            "예: ATR이 2.5라면 해당 주식은 하루 평균 2.5달러 정도 움직입니다. "
            "ATR 값이 이 설정값(min_atr)보다 작으면 하루 움직임이 너무 작아 추세 돌파 전략에 적합하지 않다고 판단하여 후보에서 제외합니다. "
            "터틀 트레이딩에서는 ATR을 포지션 크기 결정과 손절 기준 설정에 핵심적으로 활용합니다."
        ),
        gt=0,
    )

    risk_per_trade: float = Field(
        0.01,
        title="거래당 리스크",
        description="한 번의 시도에서 계좌 전체 중 얼마나 위험을 감수할지를 비율로 정합니다. 예를 들어 0.01이면 전체 자산의 1%만 손실 위험에 노출시키겠다는 뜻입니다.",
        gt=0,
        le=0.05,
    )

    account_equity: float = Field(
        50000,
        title="계좌 총액",
        description="예수금 정보를 가져오지 못할 때 대신 사용할 총자산 금액입니다. 한 번에 살 기준 수량을 계산하는 바탕이 됩니다.",
        gt=1_000,
    )

    min_trade_size: int = Field(
        1,
        title="최소 거래 수량",
        description="거래소가 허용하는 최소 매매 수량입니다. 계산된 수량이 이 값보다 작으면 자동으로 올려 잡습니다.",
        ge=1,
    )

    qrycnt: int = Field(
        260,
        title="조회 캔들 수",
        description="차트 조회 시 몇 개의 일봉을 가져올지 정합니다. 돌파 기간보다 충분히 크게 잡아야 계산이 안정적입니다.",
        ge=120,
    )

    history_limit: int = Field(
        120,
        title="응답 캔들 수",
        description="결과 JSON 안에 몇 개의 일봉 정보를 담을지 정합니다. 너무 길면 UI가 느려지고, 너무 짧으면 흐름을 파악하기 어렵습니다.",
        ge=30,
    )


class TurtleBreakoutFilter(BaseStrategyConditionOverseasStock):
    id: str = "OverseasStockTurtleFilter"
    name: str = "해외주식 터틀 추세 스캐너"
    description: str = "유동성, ATR, 돌파 조건을 모두 체크해 터틀식 종목 후보를 골라줍니다."
    securities = ["ls-sec.co.kr"]
    weight: float = 0.7
    parameter_schema: Dict[str, object] = TurtleBreakoutFilterParams.model_json_schema()

    def __init__(
        self,
        *,
        appkey: Optional[str] = None,
        appsecretkey: Optional[str] = None,
        entry_long_period: int = 20,
        strong_entry_period: int = 55,
        exit_period: int = 10,
        atr_period: int = 20,
        min_turnover: float = 1000000,
        min_volume: int = 300000,
        min_atr: float = 0.5,
        risk_per_trade: float = 0.01,
        account_equity: float = 50000,
        min_trade_size: int = 1,
        qrycnt: int = 260,
        history_limit: int = 120,
    ) -> None:
        super().__init__()
        self.appkey = appkey
        self.appsecretkey = appsecretkey
        self.entry_long_period = entry_long_period
        self.strong_entry_period = strong_entry_period
        self.exit_period = exit_period
        self.atr_period = atr_period
        self.min_turnover = min_turnover
        self.min_volume = min_volume
        self.min_atr = min_atr
        self.risk_per_trade = risk_per_trade
        self.account_equity = account_equity
        self.min_trade_size = min_trade_size
        self.qrycnt = qrycnt
        self.history_limit = history_limit
        self._ls = LS.get_instance()

    async def execute(self) -> BaseStrategyConditionResponseOverseasStockType:
        if not self.symbol:
            raise ValueError("symbol 정보가 필요합니다")
        candles = await self._load_candles()
        if len(candles) < self.strong_entry_period + 5:
            raise ValueError("돌파 계산을 위한 캔들 수가 부족합니다")
        liquidity_check = self._liquidity_check(candles)
        volatility_check = self._volatility_check(candles)
        trend_check = self._trend_check(candles)
        exit_check = self._exit_check(candles)
        success = all(
            check.passed for check in (liquidity_check, volatility_check, trend_check, exit_check)
        )
        latest_close = candles[-1]["close"]
        atr_value = float(volatility_check.reason) if isinstance(volatility_check.reason, (int, float)) else 0.0
        entry_high_value = float(trend_check.reason) if isinstance(trend_check.reason, (int, float)) else 0.0
        exit_low_value = float(exit_check.reason) if isinstance(exit_check.reason, (int, float)) else 0.0
        unit_qty = self._suggest_unit_qty(atr_value)
        exchcd = self.symbol.get("exchcd", "")
        symbol_code = self.symbol.get("symbol", "")
        data = {
            "latest_close": latest_close,
            "entry_high": entry_high_value,
            "strong_entry_high": self._highest_high(candles, self.strong_entry_period),
            "exit_low": exit_low_value,
            "atr": atr_value,
            "avg_turnover": liquidity_check.reason if liquidity_check.name == "liquidity" else None,
            "avg_volume": self._avg_volume(candles),
            "distance_to_breakout_pct": self._distance_to_breakout(latest_close, entry_high_value),
            "suggested_unit_qty": unit_qty,
            "dollars_at_risk": round(self.account_equity * self.risk_per_trade, 2),
            "filters": {
                "liquidity": {
                    "pass": liquidity_check.passed,
                    "detail": liquidity_check.reason,
                    "threshold": {
                        "turnover": self.min_turnover,
                        "volume": self.min_volume,
                    },
                },
                "volatility": {
                    "pass": volatility_check.passed,
                    "detail": volatility_check.reason,
                    "threshold": self.min_atr,
                },
                "trend": {
                    "pass": trend_check.passed,
                    "entry_high": entry_high_value,
                    "trigger": "close > entry_high",
                },
                "exit": {
                    "pass": exit_check.passed,
                    "exit_low": exit_low_value,
                    "trigger": "close > exit_low",
                },
            },
            "history": candles[-self.history_limit :],
        }
        return {
            "condition_id": self.id,
            "description": self.description,
            "success": success,
            "symbol": symbol_code,
            "exchcd": exchcd,
            "product": self.product_type,
            "weight": self.weight,
            "data": data,
        }

    async def _load_candles(self) -> List[Candle]:
        if not self.appkey or not self.appsecretkey:
            raise ValueError("appkey와 appsecretkey를 모두 입력해야 합니다")
        await self._ensure_login()
        exchcd = self.symbol.get("exchcd") if self.symbol else None
        symbol = self.symbol.get("symbol") if self.symbol else None
        if not exchcd or not symbol:
            raise ValueError("symbol/exchcd 정보를 확인하세요")
        req = (
            self._ls.overseas_stock()
            .chart()
            .g3204(
                g3204.G3204InBlock(
                    sdate="",
                    edate="",
                    keysymbol=f"{exchcd}{symbol}",
                    exchcd=exchcd,
                    symbol=symbol,
                    gubun="2",
                    qrycnt=self.qrycnt,
                )
            )
        )
        responses = await req.occurs_req_async()
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
        candles.sort(key=lambda item: item["date"])
        return candles

    async def _ensure_login(self) -> None:
        if not self._ls.is_logged_in():
            await self._ls.async_login(self.appkey, self.appsecretkey)

    def _liquidity_check(self, candles: List[Candle]) -> FilterCheck:
        recent = candles[-20:] if len(candles) >= 20 else candles
        if not recent:
            return FilterCheck("liquidity", False, 0.0)
        avg_turnover = mean(candle["close"] * candle["volume"] for candle in recent)
        avg_volume = self._avg_volume(recent)
        passed = avg_turnover >= self.min_turnover and avg_volume >= self.min_volume
        detail = round(avg_turnover, 2)
        return FilterCheck("liquidity", passed, detail)

    def _avg_volume(self, candles: List[Candle]) -> float:
        if not candles:
            return 0.0
        volumes = [candle["volume"] for candle in candles]
        return round(mean(volumes), 2)

    def _volatility_check(self, candles: List[Candle]) -> FilterCheck:
        atr_value = self._atr(candles, self.atr_period)
        return FilterCheck("volatility", atr_value >= self.min_atr, round(atr_value, 4))

    def _trend_check(self, candles: List[Candle]) -> FilterCheck:
        entry_high = self._highest_high(candles, self.entry_long_period)
        latest_close = candles[-1]["close"]
        passed = latest_close > entry_high
        return FilterCheck("trend", passed, round(entry_high, 4))

    def _exit_check(self, candles: List[Candle]) -> FilterCheck:
        exit_low = self._lowest_low(candles, self.exit_period)
        latest_close = candles[-1]["close"]
        passed = latest_close > exit_low
        return FilterCheck("exit", passed, round(exit_low, 4))

    def _highest_high(self, candles: List[Candle], period: int) -> float:
        if len(candles) <= period + 1:
            window = candles[:-1]
        else:
            window = candles[-(period + 1) : -1]
        return max(candle["high"] for candle in window)

    def _lowest_low(self, candles: List[Candle], period: int) -> float:
        if len(candles) <= period + 1:
            window = candles[:-1]
        else:
            window = candles[-(period + 1) : -1]
        return min(candle["low"] for candle in window)

    def _atr(self, candles: List[Candle], period: int) -> float:
        trs: List[float] = []
        prev_close = candles[0]["close"]
        for candle in candles[1:]:
            high_low = candle["high"] - candle["low"]
            high_close = abs(candle["high"] - prev_close)
            low_close = abs(candle["low"] - prev_close)
            trs.append(max(high_low, high_close, low_close))
            prev_close = candle["close"]
        if len(trs) < period:
            return 0.0
        recent = trs[-period:]
        return sum(recent) / period

    def _suggest_unit_qty(self, atr_value: float) -> int:
        if atr_value <= 0:
            return 0
        dollars_at_risk = self.account_equity * self.risk_per_trade
        raw_qty = int(dollars_at_risk // max(atr_value, 1e-4))
        qty = max(self.min_trade_size, raw_qty)
        # 최소 거래 단위 배수로 맞춥니다.
        remainder = qty % self.min_trade_size
        if remainder:
            qty -= remainder
        return max(qty, self.min_trade_size)

    def _distance_to_breakout(self, latest_close: float, entry_high: float) -> float:
        if entry_high <= 0:
            return 0.0
        return round((latest_close / entry_high - 1.0) * 100, 3)


__all__ = ["TurtleBreakoutFilter"]
