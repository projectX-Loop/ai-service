# ai-service

프로젝트X(Loop) 팀의 **AI 설명 서비스**. 시뮬레이션 결과 JSON을 받아 **검증된** 자연어 설명을 돌려준다.
Spring 백엔드가 내부 HTTP로 호출한다. 설계 문서는 [`docs/README.md`](docs/README.md)에서 시작.

**한 문장**: 계산은 결정론적 엔진이, LLM은 해석만 — AI가 계산 결과에 없는 말을 하면 기계가 반려한다.

## 구조

```
engine/            승준 KAN-11 계산 엔진 (LSJ v0.3/src/core 사본 + data/ 스냅샷). 승준만 수정. engine/README.md
explainer/
  calculate.py     POST /calculate 어댑터 — 기동 시 Dataset 1회 로드, ValidationError → 422
  schema.py        입력(KAN-9 §5) · 출력(KAN-9 §7 + evidence) 스키마 + AskAnswer(질문답변 스트레치). Pydantic
  prompt.py        시스템 프롬프트 (docs/KAN-12와 글자 단위 일치) + 질문답변용 build_ask_message
  guardrail.py     검증기 C2~C18 (C14 자동 부분 · C16~C18은 9/4 승준 변경점) + validate_ask(질문답변, 구조검사 C6·C8·C10·C11 제외). 이 프로젝트의 핵심
  client.py        Gemini Flash 호출 → 가드레일 → 실패 시 1회 재생성. explain()·ask() 둘 다 같은 재시도 루프
  api.py           FastAPI. POST /calculate · POST /rag/answer · POST /rag/ask · GET /health
  public_api.py    공개 API(브라우저↔Spring) JSON 계약 — KAN-4 + KAN-24(질문답변 스트레치). 실행 안 함, Spring DTO의 원본
  knowledge/       chunking · embedding · store(pgvector) · retrieve(결과 필드 → 개념 청크)
knowledge/         RAG 원재료 — 개념 문서 8개. 코드가 읽는 데이터 (KAN-15)
fixtures/          케이스 1~5 입력(승준 골든 P0·실험 X01f·X14c·X16d·X03a 실측) + t/ 검출용 6개 + inputs/(승준 페르소나) + 검수된 응답 4(케이스 1·3·4·5). 목록 fixtures/FIXTURES.md
tests/             guardrail 32 · knowledge 45 · retrieve 18 · explain 10 · public_api 53 · calculate 15 · ask 13 = 186 — 전부 LLM·DB 호출 없음
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
for t in guardrail knowledge retrieve explain public_api calculate ask; do ./.venv/bin/python tests/test_$t.py; done
./.venv/bin/python run.py fixtures/case1_small_gap.json --check fixtures/case1_response_good.json
./.venv/bin/python scripts/ingest.py --dry-run
./.venv/bin/python scripts/export_openapi.py          # docs/openapi/*.json 재생성 (계약 바꿨을 때)
```

**실제 LLM 호출** (Gemini 키)

```bash
export GEMINI_API_KEY=...
./.venv/bin/python run.py fixtures/case1_small_gap.json
```

9/3 실측: 생성 1회 20~36초 · 기본 모델 `gemini-3.6-flash` · 요청 상한 `GEMINI_TIMEOUT_MS`(기본 45000). **9/5 밤 유료 등급 전환 완료** — 무료 등급 하루 20회 제한 해소(전환 전 기록은 `docs/KAN-17` 「실측」에 이력으로 남김).

**pgvector 적재·검색** — [`docs/KAN-16-지식저장소.md`](docs/KAN-16-지식저장소.md) 「실행」 참조. `DATABASE_URL` 없으면 `knowledge/*.md`를 직접 읽는 파일 폴백으로 동작한다.

## 통합 검증 체크리스트

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
| 쿼터 | **9/5 밤 유료 등급 전환 완료** — 하루 20회 제한 없음. 전환 전엔 무료 등급 모델당 하루 20회였음(이력) |
| 스냅샷 동결(9/6) | 승준이 `engine/data/` 6개 덮어쓰기 → `data_hash` 변경 → 도윤 DB 행 갱신 → **이미지 재빌드** |

## 내부 HTTP (KAN-17)

```bash
./.venv/bin/uvicorn explainer.api:app --port 8000
```

| 엔드포인트 | 용도 |
|---|---|
| `POST /calculate` | Kan-9 §2 입력 dict → §5 결과 JSON. 승준 엔진 `engine/` (9/4) |
| `POST /rag/answer` | KAN-11 결과 JSON (+ `focus`, `goal_amount`) → AI 설명 |
| `POST /rag/ask` | 결과 JSON + `question`(자유 텍스트) → 단발 답변. KAN-24(질문답변 스트레치, 9/5 도윤 구두 확인). 이력 없음 |
| `GET /health` | healthcheck. 모델명 · 키 유무 · retriever 종류 · 엔진 `data_hash` |

**응답 규약** — 처리가 끝나면 **항상 200**, 설명이 나왔는지는 `status`로 구분. Spring이 예외 분기 없이 결과 화면을 그릴 수 있게.

| `status` | HTTP | 뜻 |
|---|---|---|
| `OK` | 200 | 생성·검증 통과. `explanation`(또는 `answer`) + `retrieved_refs` |
| `EXPLANATION_REJECTED` / `ANSWER_REJECTED` | 200 | 가드레일 2회 실패. **결과 화면 그대로**, 설명·답변 영역만 `message` |
| `EXPLANATION_UNAVAILABLE` / `ANSWER_UNAVAILABLE` | 200 | 모델 호출 실패·키 없음. 재시도 |
| `INVALID_INPUT` | 422 | 본문이 규격과 안 맞음(`/rag/ask`는 `question` 누락 포함). `violations`에 필드 |

요청·응답 예시 JSON과 상세 규약은 [`docs/KAN-17-내부HTTP계약.md`](docs/KAN-17-내부HTTP계약.md)(`/rag/ask`는 KAN-24라 이 문서엔 미반영 — 아래 「질문답변 스트레치」 절 참고, `tests/test_ask.py`가 계약 검증). OpenAPI는 [`docs/openapi/ai-service.openapi.json`](docs/openapi/ai-service.openapi.json).

## 공개 API JSON (KAN-4 · 브라우저 ↔ Spring)

ai-service가 서빙하지 않는다. **Spring DTO의 원본**을 여기서 정하고(`explainer/public_api.py`) OpenAPI로 뽑아 도윤에게 준다 — 9/3 분담. 경로·상태 코드는 노션 §4(도윤), JSON 본문은 이 레포. `calculation`·`explanation`은 `schema.py` 모델 재사용이라 내부 계약과 어긋날 수 없다.

- [`docs/openapi/public-api.openapi.json`](docs/openapi/public-api.openapi.json) — 6 엔드포인트 · 40 스키마 (`POST /plans/{public_id}/questions` = KAN-24 질문답변 스트레치, 멀티턴 `history` 포함)
- [`docs/openapi/examples/`](docs/openapi/examples/) — 요청·응답·오류 예시 17개. `tests/test_public_api.py`가 전부 계약과 대조
- 결정 4가지(오류 봉투·explanation 필드·`focus/goal_amount` 조립·samples 목록형)는 [`docs/KAN-04`](docs/KAN-04-API-명세.md) §3

## Docker

```bash
docker build -t ai-service . && docker run -p 8000:8000 -e GEMINI_API_KEY=... ai-service
```

`docker-compose.ai-service.yml`은 로컬 pgvector 개발 전용이다. 운영 Compose·서비스 네트워크·비밀 주입은 infrastructure 저장소가 소유한다. 비밀값은 이미지에 포함하지 않으며, 운영 배포에서는 `GEMINI_API_KEY`를 infrastructure의 SSM 동기화로 주입한다.

## 가드레일이 하는 일

LLM 응답을 신뢰하지 않고 심문한다. ERROR가 하나라도 있으면 재생성, 두 번째도 실패하면 반려.
번호는 [`docs/KAN-12`](docs/KAN-12-AI-응답규격.md) 산출물 ④와 같다. `validate_ask()`(질문답변 스트레치)는 C2~C5·C12~C14·C16~C18을 그대로 재사용하고, Explanation 구조 전용인 C6·C8·C10·C11만 뺀다.

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

## 질문답변 스트레치 (`POST /rag/ask` · `/plans/{public_id}/questions`) — KAN-24

9/5 도윤 구두 확인("간단한 채팅이라도 있으면 좋겠다") 후 구현, **KAN-24로 사후 등록**(회의·PRD·기존 티켓엔 없던 범위). **9/7 아침 기준 ai-service·백엔드([projectX-Backend](https://github.com/projectX-Loop/projectX-Backend))·프론트 세 레포 전부 `main`에 merge 완료**, `VITE_ENABLE_RAG=true`로 프로덕션 전환까지 끝남 — 배포 준비 완료.

**내부 스키마 `AskAnswer.claim: Claim`**(9/5 밤, 전체 검토 중 발견·정정). 처음엔 `answer: Claim`이었는데, 내부 응답 `AskResponse.answer`가 `AskAnswer` 자체라 JSON이 `answer.answer`로 중첩돼 헷갈렸다. Spring 쪽 소비자가 아직 없는 지금 고치는 게 제일 싸서 바로 정정 — `claim`으로 이름 바꿈.

**정정(9/6 저녁)**: 위 "공개 계약은 애초에 평평해서 영향 없음"은 **틀린 판단이었다.** 내부 이름만 `claim`으로 바꿨을 뿐 `api.py` 핸들러가 `AskAnswer`(`{claim, retrieved_refs}`)를 그대로 `answer`에 직렬화하고 있어서, 실제 wire 응답은 여전히 `answer.claim.text`로 중첩돼 있었다 — 공개 계약(`public_api.QuestionResponse.answer: Claim`, 즉 `answer.text` 평평)과 실제 구현이 어긋난 상태로 `feature/rag-ask`에 남아 있었던 것. 이걸 검증하는 테스트도 없었다(`tests/test_ask.py`가 `explainer.client.ask()`만 직접 테스트하고 FastAPI 앱을 통한 실제 응답 바디는 확인한 적이 없었음). Spring·프론트 팀 전체 통합 재점검 중 발견 — `api.py`에서 `outcome.answer.claim.model_dump(...)`로 수정, `TestClient` 기반 회귀 테스트 2개 추가(`8a6eb3b`). 이제 Spring·프론트가 원래 짜둔 flat `answer.text` 가정이 실제로 맞다.

- **서버는 세션을 저장하지 않는다** — `agent_message`류 저장 없음(9/7 스코프 밖). 대신 **멀티턴 지원**: `ask()`가 `history`(이전 질문·답변 배열)를 받아 `build_ask_message`가 프롬프트 텍스트에 "이전 대화" 절로 얹는다. Gemini `contents`(role 구조)는 안 건드림 — 가드레일 재시도 루프가 이미 그 구조를 쓰고 있어 엉키는 걸 피함
- **가드레일은 안 바뀜** — history가 있어도 새 답변의 evidence는 여전히 계산 결과 JSON에서만 다시 찾아야 한다(프롬프트에 명시). 이전 답변을 근거로 삼는 것 금지
- `explain()`/`validate()`와 최대한 재사용: SYSTEM 프롬프트·숫자환각(C4)·evidence(C2·C3)·금지표현(C5) 등은 공유, Explanation 구조 전용 검사(C6·C8·C10·C11)만 제외
- 검증: `tests/test_ask.py` 13건(멀티턴 4건 포함, 가짜 Gemini) + **실제 Gemini 호출 검증 완료(9/5 밤, 유료 전환 후)** — 단발·멀티턴 둘 다 429 없이 통과, 멀티턴 질문에 모델이 이전 맥락을 정확히 이해하고 답변하는 것 확인. 표본 2건 모두 1회차 실패→2회차 통과(재시도 빈도는 표본이 더 필요)
- **⚠ 알려진 한계였던 것 — 개선 ①은 이제 구현됨.** 매 호출마다 계산 결과 JSON 전체 + 이전 질문·답변 전부를 재전송하면 세션이 길어질수록 페이로드·토큰이 거의 제곱으로 늘어나는 문제가 있었다. **Spring `ExplanationService`가 최근 5턴만 잘라서 ai-service에 보내도록 구현**(개선 ①, `MAX_HISTORY_TURNS`)해 해소 — `plan_explanation` 자체는 감사 로그로 전부 남기고, AI 호출에 실어 보내는 범위만 제한한다. 개선 ②(Gemini context caching)·③은 여전히 미구현이지만 ①만으로 9/7 MVP 스코프에선 충분
- **프론트는 `ChatPanel.vue`, plan당 대화 스레드 하나**(9/6 ERD 통합 결정으로 세션 목록·사이드바 설계는 되돌림 — `agent_session`+`agent_message` 2테이블을 `plan_explanation` 1테이블로 합치면서 세션 CRUD 자체가 없어졌기 때문). 스레드 안에서는 진짜 대화형(이전 질문·답변 기억), `plan.public_id` 스코프(로그인 없음), 프론트 `localStorage`(`src/chat/store.ts`)로 저장. **멀티턴 문맥은 이제 서버(Spring)가 `plan_explanation`에서 재구성**(9/6 도윤 `docs/plan-rag-design.md` 결정) — 프론트는 `question`만 보내고 `history`는 안 보냄.
- **Spring `/plans/{id}/questions` 구현 완료**(`projectX-Backend` `feat/plan-explanation`, PR [#11](https://github.com/projectX-Loop/projectX-Backend/pull/11), 리뷰 승인 대기) — ai-service `develop` merge는 완료(`f7c66f5`), frontend는 PR [#5](https://github.com/projectX-Loop/frontend/pull/5)로 리뷰 승인 대기. 3레포 dry-run merge·테스트는 확인 완료(충돌 없음), 실제 서버 3개 동시 기동한 end-to-end 검증은 아직 안 함

## 미검증 · 다음

- ~~실제 LLM 호출~~ — 9/3 `/rag/answer` 검증 완료(3/3 통과), 9/5 `/rag/ask` 단발·멀티턴 검증 완료(위 절 참고). ~~쿼터~~ — 9/5 밤 유료 등급 전환 완료(하루 20회 제한 해소). ~~케이스 2 응답 픽스처~~ — 9/5 밤 생성 완료(1회 통과). ~~통과율 표본~~ — 9/5 밤 5케이스×2회 실측: **case2가 3회 중 1회 반려**(가장 불안정, 표본 작음). 상세 `fixtures/FIXTURES.md`
- ~~DB 적재·검색 실행~~ — 9/4 Docker 검증 완료(적재 28청크·검색 5/5). 절차는 docs/KAN-16 「실행」
- ~~Docker 이미지 재빌드~~ — 9/5 밤 재검증 완료. `/rag/ask` 추가 후에도 빌드·기동·`/health`·`/calculate`·`/rag/ask`(422 및 정상 페이로드) 전부 정상, 골든 P0 값 일치
- 운영 Compose 연동은 infrastructure 저장소에서 관리
- ~~KAN-13 픽스처 2~5~~ — 9/4 입력 4 + 응답 3 작성(승준 실험 payload). 케이스 2 응답만 남음
- 레포 구조 — 노션 인프라 문서 `contracts/simulator/rag/app` vs 현재 `explainer/` + `engine/`(9/4 승준 엔진 수용). 도윤 확인 후
- ~~질문답변 스트레치(KAN-24)~~ — 위 절 참고. ai-service `develop` merge 완료. 남은 건 Spring PR #11·frontend PR #5 리뷰 승인(팀 대기)
