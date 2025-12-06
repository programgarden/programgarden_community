"""
베타 (Beta) 전략 조건

개별 종목이 시장 대비 얼마나 민감하게 움직이는지 측정합니다.
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
class BetaSnapshot:
    date: str
    close: float
    stock_return: Optional[float]
    market_return: Optional[float]
    rolling_beta: Optional[float]
    rolling_correlation: Optional[float]
    risk_category: Literal["aggressive", "neutral", "defensive"]


class BetaParams(BaseModel):
    """베타 파라미터"""

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
        description="베타를 계산할 기간입니다. 보통 60일(약 3개월)을 사용합니다.",
        ge=20,
    )
    benchmark_exchcd: str = Field(
        "81",
        title="벤치마크 거래소",
        description="벤치마크 ETF의 거래소 코드 (NYSE/AMEX='81', NASDAQ='82')",
    )
    benchmark_symbol: str = Field(
        "SPY",
        title="벤치마크 심볼",
        description="시장 지수 대용 ETF (SPY, QQQ, IWM 등). SPY/IWM은 NYSE('81'), QQQ는 NASDAQ('82')",
    )
    aggressive_threshold: float = Field(
        1.2,
        title="공격적 임계값",
        description="베타가 이 값 이상이면 공격적(aggressive) 종목입니다.",
        gt=0,
    )
    defensive_threshold: float = Field(
        0.8,
        title="방어적 임계값",
        description="베타가 이 값 이하면 방어적(defensive) 종목입니다.",
        gt=0,
    )
    timeframe: Literal["days", "weeks", "months"] = Field("days", title="시간 단위")
    qrycnt: int = Field(300, title="조회 캔들 수", ge=100)


class Beta(BaseStrategyConditionOverseasStock):
    """베타 기반 시장 민감도 분석 전략"""

    id: str = "Beta"
    name: str = "해외주식 베타 분석"
    description: str = (
        "베타(β)는 개별 종목이 시장 대비 얼마나 민감하게 움직이는지를 나타냅니다. "
        "β > 1이면 시장보다 변동성이 크고(공격적), β < 1이면 안정적(방어적)입니다. "
        "포트폴리오 구성 시 리스크 조절에 활용됩니다."
    )
    securities: List[str] = ["ls-sec.co.kr"]
    weight: float = 0.5
    parameter_schema: Dict[str, object] = BetaParams.model_json_schema()

    def __init__(
        self,
        *,
        appkey: Optional[str] = None,
        appsecretkey: Optional[str] = None,
        period: int = 60,
        benchmark_exchcd: str = "81",
        benchmark_symbol: str = "SPY",
        aggressive_threshold: float = 1.2,
        defensive_threshold: float = 0.8,
        timeframe: Literal["days", "weeks", "months"] = "days",
        qrycnt: int = 300,
    ) -> None:
        super().__init__()
        self.appkey = appkey
        self.appsecretkey = appsecretkey
        self.period = period
        self.benchmark_exchcd = benchmark_exchcd
        self.benchmark_symbol = benchmark_symbol
        self.aggressive_threshold = aggressive_threshold
        self.defensive_threshold = defensive_threshold
        self.timeframe = timeframe
        self.qrycnt = max(qrycnt, period + 50)
        self._ls = None

    async def execute(self) -> BaseStrategyConditionResponseOverseasStockType:
        if not self.symbol:
            raise ValueError("종목 정보가 필요합니다")

        stock_exchcd = self.symbol.get("exchcd", "")
        stock_symbol = self.symbol.get("symbol", "")
        
        stock_candles = await self._load_candles(stock_exchcd, stock_symbol)
        market_candles = await self._load_candles(self.benchmark_exchcd, self.benchmark_symbol)

        if len(stock_candles) < self.period + 10 or len(market_candles) < self.period + 10:
            raise ValueError("베타 계산에 필요한 데이터가 부족합니다")

        # 날짜 기준으로 정렬 및 매칭
        stock_dict = {c["date"]: c for c in stock_candles}
        market_dict = {c["date"]: c for c in market_candles}
        common_dates = sorted(set(stock_dict.keys()) & set(market_dict.keys()))

        if len(common_dates) < self.period + 10:
            raise ValueError("종목과 벤치마크의 공통 거래일이 부족합니다")

        snapshots = self._calculate_beta(stock_dict, market_dict, common_dates)
        last = snapshots[-1] if snapshots else None

        success = last is not None and last.rolling_beta is not None

        symbol = stock_symbol
        exchcd = stock_exchcd

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
                "current_beta": last.rolling_beta if last else None,
                "current_correlation": last.rolling_correlation if last else None,
                "risk_category": last.risk_category if last else "neutral",
                "benchmark": self.benchmark_symbol,
                "period": self.period,
            },
        }

    async def _load_candles(self, exchcd: str, symbol: str) -> List[Candle]:
        from programgarden_finance import LS, g3204

        if not self.appkey or not self.appsecretkey:
            raise ValueError("LS증권 API 키가 필요합니다")

        if self._ls is None:
            self._ls = LS.get_instance()
        
        if not self._ls.token_manager or not self._ls.token_manager.is_token_available():
            await self._ls.async_login(self.appkey, self.appsecretkey)

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

    def _calculate_beta(
        self,
        stock_dict: Dict[str, Candle],
        market_dict: Dict[str, Candle],
        dates: List[str]
    ) -> List[BetaSnapshot]:
        """
        베타 계산
        
        β = Cov(stock, market) / Var(market)
        
        또는 회귀 분석:
        stock_return = α + β × market_return + ε
        """
        snapshots: List[BetaSnapshot] = []
        
        # 수익률 계산
        stock_returns: List[Optional[float]] = [None]
        market_returns: List[Optional[float]] = [None]
        
        for i in range(1, len(dates)):
            prev_date = dates[i - 1]
            curr_date = dates[i]
            
            prev_stock = stock_dict[prev_date]["close"]
            curr_stock = stock_dict[curr_date]["close"]
            prev_market = market_dict[prev_date]["close"]
            curr_market = market_dict[curr_date]["close"]
            
            if prev_stock > 0 and prev_market > 0:
                stock_returns.append((curr_stock - prev_stock) / prev_stock)
                market_returns.append((curr_market - prev_market) / prev_market)
            else:
                stock_returns.append(None)
                market_returns.append(None)
        
        # 롤링 베타 계산
        for i, date in enumerate(dates):
            stock_close = stock_dict[date]["close"]
            
            if i < self.period:
                snapshots.append(
                    BetaSnapshot(
                        date=date,
                        close=stock_close,
                        stock_return=stock_returns[i],
                        market_return=market_returns[i],
                        rolling_beta=None,
                        rolling_correlation=None,
                        risk_category="neutral",
                    )
                )
                continue
            
            # 기간 내 수익률 수집
            window_stock = []
            window_market = []
            for j in range(i - self.period + 1, i + 1):
                if stock_returns[j] is not None and market_returns[j] is not None:
                    window_stock.append(stock_returns[j])
                    window_market.append(market_returns[j])
            
            if len(window_stock) < self.period // 2:
                snapshots.append(
                    BetaSnapshot(
                        date=date,
                        close=stock_close,
                        stock_return=stock_returns[i],
                        market_return=market_returns[i],
                        rolling_beta=None,
                        rolling_correlation=None,
                        risk_category="neutral",
                    )
                )
                continue
            
            # 베타 및 상관계수 계산
            beta, correlation = self._compute_beta_correlation(window_stock, window_market)
            
            # 리스크 카테고리
            risk_category: Literal["aggressive", "neutral", "defensive"] = "neutral"
            if beta is not None:
                if beta >= self.aggressive_threshold:
                    risk_category = "aggressive"
                elif beta <= self.defensive_threshold:
                    risk_category = "defensive"
            
            snapshots.append(
                BetaSnapshot(
                    date=date,
                    close=stock_close,
                    stock_return=round(stock_returns[i], 6) if stock_returns[i] else None,
                    market_return=round(market_returns[i], 6) if market_returns[i] else None,
                    rolling_beta=round(beta, 4) if beta else None,
                    rolling_correlation=round(correlation, 4) if correlation else None,
                    risk_category=risk_category,
                )
            )
        
        return snapshots

    def _compute_beta_correlation(
        self, 
        stock_returns: List[float], 
        market_returns: List[float]
    ) -> tuple[Optional[float], Optional[float]]:
        """베타와 상관계수 계산"""
        n = len(stock_returns)
        if n < 2:
            return None, None
        
        # 평균
        mean_stock = sum(stock_returns) / n
        mean_market = sum(market_returns) / n
        
        # 공분산 및 분산
        cov = sum(
            (stock_returns[i] - mean_stock) * (market_returns[i] - mean_market)
            for i in range(n)
        ) / n
        
        var_market = sum(
            (market_returns[i] - mean_market) ** 2
            for i in range(n)
        ) / n
        
        var_stock = sum(
            (stock_returns[i] - mean_stock) ** 2
            for i in range(n)
        ) / n
        
        # 베타
        beta = cov / var_market if var_market > 0 else None
        
        # 상관계수
        std_stock = var_stock ** 0.5
        std_market = var_market ** 0.5
        correlation = cov / (std_stock * std_market) if std_stock > 0 and std_market > 0 else None
        
        return beta, correlation


__all__ = ["Beta", "BetaParams"]
