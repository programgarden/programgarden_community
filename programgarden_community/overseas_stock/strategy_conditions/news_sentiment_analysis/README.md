# 해외주식 뉴스 감성 분석 (News Sentiment Analysis)

## 전략 ID
`OverseasStockNewsSentiment`

## 전략 기여자
ProgramGarden Team

## 간단한 설명
이 전략은 **최신 뉴스를 수집하고 LLM(OpenAI)을 통해 시장 추세에 긍정적인지 분석**해 주는 AI 기반 필터입니다.

단순한 차트 분석을 넘어, **실제 시장의 뉴스 흐름이 해당 종목에 호재인지 악재인지**를 인공지능이 판단하여 매매 의사결정을 돕습니다.

## 이 전략이 필요한 이유
- 차트만으로는 알 수 없는 **외부 이슈(실적 발표, 규제, 거시 경제 등)**가 주가에 큰 영향을 미칩니다.
- 수많은 뉴스를 일일이 읽고 판단하기에는 시간이 부족하며, **감정적인 해석이 개입될 여지**가 있습니다.
- `OverseasStockNewsSentiment`는 Google Custom Search와 OpenAI를 활용하여 **객관적이고 빠르게 뉴스 긍정도를 수치화**해 줍니다.

## 전략 상세 설명

### 어떤 방식으로 동작하나요?
- **뉴스 수집**: Google Custom Search API를 통해 해당 종목(티커)과 관련된 **최근 3일간의 뉴스(최대 10개)**를 실시간으로 수집한 뒤, 각 기사 링크에서 본문까지 추출합니다.
- **AI 분석**: 수집된 기사의 제목과 본문을 OpenAI GPT 모델에게 전달하여, 이 뉴스가 주가 상승에 도움이 되는지("YES" or "NO") 물어봅니다.
- **긍정 비율 판단**: 전체 수집된 뉴스 중 긍정적인 뉴스의 비율을 계산하여, 설정한 기준(`min_positive_rate`)을 넘으면 `success: True`를 반환합니다.

### 활용 시나리오
- **매수 전 최종 확인**: 기술적 지표가 매수 신호를 보냈을 때, **악재가 없는지 한 번 더 체크**하는 용도로 사용합니다.
- **시장 분위기 파악**: 특정 종목에 대해 현재 시장이 긍정적으로 반응하고 있는지 **정량적인 수치**로 확인하고 싶을 때 유용합니다.
- **뉴스 기반 트레이딩**: 긍정적인 뉴스가 쏟아질 때만 진입하는 **모멘텀 전략**의 필터로 활용할 수 있습니다.

## DSL 예시
```python
{
    "condition_id": "OverseasStockNewsSentiment",
    "parameters": {
        "google_api_key": "YOUR_GOOGLE_CUSTOM_SEARCH_KEY",
        "google_cse_id": "YOUR_PROGRAMMABLE_SEARCH_ENGINE_ID",
        "llm_api_key": "YOUR_OPENAI_KEY",
        "min_positive_rate": 0.6,
        "question_text": "Is this news article positive for the stock market trend? Answer with YES or NO."
    }
}
```

## 파라미터 설명
`google_api_key`, `google_cse_id`는 https://cafe.naver.com/programgarden/5638 블로그를 참고해서 발급받습니다. `llm_api_key`는 OpenAI API에서 발급받습니다.

| 이름 | 타입 | 기본값 | 설명 |
| --- | --- | --- | --- |
| `google_api_key` | str | - | Google Custom Search JSON API 키입니다. 'TEST' 입력 시 모의 데이터를 사용합니다. |
| `google_cse_id` | str | - | Programmable Search Engine ID(cx)입니다. 'TEST' 입력 시 모의 데이터를 사용합니다. |
| `llm_api_key` | str | - | OpenAI API 키입니다. 'TEST' 입력 시 모의 분석을 수행합니다. |
| `min_positive_rate` | float | 0.6 | 수집된 뉴스 중 긍정적인 뉴스의 최소 비율입니다 (0.0 ~ 1.0). 이 비율 이상이어야 통과합니다. |
| `question_text` | str | (기본 질문) | LLM에게 던질 질문입니다. 답변에 "YES"가 포함되면 긍정으로 간주합니다. |

## 전략 사용 시 주의사항
- **API 비용**: Google Custom Search API와 OpenAI API는 유료 서비스일 수 있으므로, 호출 횟수와 비용을 고려해야 합니다.
- **LLM의 한계**: AI 모델이 항상 정확한 판단을 내리는 것은 아니며, 뉴스 내용이 모호할 경우 잘못된 분석을 할 수도 있습니다.
- **뉴스 지연**: 검색되는 뉴스가 실시간 주가를 즉각 반영하지 못할 수 있습니다.
- **보조 지표로 활용**: 이 전략 단독으로 매매하기보다는, 기술적 분석과 함께 **보조적인 필터**로 사용하는 것을 권장합니다.
