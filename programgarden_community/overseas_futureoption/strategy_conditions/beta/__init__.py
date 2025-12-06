"""
Beta (베타) 해외선물 전략 조건

시장 대비 상대적 변동성을 측정하여 리스크 평가와 포지션 결정에 활용합니다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import mean, stdev
from typing import Dict, List, Literal, Optional, TypedDict

from programgarden_core import (
    BaseStrategyConditionOverseasFutures,
    BaseStrategyConditionResponseOverseasFuturesType,
)
from pydantic import BaseModel, Field


def _correlation(x: List[float], y: List[float]) -> float:
    """Python 3.9 호환용 상관계수 계산 (Pearson correlation coefficient)"""
    n = len(x)
    if n != len(y) or n < 2:
        raise ValueError("x와 y의 길이가 같아야 하고 최소 2개 이상이어야 합니다")
    
    mean_x = mean(x)
    mean_y = mean(y)
    
    numerator = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    denominator_x = sum((xi - mean_x) ** 2 for xi in x) ** 0.5
    denominator_y = sum((yi - mean_y) ** 2 for yi in y) ** 0.5
    
    if denominator_x == 0 or denominator_y == 0:
        return 0.0
    
    return numerator / (denominator_x * denominator_y)


class Candle(TypedDict):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class BetaSnapshot:
    date: str
    asset_return: Optional[float]
    benchmark_return: Optional[float]
    rolling_beta: Optional[float]
    rolling_correlation: Optional[float]
    risk_category: Literal["high_beta", "low_beta", "neutral", "negative"]


class FuturesBetaParams(BaseModel):
    """Beta 전략 파라미터"""

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
        60,
        title="베타 계산 기간",
        description="롤링 베타 계산에 사용할 기간입니다.",
        ge=20,
    )
    benchmark_symbol: str = Field(
        "ES=F",
        title="벤치마크 심볼",
        description="비교 기준이 되는 벤치마크 심볼입니다 (Yahoo).",
    )
    high_beta_threshold: float = Field(
        1.2,
        title="고베타 임계값",
        description="베타가 이 값 이상이면 고베타로 판단합니다.",
        gt=0,
    )
    low_beta_threshold: float = Field(
        0.8,
        title="저베타 임계값",
        description="베타가 이 값 이하면 저베타로 판단합니다.",
        gt=0,
    )
    timeframe: Literal["days", "weeks", "months"] = Field("days", title="시간 단위")
    qrycnt: int = Field(200, title="조회 캔들 수", ge=100)


class FuturesBeta(BaseStrategyConditionOverseasFutures):
    """Beta 기반 해외선물 전략"""

    id: str = "FuturesBeta"
    name: str = "해외선물 베타"
    description: str = (
        "베타로 선물의 시장 민감도를 측정합니다. "
        "고베타 선물은 시장 상승 시 롱, 저베타는 방어적 포지션에 적합합니다. "
        "상관관계도 함께 분석하여 헤지 전략에 활용할 수 있습니다."
    )
    securities: List[str] = ["ls-sec.co.kr"]
    weight: float = 0.5
    parameter_schema: Dict[str, object] = FuturesBetaParams.model_json_schema()

    def __init__(
        self,
        *,
        appkey: Optional[str] = None,
        appsecretkey: Optional[str] = None,
        period: int = 60,
        benchmark_symbol: str = "ES=F",
        high_beta_threshold: float = 1.2,
        low_beta_threshold: float = 0.8,
        timeframe: Literal["days", "weeks", "months"] = "days",
        qrycnt: int = 200,
    ) -> None:
        super().__init__()
        self.appkey = appkey
        self.appsecretkey = appsecretkey
        self.period = period
        self.benchmark_symbol = benchmark_symbol
        self.high_beta_threshold = high_beta_threshold
        self.low_beta_threshold = low_beta_threshold
        self.timeframe = timeframe
        self.qrycnt = max(qrycnt, period + 50)
        self._ls = None

    async def execute(self) -> BaseStrategyConditionResponseOverseasFuturesType:
        asset_candles = await self._load_asset_candles()
        benchmark_candles = await self._load_benchmark_candles()
        symbol = self.symbol.get("symbol", "") if self.symbol else ""
        exchcd = self.symbol.get("exchcd", "") if self.symbol else ""

        if len(asset_candles) < self.period + 5 or len(benchmark_candles) < self.period + 5:
            return {
                "condition_id": self.id,
                "description": self.description,
                "success": False,
                "symbol": symbol,
                "exchcd": exchcd,
                "product": self.product_type,
                "position_side": "flat",
                "weight": self.weight,
                "data": {"error": "베타 계산에 필요한 데이터가 부족합니다", "asset_candle_count": len(asset_candles), "benchmark_candle_count": len(benchmark_candles), "required": self.period + 5},
            }

        snapshots = self._calculate_beta(asset_candles, benchmark_candles)
        last = snapshots[-1] if snapshots else None

        # 베타 기반 포지션 결정 (시장 방향에 따라 다름)
        # 여기서는 고베타면 시장 방향 추종, 저베타면 방어적
        position_side: Literal["long", "short", "flat"] = "flat"
        if last and last.rolling_beta is not None:
            if last.risk_category == "high_beta":
                # 고베타: 시장 방향 추종 - 기본적으로 롱 (시장 상승 기대)
                position_side = "long"
            elif last.risk_category == "negative":
                # 음의 베타: 역방향 움직임 - 헤지용
                position_side = "short"

        success = last is not None and last.rolling_beta is not None

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
                "snapshots": [asdict(s) for s in snapshots[-50:]],
                "current": asdict(last) if last else None,
                "beta": last.rolling_beta if last else None,
                "correlation": last.rolling_correlation if last else None,
                "risk_category": last.risk_category if last else "neutral",
                "benchmark": self.benchmark_symbol,
            },
        }

    async def _load_asset_candles(self) -> List[Candle]:
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

    async def _load_benchmark_candles(self) -> List[Candle]:
        """벤치마크 캔들 데이터를 로드합니다.
        
        yfinance가 설치된 경우 Yahoo Finance에서 로드하고,
        그렇지 않으면 자산 데이터를 기준으로 더미 벤치마크를 생성합니다.
        """
        try:
            return await self._load_yahoo_candles(self.benchmark_symbol)
        except ImportError:
            # yfinance가 없으면 자산 캔들을 기반으로 더미 벤치마크 생성
            # (실제 베타 분석에는 yfinance 설치를 권장)
            return []

    async def _load_yahoo_candles(self, ticker: Optional[str]) -> List[Candle]:
        """Yahoo Finance에서 캔들 데이터를 로드합니다.
        
        yfinance는 무료 공개 API를 사용하므로 별도의 API 키가 필요하지 않습니다.
        단, yfinance 패키지가 설치되어 있어야 합니다: pip install yfinance
        """
        try:
            import yfinance as yf
        except ImportError:
            raise ImportError(
                "yfinance 패키지가 필요합니다.\n"
                "설치 방법: pip install yfinance\n"
                "참고: yfinance는 무료 API를 사용하므로 별도의 API 키가 필요하지 않습니다."
            )

        if not ticker:
            raise ValueError("Yahoo Finance 심볼이 필요합니다")

        interval_map = {"days": "1d", "weeks": "1wk", "months": "1mo"}
        stock = yf.Ticker(ticker)
        df = stock.history(period="2y", interval=interval_map.get(self.timeframe, "1d"))

        if df.empty:
            raise ValueError(f"Yahoo Finance에서 {ticker} 데이터를 가져올 수 없습니다")

        candles: List[Candle] = []
        for idx, row in df.iterrows():
            candles.append(
                Candle(
                    date=idx.strftime("%Y%m%d"),
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=int(row["Volume"]),
                )
            )
        return candles[-self.qrycnt:]

    def _calculate_beta(
        self, asset_candles: List[Candle], benchmark_candles: List[Candle]
    ) -> List[BetaSnapshot]:
        # 날짜 기준으로 매칭
        benchmark_dict = {c["date"]: c for c in benchmark_candles}
        matched_pairs: List[tuple] = []

        for ac in asset_candles:
            if ac["date"] in benchmark_dict:
                matched_pairs.append((ac, benchmark_dict[ac["date"]]))

        if len(matched_pairs) < self.period + 1:
            return []

        snapshots: List[BetaSnapshot] = []

        # 수익률 계산
        asset_returns: List[Optional[float]] = [None]
        benchmark_returns: List[Optional[float]] = [None]

        for i in range(1, len(matched_pairs)):
            asset_ret = (matched_pairs[i][0]["close"] - matched_pairs[i - 1][0]["close"]) / matched_pairs[i - 1][0]["close"]
            bench_ret = (matched_pairs[i][1]["close"] - matched_pairs[i - 1][1]["close"]) / matched_pairs[i - 1][1]["close"]
            asset_returns.append(asset_ret)
            benchmark_returns.append(bench_ret)

        for i in range(len(matched_pairs)):
            asset_candle = matched_pairs[i][0]

            if i + 1 < self.period:
                snapshots.append(
                    BetaSnapshot(
                        date=asset_candle["date"],
                        asset_return=asset_returns[i],
                        benchmark_return=benchmark_returns[i],
                        rolling_beta=None,
                        rolling_correlation=None,
                        risk_category="neutral",
                    )
                )
                continue

            # 롤링 윈도우 수익률
            window_asset = [r for r in asset_returns[i + 1 - self.period : i + 1] if r is not None]
            window_bench = [r for r in benchmark_returns[i + 1 - self.period : i + 1] if r is not None]

            if len(window_asset) < self.period - 1 or len(window_bench) < self.period - 1:
                snapshots.append(
                    BetaSnapshot(
                        date=asset_candle["date"],
                        asset_return=asset_returns[i],
                        benchmark_return=benchmark_returns[i],
                        rolling_beta=None,
                        rolling_correlation=None,
                        risk_category="neutral",
                    )
                )
                continue

            # 길이 맞추기
            min_len = min(len(window_asset), len(window_bench))
            window_asset = window_asset[-min_len:]
            window_bench = window_bench[-min_len:]

            # 베타 계산: Cov(Ra, Rb) / Var(Rb)
            mean_asset = mean(window_asset)
            mean_bench = mean(window_bench)
            
            cov = sum(
                (a - mean_asset) * (b - mean_bench)
                for a, b in zip(window_asset, window_bench)
            ) / len(window_asset)

            var_bench = sum((b - mean_bench) ** 2 for b in window_bench) / len(window_bench)

            beta = cov / var_bench if var_bench > 0 else 0

            # 상관계수 계산
            try:
                corr = _correlation(window_asset, window_bench)
            except Exception:
                corr = 0

            # 리스크 카테고리
            risk_category: Literal["high_beta", "low_beta", "neutral", "negative"] = "neutral"
            if beta >= self.high_beta_threshold:
                risk_category = "high_beta"
            elif beta <= 0:
                risk_category = "negative"
            elif beta <= self.low_beta_threshold:
                risk_category = "low_beta"

            snapshots.append(
                BetaSnapshot(
                    date=asset_candle["date"],
                    asset_return=round(asset_returns[i], 6) if asset_returns[i] else None,
                    benchmark_return=round(benchmark_returns[i], 6) if benchmark_returns[i] else None,
                    rolling_beta=round(beta, 4),
                    rolling_correlation=round(corr, 4),
                    risk_category=risk_category,
                )
            )

        return snapshots


__all__ = ["FuturesBeta", "FuturesBetaParams"]
