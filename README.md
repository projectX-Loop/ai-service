# ai-service

프로젝트X(Loop) 팀의 **AI 설명 서비스**. 시뮬레이션 결과 JSON을 받아 **검증된** 자연어 설명을 돌려준다.
Spring 백엔드가 내부 HTTP로 호출한다. 설계 문서는 [`docs/README.md`](docs/README.md)에서 시작.

**한 문장**: 계산은 결정론적 엔진이, LLM은 해석만 — AI가 계산 결과에 없는 말을 하면 기계가 반려한다.

## 구조

```
engine/            승준 KAN-11 계산 엔진 (LSJ v0.3/src/core 사본 + data/ 스냅샷). 승준만 수정. engine/README.md
explainer/
  calculate.py     POST /calculate 어댑터 — 기동 시 Dataset 1회 로드, ValidationError → 422
  schema.py        입력(KAN-9 §5) · 출력(KAN-9 §7 + evidence) 스키마. Pydantic
  prompt.py        시스템 프롬프트 (docs/KAN-12와 글자 단위 일치)
  guardrail.py     검증기 C2~C18 (C14 자동 부분 · C16~C18은 9/4 승준 변경점). 이 프로젝트의 핵심
  client.py        Gemini Flash 호출 → 가드레일 → 실패 시 1회 재생성
  api.py           FastAPI. POST /calculate · POST /rag/answer · GET /health
  public_api.py    공개 API(브라우저↔Spring) JSON 계약 — KAN-4. 실행 안 함, Spring DTO의 원본
  knowledge/       chunking · embedding · store(pgvector) · retrieve(결과 필드 → 개념 청크)
knowledge/         RAG 원재료 — 개념 문서 8개. 코드가 읽는 데이터 (KAN-15)
fixtures/          케이스 1~5 입력(승준 골든 P0·실험 X01f·X14c·X16d·X03a 실측) + t/ 검출용 6개 + inputs/(승준 페르소나) + 검수된 응답 4(케이스 1·3·4·5). 목록 fixtures/FIXTURES.md
tests/             guardrail 31 · knowledge 45 · retrieve 18 · explain 10 · public_api 47 · calculate 15 — 전부 LLM·DB 호출 없음
scripts/           ingest.py(적재) · search.py(검색 평가) · export_openapi.py(OpenAPI 생성) · smoke.py(통합 스모크, 9/5 합숙용)
db/                V1__knowledge.sql — knowledge_* 스키마. backend Flyway로 이관 예정
docs/              설계 문서 6개 (KAN-04·12·13·15·16·17) + 인덱스 + openapi/(공개·내부 OpenAPI, 예시 JSON 13개)
run.py             CLI
Dockerfile · docker-compose.ai-service.yml
```

## 실행

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
```

**키·DB 없이 — 전부 돈다** (검증기·청킹·검색 파일 폴백·가짜 Gemini)

```bash
for t in guardrail knowledge retrieve explain public_api calculate; do ./.venv/bin/python tests/test_$t.py; done
./.venv/bin/python run.py fixtures/case1_small_gap.json --check fixtures/case1_response_good.json
./.venv/bin/python scripts/ingest.py --dry-run
./.venv/bin/python scripts/export_openapi.py          # docs/openapi/*.json 재생성 (계약 바꿨을 때)
```

**실제 LLM 호출** (Gemini 키)

```bash
export GEMINI_API_KEY=...
./.venv/bin/python run.py fixtures/case1_small_gap.json
```

9/3 실측: 생성 1회 20~36초 · 기본 모델 `gemini-3.6-flash` · 요청 상한 `GEMINI_TIMEOUT_MS`(기본 45000). **무료 등급 키는 모델당 하루 20회**라 429가 나면 쿼터다. 상세 [`docs/KAN-17`](docs/KAN-17-내부HTTP계약.md) 「실측」.

**pgvector 적재·검색** — [`docs/KAN-16-지식저장소.md`](docs/KAN-16-지식저장소.md) 「실행」 참조. `DATABASE_URL` 없으면 `knowledge/*.md`를 직접 읽는 파일 폴백으로 동작한다.

## 9/5 통합(합숙) 체크리스트 — 도윤 compose 에 붙일 때

```bash
docker build -t ai-service .                                   # 9/4 로컬 빌드·기동 검증 (375MB, python:3.14-slim)
docker run -d -p 8000:8000 -e GEMINI_API_KEY=... ai-service     # DATABASE_URL 은 9/7 비움 (파일 폴백)
python3 scripts/smoke.py http://localhost:8000                 # LLM 없이 6항목: health·엔진 해시·/calculate 골든·422·결정론
python3 scripts/smoke.py http://localhost:8000 --llm           # /rag/answer 실호출 1회 (쿼터 1회)
```

| 확인 | 기대 |
|---|---|
| `GET /health` | `engine.data_hash` = `sha256:fa84100c…` (data_version 2026-09-02). Spring `data_snapshot(is_current).data_hash` 와 같아야 `SNAPSHOT_MISMATCH` 가 안 난다 |
| `POST /calculate` | P0 입력 → 골든 P0 원 단위 동일, ~10ms. 검증 실패 422 `{code, retryable:false, errors[]}` → Spring 400 변환 |
| `POST /rag/answer` | 항상 200 + `status`. 생성 20~60초 → Spring 타임아웃 **90초**. 429/503 은 `EXPLANATION_UNAVAILABLE` (결과 화면은 유지) |
| 쿼터 | 무료 키 **모델당 하루 20회**, 리셋 한국시간 16~17시. 통합 테스트에 `--llm` 을 아껴 쓸 것. 개발용으로 팀원 개인 Google 계정 무료 키를 하나 더 만들면 20회 추가 |
| 스냅샷 동결(9/6) | 승준이 `engine/data/` 6개 덮어쓰기 → `data_hash` 변경 → 도윤 DB 행 갱신 → **이미지 재빌드** |

## 내부 HTTP (KAN-17)

```bash
./.venv/bin/uvicorn explainer.api:app --port 8000
```

| 엔드포인트 | 용도 |
|---|---|
| `POST /calculate` | Kan-9 §2 입력 dict → §5 결과 JSON. 승준 엔진 `engine/` (9/4) |
| `POST /rag/answer` | KAN-11 결과 JSON (+ `focus`, `goal_amount`) → AI 설명 |
| `GET /health` | healthcheck. 모델명 · 키 유무 · retriever 종류 · 엔진 `data_hash` |

**응답 규약** — 처리가 끝나면 **항상 200**, 설명이 나왔는지는 `status`로 구분. Spring이 예외 분기 없이 결과 화면을 그릴 수 있게.

| `status` | HTTP | 뜻 |
|---|---|---|
| `OK` | 200 | 생성·검증 통과. `explanation` + `retrieved_refs` |
| `EXPLANATION_REJECTED` | 200 | 가드레일 2회 실패. **결과 화면 그대로**, 설명 영역만 `message` |
| `EXPLANATION_UNAVAILABLE` | 200 | 모델 호출 실패·키 없음. 재시도 |
| `INVALID_INPUT` | 422 | 본문이 KAN-9 §5 모양이 아님. `violations`에 필드 |

요청·응답 예시 JSON과 상세 규약은 [`docs/KAN-17-내부HTTP계약.md`](docs/KAN-17-내부HTTP계약.md). OpenAPI는 [`docs/openapi/ai-service.openapi.json`](docs/openapi/ai-service.openapi.json).

## 공개 API JSON (KAN-4 · 브라우저 ↔ Spring)

ai-service가 서빙하지 않는다. **Spring DTO의 원본**을 여기서 정하고(`explainer/public_api.py`) OpenAPI로 뽑아 도윤에게 준다 — 9/3 분담. 경로·상태 코드는 노션 §4(도윤), JSON 본문은 이 레포. `calculation`·`explanation`은 `schema.py` 모델 재사용이라 내부 계약과 어긋날 수 없다.

- [`docs/openapi/public-api.openapi.json`](docs/openapi/public-api.openapi.json) — 5 엔드포인트 · 35 스키마
- [`docs/openapi/examples/`](docs/openapi/examples/) — 요청·응답·오류 예시 13개. `tests/test_public_api.py`가 전부 계약과 대조
- 결정 4가지(오류 봉투·explanation 필드·`focus/goal_amount` 조립·samples 목록형)는 [`docs/KAN-04`](docs/KAN-04-API-명세.md) §3

## Docker

```bash
docker build -t ai-service . && docker run -p 8000:8000 -e GEMINI_API_KEY=... ai-service
```

`docker-compose.ai-service.yml`은 도윤 compose에 붙일 조각(`db` + `ai-service`). 비밀값은 이미지에 없고 전부 환경변수(`GEMINI_API_KEY` `GEMINI_MODEL` `DATABASE_URL` `EMBEDDING_MODEL` `EMBEDDING_DIM`). 배포 시 SSM `/loop/mvp/*`에서 주입.

## 가드레일이 하는 일

LLM 응답을 신뢰하지 않고 심문한다. ERROR가 하나라도 있으면 재생성, 두 번째도 실패하면 반려.
번호는 [`docs/KAN-12`](docs/KAN-12-AI-응답규격.md) 산출물 ④와 같다.

| 검사 | 내용 |
|---|---|
| C1·C7·C9 | Pydantic이 파싱 단계에서 강제 (필수 필드·minItems·enum) |
| C2 | 모든 문장에 evidence |
| C3 | evidence 실존 — JSON Pointer는 입력에, `chunk:` 참조는 retriever에 |
| C4 | **문장의 숫자가 입력에서 재현 가능한가** — 환각 탐지. 파생 계산 전면 금지 |
| C5 | 금지 표현 — 확률·확정수익·상품권유·유도·시장전망 + 성향 인격 단정(KAN-9 규칙 4)·지출 훈계(규칙 7) |
| C6 | M·Q·H 세 주기 장단점 전부 |
| C8 | assumptions_note에 기준 구간·환노출 |
| C10 | `focus` 반영 (PRD 수용기준 4). focus 없으면 WARN |
| C11 | summary에 기준 구간 언급 (KAN-9 규칙 6) |
| C12 | 조건절 없는 미래 단정 없음 (KAN-9 규칙 5) |
| C13 | 청크만 근거인 문장에 수치 없음 — 수치는 계산 결과에서만 |

### C4의 알려진 한계

숫자 대조는 정규식 기반이다. 단위 있는 수치·소수는 ERROR, 단위 없는 1~12 정수는 WARN, 날짜는 제외.
**파생값은 허용하지 않는다** — 납입액 조정은 `gap.extra_monthly_required` 증감분으로만. 한글 수사("세 주기")는 못 잡는다.
실호출이 붙으면 실제 응답으로 오탐·미탐을 재조정할 것.

## 미검증 · 다음

- ~~실제 LLM 호출~~ — 9/3 검증 완료(3/3 통과). 키는 **무료 등급 유지**(9/4 팀 결정, 하루 20회). 남은 것: 케이스 2 응답 픽스처(Gemini 503으로 미생성) · 통과율 표본
- ~~DB 적재·검색 실행~~ — 9/4 Docker 검증 완료(적재 28청크·검색 5/5). 절차는 docs/KAN-16 「실행」
- docker compose 연동 (도윤 compose 대기)
- ~~KAN-13 픽스처 2~5~~ — 9/4 입력 4 + 응답 3 작성(승준 실험 payload). 케이스 2 응답만 남음
- 레포 구조 — 노션 인프라 문서 `contracts/simulator/rag/app` vs 현재 `explainer/` + `engine/`(9/4 승준 엔진 수용). 도윤 확인 후
