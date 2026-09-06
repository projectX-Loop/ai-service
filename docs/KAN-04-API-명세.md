# KAN-4 — BE-AI API 설계 (Jira 제목 2026-09-03 변경 · assignee 성종현)

> 담당 성종현 (2026-09-03 assignee 변경) / 권도윤(Spring 통합·백엔드↔프론트 설계) · 의존 KAN-9·KAN-11·KAN-12
> **Jira 제목이 "BE-AI API 설계"로 좁혀졌다** — 이 티켓의 본체는 Spring↔ai-service 내부 계약(→ KAN-17). 다만 Jira 본문은 8/27 원문(공개 API 범위·"투자 성향" 입력)이 그대로라 제목과 어긋난다 — 본문 갱신은 도윤에게 요청.
> **분담 (2026-09-03 오후 도윤 카톡 · Jira·Notion 미기록)**: "API 설계는 **JSON 응답 방식까지 성종현이 정해서 도윤에게 주는 것**으로 진행. 백엔드-프론트 설계 자체는 도윤이 진행." → §3은 제안이 아니라 **도윤에게 넘기는 확정안**이다. Jira 댓글(9/3 15:22 도윤)의 "노션 개정 문서 §4와 대조해 차이만 알려달라" 요청과 KAN-19(최종 고정 게이트)는 그대로 유효.
> **상태 (2026-09-03 밤 · Jira 진행 중)**: AI 측 계약은 KAN-9 §5·§7에 정렬 완료(→ `KAN-17`). 공개 API JSON은 `docs/openapi/` + 예시 13으로 **도윤에게 전달 완료**(Jira 22:04), 노션 §4 대조 **충돌 0**(§3-1). 남은 것: 도윤 확인 2건(오류 봉투 한 모양 · explanation 응답 3필드) · **KAN-19 결정표가 최종 고정 게이트**(도윤).
> 이력: 8/29 초안(KAN-9 확정 전 추정) → 9/2 KAN-9 대조 → 9/3 현행화 → 9/3 오후 카톡 분담 반영. 옛 초안의 `/analyses`·TTL 저장·오류 12종은 KAN-9·ERD로 대체되어 폐기.

## 이 문서의 범위

KAN-4는 9/2 분담에서 사실상 둘로 나뉘었고, 9/3 카톡으로 공개 API의 **JSON 응답 방식**도 성종현 몫이 됐다.

| 부분 | 담당 | 문서 |
|---|---|---|
| 브라우저가 부르는 **공개 API** (Spring) | 응답 JSON 방식 = **성종현이 정해 도윤에게 전달** (9/3 카톡) · Spring·프론트 설계·구현 = 권도윤 | 이 문서의 §3. 노션 인프라 문서 §4(HTTP 층)와 차이 대조 후 최종 고정 |
| Spring이 부르는 **내부 AI 계약** (`POST /rag/answer`) | 성종현 | [`KAN-17-내부HTTP계약.md`](KAN-17-내부HTTP계약.md) |
| AI 입출력 스키마·가드레일 | 성종현 | [`KAN-12-AI-응답규격.md`](KAN-12-AI-응답규격.md) |

수용 기준 3개 중 "AI 응답은 KAN-12 규격을 따른다"와 "KAN-9 계약과 일치한다"가 성종현 몫이다.

## 1. 전체 호출 흐름 (노션 인프라 문서 기준)

```
브라우저 ─(입력 폼)─▶ Spring BE
                        ├─▶ POST /calculate   (ai-service · simulator, 승준)  → KAN-9 §5 결과 JSON
                        ├─▶ POST /rag/answer  (ai-service · rag, 종현)       → KAN-12 §7 설명 JSON
                        └─▶ RDS (plan 저장 · knowledge_* 는 ai-service 소유)
브라우저 ◀─(결과 화면: 간극·주기 비교·위험 + AI 설명)─ Spring BE
```

- ai-service는 **레포 1개·컨테이너 1개·FastAPI 1개** 안에 `simulator/`와 `rag/`가 같이 들어간다 (인프라 문서 §ai-service 구조). Spring은 이 둘만 부른다.
- **계산 결과는 저장하지 않는다** (ERD: 계산 층 테이블 없음). `plan`만 저장하고 캐시는 `hash(plan.version, snapshot_id, params)` 메모리 키.

## 2. 핵심 설계 — 계산과 설명은 분리해서 부른다

인프라 문서가 `/calculate`와 `/rag/answer`를 별도 호출로 그린 것과 같은 이유다.

1. **PRD 수용 기준 2** — 결과 화면에 간극·자산추이·비용·위험이 "모두 표시"되어야 한다. LLM이 죽어도 이 화면은 떠야 하므로 설명과 묶으면 안 된다.
2. **PRD 수용 기준 6 "3분 이내"** — 결과를 먼저 그리고 설명을 나중에 채우면 체감 대기가 짧다.
3. ai-service `/rag/answer`는 실패해도 **항상 200 + `status`** 로 돌아온다(→ KAN-17). Spring은 예외 분기 없이 결과 화면을 유지하고 설명 영역만 바꾼다.

## 3. 공개 API — JSON 응답 방식 (9/3 카톡 분담: 성종현 확정 → 도윤 전달)

**원본은 코드다.** [`explainer/public_api.py`](../explainer/public_api.py)가 JSON 계약(Pydantic), [`scripts/export_openapi.py`](../scripts/export_openapi.py)가 거기서 [`openapi/public-api.openapi.json`](openapi/public-api.openapi.json)을 뽑고, [`openapi/examples/`](openapi/examples/)의 예시 13개를 [`tests/test_public_api.py`](../tests/test_public_api.py)가 계약과 대조한다(47건). **도윤에게 주는 것 = OpenAPI 파일 + 예시 폴더.** 계산 결과(`Calculation`)와 AI 설명(`Explanation`)은 `schema.py` 모델을 그대로 재사용하므로 어휘가 갈라질 수 없다.

경로·상태 코드·흐름은 노션 「프론트-백엔드 계약 정리」 §4(도윤 9/3 14:30)를 그대로 따른다. 입력은 **KAN-9 §2 그대로**(8필드: `goal.amount`, `goal.horizon_months`, `funds.initial`, `funds.monthly`, `alloc.initial`, `alloc.monthly`, `portfolio.assets[]`, `rebalancing.focus`). 투자 성향은 받지 않는다(확정 ⑪ — 배분율에서 파생). v0.3 필드는 400 `UNSUPPORTED_FIELD`.

| 메서드·경로 | HTTP | 응답 JSON | 예시 |
|---|---|---|---|
| `POST /api/v1/plans` | 201 · 400 · 500 · 502 | `PlanResponse` = `{plan{public_id, data_snapshot_id, created_at, inputs}, calculation}` — `calculation`은 analyze() 출력 그대로(Kan-9 §5) | `plans.request.P0` → `plans.response.P0` |
| `GET /api/v1/plans/{public_id}` | 200 · 404 · 502 | 위와 같음. 저장된 inputs로 **재계산**(결과 미저장) | `plans.response.P0` |
| `POST /api/v1/plans/{public_id}/explanation` | 200(항상) · 404 · 502 | `ExplanationResponse` = `{status, explanation, message}` — `status`로 분기, OK면 `explanation`(Kan-9 §7 + evidence) | `explanation.response.ok / rejected / unavailable` |
| `GET /api/v1/universe` | 200 | `{snapshot{data_version, data_hash, window, safe_rate_annual_pct}, assets[{code, display_name, instrument, group, tax_class}]}` — 필드명은 §5 `meta`와 동일 | `universe.response` |
| `GET /api/v1/samples` | 200 | `{samples[{id, label, inputs}]}` — `inputs`를 그대로 `POST /plans`에 | `samples.response` |

**JSON 결정 4가지** — 노션 §4에 없거나 모호했던 자리를 채운 것. 도윤에게 전달하는 핵심.

1. **오류 봉투는 항상 하나의 모양** `{code, message, retryable, field?, errors?[], public_id?, max_months?}`. 검증 오류 여러 건은 봉투 `code=VALIDATION_ERROR` + `errors[{code, field, message}]`(엔진 `ValidationError.errors` 모양). 노션 §4의 "배열로 반환"을 봉투 안에 넣은 것 — 프론트가 최상위에서 배열/객체를 분기하지 않게.
2. **explanation 공개 응답은 `status·explanation·message`만.** ai-service의 `attempts·violations·retrieved_refs`는 `agent_message`에 저장하고 브라우저엔 주지 않는다.
3. **`calculation`에 `focus·goal_amount` 없음.** 그 둘은 Spring이 `/rag/answer`를 부를 때 덧붙인다. 테스트가 "공개 `calculation` + `focus` + `goal_amount` == `/rag/answer` 요청 픽스처"를 바이트 단위로 확인한다.
4. **`samples`는 목록형.** P0 하나여도 `{samples:[…]}` — P1~P5(KAN-14) 추가 시 모양이 안 바뀐다.

**KAN-13 케이스 6(입력 오류)은 이 티켓 소관이다** — 도윤 9/3 14:18 "입력 오류 케이스 6(필수값·비중합계·주기·유니버스)은 KAN-4의 API 검증 소관으로 이관". `public_api.PlanInputs`가 6-a 비중합계(`WEIGHTS_SUM`)·6-b 필수값 누락·6-c 자금 없음(`NO_FUNDS`)·6-d 주기 미선택·6-e 카탈로그 밖 자산(`ASSET_NOT_IN_CATALOG`)을 전부 거부하고, `tests/test_public_api.py`가 다섯 건을 그 이름으로 검사한다. Spring 검증이 같은 코드를 내면 프론트 분기가 맞는다.

### 오류 코드 → HTTP · retryable

`public_api.HTTP_ERROR_CODES`가 원본이다. 입력 검증 낱개 코드는 KAN-11 엔진 것 그대로(`GOAL_AMOUNT_RANGE`, `ALLOC_SUM_*`, `WEIGHTS_SUM`, `NO_FUNDS`, `FOCUS_INVALID` …) — 기준은 KAN-11 문서(노션).

| code | HTTP | retryable | 뜻 |
|---|---|---|---|
| `VALIDATION_ERROR` | 400 | ✕ | Kan-9 정적 검증 실패. `errors[]`에 낱개 |
| `UNSUPPORTED_FIELD` | 400 | ✕ | v0.3 필드(`goal.target_month` 등). `field` |
| `INSUFFICIENT_HISTORY` · `ASSET_NOT_IN_CATALOG` | 400 | ✕ | 엔진 데이터 의존 오류. ai-service 422 → 백엔드 400 변환. `max_months` 동봉 |
| `PLAN_NOT_FOUND` | 404 | ✕ | |
| `SNAPSHOT_MISMATCH` | 500 | ✕ | ai-service `data_hash` ≠ DB `is_current` |
| `CALCULATION_FAILED` | 502 | ✓ | plan은 저장됨. `public_id` 동봉 → `GET /plans/{id}` |
| `EXPLANATION_UNAVAILABLE` | 502 | ✓ | ai-service 불가·타임아웃. ai-service가 응답한 경우엔 200 + `status` |

`EXPLANATION_REJECTED`는 오류가 아니다 — 200 + `status`, 결과 화면 유지, 설명 영역만 `message`.

### 3-1. 노션 §4 대조 — 도윤 요청 "차이만" (9/3 15:22 댓글 답신용)

| # | 항목 | 내 9/3 오전 문서 | 노션 §4 | 처리 |
|---|---|---|---|---|
| 1 | 재조회 | `POST /plans/{id}/analysis` | `GET /plans/{public_id}` 재계산 | **노션 채택** |
| 2 | 유니버스 | 없음 | `GET /universe` | **노션 채택**, JSON 모양은 위 표 |
| 3 | 상태 코드 | 200만 | 201 · 502 `CALCULATION_FAILED`+`public_id` · 502 `EXPLANATION_UNAVAILABLE` · 500 `SNAPSHOT_MISMATCH` | **노션 채택** |
| 4 | `plan.version` | 응답에 포함 | 없음 | **제거** |
| 5 | 검증 오류 모양 | `retryable`만 | "배열로 반환" | **결정 1** — 봉투 + `errors[]`. 확인 요청 |
| 6 | explanation 응답 필드 | 미정 | "종현 규약대로 항상 200 + status" | **결정 2** — 3필드. 확인 요청 |
| 7 | samples | P0·P1 | P0 | **결정 4** — 목록형 |

충돌은 없다. 5·6은 노션이 비워둔 자리를 채운 것이라 도윤 확인 한 번이면 KAN-4를 닫을 수 있다(KAN-19 게이트 별도).

## 4. KAN-9 계약 대조 — 수용 기준 1

노션 Kan-9(v0.2 확정 + v0.3 초안)와 KAN-11 구현 결과로 대조했다.

| 항목 | KAN-9 | ai-service 현재 | 상태 |
|---|---|---|---|
| 입력 8필드 | §2 | Spring이 받아 `/calculate`에 전달. ai-service rag는 결과만 받음 | ✅ |
| 결과 모양 `per_period.{M,Q,H}` · `gap.shortfall`(양수=부족) · `risk.mdd_pct`(양수 %) · `meta.window` · `derived.propensity_label` | §5 | `schema.SimulationInput` 정렬 완료 (9/2 밤) | ✅ |
| AI 출력 `summary` · `per_period_pros_cons` · `risks` · `next_actions` · `assumptions_note` | §7 | `schema.Explanation` 정렬 완료. `evidence`·`highlighted_period`·`retrieved_refs`는 KAN-12 추가분 | ✅ |
| 금지 규칙 7개 | §7 | 가드레일 C5·C11·C12 | ✅ |
| 재생성 1회 → 폴백 | §7 | `client.py` | ✅ |
| **`rebalancing.focus` 에코** | §5 출력에 **없음** | Spring이 `/rag/answer` 요청에 함께 넣는 것으로 설계 | ⚠️ **KAN-9 반영 요청** — PRD 수용기준 4 |
| **`goal.amount` 에코** | §5 출력에 **없음** | 위와 같이. 없으면 AI가 "목표 5,000만원"을 말할 근거가 없음 | ⚠️ **KAN-9 반영 요청** |
| 자산 코드 | `KR_EQ`·`US_EQ`·`KR_BOND` (카탈로그) | 픽스처·문서 정렬 | ✅ |
| 위험 지표 | mdd·vol·worst_month·max_drift 4종 | 샤프비율 제거 | ✅ |

**반영 요청 2건은 Jira KAN-4 댓글(9/2 23:48)로 도윤·승준에게 전달됨.**

## 5. 수용 기준 대조

| 티켓 수용 기준 | 상태 |
|---|---|
| KAN-9의 분석 입력·결과 계약과 일치한다 | ✅ AI 측 정렬 완료. 반영 요청 2건은 KAN-9 쪽 갱신 대기 |
| 프론트 담당자가 추가 설명 없이 입력·결과 화면을 연결할 수 있다 | 🟡 OpenAPI + 예시 17개 완성(`docs/openapi/`, `test_public_api` 53/53. 9/5 `/plans/{id}/questions` 추가분 포함). 도윤 확인 2건(§3 결정 1·2) 후 ✅ |
| AI 응답은 KAN-12의 가드레일과 입력·출력 규격을 따른다 | ✅ `test_public_api` 53/53 (공개 응답의 `explanation` == KAN-12 픽스처를 확인. 전체 스위트는 185/185, 상세 `../README.md`) |

## 6. 폐기된 옛 초안 (8/29~9/2)

기록용. KAN-9 확정 전 PRD·KAN-11 티켓 문구만으로 추정해 썼던 것들 — `POST /analyses` + `analysis_id` + TTL 1시간 저장(→ ERD "계산 결과 미저장"으로 폐기), 오류 12종 자체 정의(→ KAN-11 코드 전달로 폐기), `DOMESTIC_EQUITY` 등 자산 코드 4종(→ KAN-9 카탈로그 코드), `selected_frequency` 필수(→ `rebalancing.focus` 선택), `CASH_DEPOSIT` 자산군(→ KAN-9 `alloc.*.safe` 배분 계층). 당시 대조표와 정정 내역은 Jira KAN-4 댓글 3건에 남아 있다.
