from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from bs4 import BeautifulSoup
from programgarden_core import (
    BaseStrategyConditionOverseasStock,
    BaseStrategyConditionResponseOverseasStockType,
)
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class NewsSentimentAnalysisParams(BaseModel):
    google_api_key: str = Field(
        ...,
        title="Google API 키",
        description="Google Custom Search JSON API 키입니다.",
    )
    google_cse_id: str = Field(
        ...,
        title="Google 검색 엔진 ID",
        description="Google Programmable Search Engine ID (cx)입니다.",
    )
    llm_api_key: str = Field(
        ...,
        title="OpenAI API 키",
        description="OpenAI API 키입니다. 'TEST' 입력 시 모의 분석이 수행됩니다.",
    )
    min_positive_rate: float = Field(
        0.6,
        title="최소 긍정 비율",
        description="수집된 뉴스 중 긍정적/시장 친화적인 뉴스의 최소 비율입니다 (0.0 ~ 1.0).",
        ge=0.0,
        le=1.0,
    )
    question_text: str = Field(
        "Is this news article positive for the stock market trend? Answer with YES or NO.",
        title="질문 텍스트",
        description="LLM에게 물어볼 질문입니다. 답변에 YES가 포함되면 긍정으로 간주합니다.",
    )


class NewsSentimentAnalysis(BaseStrategyConditionOverseasStock):
    id: str = "OverseasStockNewsSentiment"
    name: str = "해외주식 뉴스 감성 분석"
    description: str = "최신 뉴스를 수집하고 LLM을 통해 시장 추세에 긍정적인지 분석합니다."
    weight: float = 0.5
    parameter_schema: Dict[str, object] = NewsSentimentAnalysisParams.model_json_schema()

    def __init__(
        self,
        *,
        google_api_key: str,
        google_cse_id: str,
        llm_api_key: str,
        min_positive_rate: float = 0.6,
        question_text: str = "Is this news article positive for the stock market trend? Answer with YES or NO.",
    ) -> None:
        super().__init__()
        self.google_api_key = google_api_key
        self.google_cse_id = google_cse_id
        self.llm_api_key = llm_api_key
        self.min_positive_rate = min_positive_rate
        self.question_text = question_text

    async def execute(self) -> BaseStrategyConditionResponseOverseasStockType:
        if not self.symbol:
            raise ValueError("symbol 정보가 필요합니다")

        # 해외주식의 경우 symbol이 티커(예: AAPL, TSLA)인 경우가 많음
        symbol_code = self.symbol.get("symbol", "")
        symbol_name = self.symbol.get("symbol_name", symbol_code)
        exchcd = self.symbol.get("exchcd", "")
        
        # 검색 쿼리 생성 (티커 + stock news)
        query = f"{symbol_name} stock news"

        articles = await self._fetch_news(query)
        
        if not articles:
            # 뉴스가 없으면 판단 불가 -> 실패 처리 또는 중립 처리
            # 여기서는 데이터 부족으로 False 처리
            return self._build_response(False, 0.0, [], "No news found")

        analysis_results = await self._analyze_articles(articles)

        positive_count = sum(1 for r in analysis_results if r["is_positive"])
        positive_rate = positive_count / len(articles) if articles else 0.0

        success = positive_rate >= self.min_positive_rate

        return self._build_response(success, positive_rate, analysis_results)

    def _build_response(
        self,
        success: bool,
        positive_rate: float,
        details: List[Dict[str, Any]],
        msg: str = "",
    ) -> BaseStrategyConditionResponseOverseasStockType:
        return {
            "condition_id": self.id,
            "description": self.description,
            "success": success,
            "symbol": self.symbol.get("symbol", "") if self.symbol else "",
            "exchcd": self.symbol.get("exchcd", "") if self.symbol else "",
            "product": self.product_type,
            "weight": self.weight,
            "data": {
                "positive_rate": round(positive_rate, 2),
                "min_positive_rate": self.min_positive_rate,
                "analyzed_count": len(details),
                "details": details,
                "message": msg,
            },
        }

    async def _fetch_news(self, query: str) -> List[Dict[str, str]]:
        """
        뉴스 API를 통해 뉴스를 수집합니다.
        Google Custom Search API를 사용합니다.
        """
        articles = await self._fetch_news_google(query)

        # 본문 수집 (병렬 처리)
        async with aiohttp.ClientSession() as session:
            tasks = [self._fetch_full_content(session, article) for article in articles]
            return await asyncio.gather(*tasks)

    async def _fetch_full_content(
        self, session: aiohttp.ClientSession, article: Dict[str, str]
    ) -> Dict[str, str]:
        link = article.get("link")
        if not link:
            return article

        try:
            async with session.get(link, timeout=10) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    soup = BeautifulSoup(html, "html.parser")

                    # 불필요한 태그 제거
                    for script in soup(
                        ["script", "style", "nav", "footer", "header", "iframe", "noscript"]
                    ):
                        script.decompose()

                    # 텍스트 추출
                    text = soup.get_text(separator=" ", strip=True)

                    # 본문이 너무 짧으면 snippet 유지, 길면 교체 (최대 4000자 제한)
                    if len(text) > 100:
                        article["content"] = text[:4000]
        except Exception as e:
            logger.warning(f"Failed to fetch content for {link}: {e}")

        return article

    async def _fetch_news_google(self, query: str) -> List[Dict[str, str]]:
        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "key": self.google_api_key,
            "cx": self.google_cse_id,
            "q": query,
            "num": 10,  # 최대 10개
            "dateRestrict": "d3",  # 지난 3일
            "sort": "date",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        items = data.get("items", [])
                        return [
                            {
                                "title": item.get("title", ""),
                                "content": item.get("snippet", ""),
                                "link": item.get("link", ""),
                            }
                            for item in items
                        ]
                    else:
                        logger.error(f"Google Custom Search API failed with status {resp.status}")
                        return []
        except Exception as e:
            logger.error(f"Error fetching news from Google: {e}")
            return []

    async def _analyze_articles(self, articles: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        tasks = [self._analyze_single_article(article) for article in articles]
        return await asyncio.gather(*tasks)

    async def _analyze_single_article(self, article: Dict[str, str]) -> Dict[str, Any]:
        text = f"Title: {article['title']}\nContent: {article['content']}"
        prompt = f"{self.question_text}\n\nNews Article:\n{text}"

        is_positive, reason = await self._call_openai(prompt)
        return {
            "title": article["title"],
            "link": article.get("link", ""),
            "is_positive": is_positive,
            "reason": reason,
        }

    async def _call_openai(self, prompt: str) -> Tuple[bool, str]:
        url = "https://api.openai.com/v1/responses"
        headers = {
            "Authorization": f"Bearer {self.llm_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "gpt-5-nano",
            "instructions": (
                "You are a smart financial analyst. "
                "Decide if the provided news article is positive for the stock market trend "
                "and answer with YES or NO plus a short justification."
            ),
            "input": prompt,
            "max_output_tokens": 800,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=payload) as resp:
                    data = await resp.json()
                    if resp.status == 200:
                        content = self._extract_response_text(data)
                        is_positive = "YES" in content.upper()
                        return is_positive, content
                    error_message = data.get("error", {}).get("message", "Unknown error")
                    return False, f"OpenAI Error {resp.status}: {error_message}"
        except Exception as e:
            return False, f"OpenAI Exception: {str(e)}"

    def _extract_response_text(self, data: Dict[str, Any]) -> str:
        """Extracts assistant text regardless of OpenAI response schema."""
        output = data.get("output", [])
        for item in output:
            if item.get("type") == "message":
                for content in item.get("content", []):
                    if content.get("type") in {"output_text", "text"}:
                        return content.get("text", "")
            if item.get("type") in {"output_text", "text"}:
                return item.get("text", "")

        # Fallback to legacy chat completion schema if the API ever routes differently
        choices = data.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", "")

        return ""


__all__ = ["NewsSentimentAnalysis"]