# AI 설명 모듈 (KAN-12 구현체)

시뮬레이션 결과 JSON을 받아 **검증된** 자연어 설명을 돌려준다.
설계 근거는 [`../ai-service/docs/KAN-12-AI-응답규격.md`](../ai-service/docs/KAN-12-AI-응답규격.md).

백엔드(KAN-4)의 `POST /analyses/{id}/explanation` 이 실행하는 코드가 이것이다.

## 구조

```
explainer/
  schema.py      KAN-12 입력·출력 스키마 (Pydantic)
  prompt.py      시스템 프롬프트 + 입력 조립
  client.py      Gemini Flash 호출 → 검증 → 실패 시 1회 재생성
  guardrail.py   C2~C10 검증기. 이 프로젝트의 핵심
fixtures/        시뮬레이터 없이 돌리기 위한 예시 입출력
tests/           가드레일 테스트 (LLM 호출 없음)
run.py           CLI
```

## 실행

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
```

**API 키 없이 — 검증기만 확인**

```bash
./.venv/bin/python run.py fixtures/case1_small_gap.json --check fixtures/case1_response_good.json
./.venv/bin/python tests/test_guardrail.py
```

**실제 LLM 호출**

```bash
export GEMINI_API_KEY=...
./.venv/bin/python run.py fixtures/case1_small_gap.json
```

## 내부 HTTP 서비스 (KAN-17)

Spring 백엔드가 호출하는 엔드포인트다.

```bash
./.venv/bin/uvicorn explainer.api:app --port 8000
```

| 엔드포인트 | 용도 |
|---|---|
| `GET /health` | docker compose healthcheck. 모델명·키 유무 반환 |
| `POST /rag/answer` | 시뮬레이션 결과 JSON → AI 설명 |

**응답 규약** — 처리가 끝나면 항상 200이고, 설명이 나왔는지는 `status`로 구분한다.
Spring이 예외 분기 없이 결과 화면을 그릴 수 있게 하기 위해서다.

| status | HTTP | 뜻 |
|---|---|---|
| `OK` | 200 | 설명 생성·검증 통과 |
| `EXPLANATION_REJECTED` | 200 | 가드레일 2회 실패. **결과 화면은 그대로, 설명 영역만 안내 문구** |
| `EXPLANATION_UNAVAILABLE` | 200 | 모델 호출 실패·키 없음. 재시도 버튼 |
| `INVALID_INPUT` | 422 | 입력이 AI 설명 입력 규격과 안 맞음 |

`message`는 Spring이 사용자 문구로 바꿔 쓰라는 사유이고, `violations`에는 가드레일 위반
내역이 들어간다(로그·디버깅용).

## Docker

```bash
docker build -t ai-service .
docker run -p 8000:8000 -e GEMINI_API_KEY=... ai-service
```

`docker-compose.ai-service.yml`은 도윤의 compose에 옮겨 붙일 조각이다.
비밀값은 이미지에 넣지 않고 전부 환경변수로 주입한다.

## 가드레일이 하는 일

LLM 응답을 신뢰하지 않고 심문한다. 하나라도 ERROR면 재생성하고, 두 번째도 실패하면 반려한다.

| 검사 | 내용 |
|---|---|
| C1·C7·C9 | Pydantic이 파싱 단계에서 강제 (필수 필드, minItems, enum) |
| C2 | 모든 텍스트에 evidence가 있는가 |
| C3 | evidence의 JSON Pointer가 입력에 실제로 존재하는가 |
| C4 | **텍스트의 숫자가 입력에서 재현 가능한가** — 환각 탐지 |
| C5 | 금지 표현 6종 (확률·확정수익·상품권유·유도·시장전망) |
| C6 | 세 주기를 중복 없이 다루는가 |
| C8 | data_basis가 입력 meta와 일치하는가 |
| C10 | 선택된 주기를 반영했는가 (PRD 수용기준 4) |

### C4의 알려진 한계

숫자 대조는 정규식 기반이라 완벽하지 않다. 현재 규칙:

- 단위가 붙은 수치(`68만원`, `-23.8%`)와 소수는 **ERROR**
- 단위 없는 1~12 정수는 순번·개수일 수 있어 **WARN**
- 날짜(`2026-06-30`)는 검사에서 제외
- **파생값은 허용하지 않는다.** 덧셈·뺄셈·비율 환산 전부 ERROR다. 프롬프트도 같은 규칙이라
  (C4-a) 납입액 조정은 조정 후 총액이 아니라 `additional_monthly_required` 증감분으로만
  표현하게 되어 있다

한글 수사("세 주기")는 잡지 못한다. 시뮬레이터가 붙으면 실제 응답으로 오탐·미탐을 재조정할 것.

## 다음 할 일

- KAN-13 케이스 2~5 픽스처 추가 (`fixtures/`). 현재 케이스 1만 있다
- 실제 LLM 호출로 프롬프트 튜닝 — 프롬프트를 고칠 때마다 `tests/test_guardrail.py` 재실행
- KAN-11 확정 후 반영: `additional_monthly_required`, `CASH_DEPOSIT` 자산군, 위험 지표 3종
