# KAN-17 — ai-service 내부 HTTP 계약

> 담당 성종현(구현) / 권도윤(계약 확정·Spring 연동) · 선행 KAN-9·12·16 · 후속 백엔드 통합·KAN-14
> **상태 (2026-09-03 밤)**: 엔드포인트·Dockerfile·RAG 결합 완료. **Gemini 실호출 검증 완료** — 아래 「실측」 절. compose 연동은 도윤 대기.
> 요청·응답 JSON은 KAN-4를 따른다. **9/3 도윤 카톡으로 JSON 응답 방식 확정이 성종현 몫**이 되어, 아래는 제안이 아니라 **ai-service 측 확정안**이다. 도윤 통합 중 바뀌면 여기를 먼저 고친다.

## 엔드포인트

| 메서드·경로 | 용도 |
|---|---|
| `POST /calculate` | **Kan-9 §2 입력 dict → §5 결과 JSON** (승준 엔진 `engine/`, M/Q/H 전부 한 응답). 노션 §4 · 9/4 |
| `POST /rag/answer` | 시뮬레이션 결과 JSON → AI 설명 |
| `GET /health` | docker compose healthcheck. 모델·키 유무·retriever 종류 · **엔진 `data_hash`**(스냅샷 정합성) |

## `POST /calculate` — 계산 (9/4, 노션 §4 확정: "3주기 계산 배치는 ai-service가 전부 수행")

**경계**: `engine/`(승준 — `engine.py`·`dataset.py`·`cashflow.py`·`errors.py` + `data/` 스냅샷 6개, LSJ `v0.3/src/core` 사본, 표준 라이브러리만) ↔ `explainer/calculate.py`(종현 — 기동 시 `Dataset` 1회 로드, `now` 주입, 예외 → HTTP). 엔진 코드는 승준만 고친다. `analyze()` 입출력 모양이 바뀌면 사전 통지.

| 항목 | 내용 |
|---|---|
| 요청 | Kan-9 §2 입력 dict **그대로** (Spring이 검증·저장 후 변환 없이 전달). 예: `fixtures/inputs/P0.json` |
| 검증 | **엔진이 한다.** 정적 오류 여러 개 → 데이터 의존 오류 첫 것. ai-service는 덧붙이지 않는다(v0.3 필드 거부 `UNSUPPORTED_FIELD`는 Spring 소관) |
| 200 | §5 출력 그대로 (`status` OK \| NO_PLAN_FUNDS). `public_api.Calculation`과 동일 모양 |
| 422 | `{"code":"VALIDATION_ERROR","retryable":false,"errors":[{code,field,message}…]}` — 엔진 오류 코드 그대로. Spring은 400으로 변환해 그대로 전달 |
| 500 | `{"code":"CALCULATION_FAILED","retryable":true}` — 엔진 예외. Spring은 502 |
| 503 | `{"code":"ENGINE_UNAVAILABLE","retryable":true}` — `engine/` 미배치·스냅샷 해시 불일치 |
| 시간 | 60개월 1~2ms, 120개월+ΔM+연장 ≤ 약 210ms (승준 실측). Spring 타임아웃 10초면 충분 |
| 스냅샷 정합성 | `GET /health`의 `engine.data_hash`와 Spring `data_snapshot(is_current).data_hash`가 같아야 한다. 9/6 동결 시 승준이 `engine/data/` 6개 덮어쓰기 → 해시 변경 → 도윤 DB 행 갱신 → 이미지 재빌드 |

`tests/test_calculate.py` — 골든 P0 원 단위 deep-equal · 공개 계약 `Calculation` 파싱 · 예시 `plans.response.P0.calculation` 일치 · 오류 코드 5종 · 결정론 · P1 현금흐름. `engine/`이 비어 있으면 503 경로만 검사하고 통과.

## 요청 — `POST /rag/answer`

본문 = **KAN-11 `analyze()` 출력 JSON 그대로** + 두 필드.

| 추가 필드 | 왜 | 없으면 |
|---|---|---|
| `focus` (M/Q/H) | KAN-9 입력 `rebalancing.focus`가 §5 출력에 에코되지 않는다. PRD 수용기준 4 "선택된 주기 포함" | C10 WARN, `highlighted_period: null` |
| `goal_amount` | `goal.amount`가 §5 출력에 없다. "목표 5,000만원 대비"를 말할 근거. `fv_total+shortfall` 역산은 계산 금지 | AI가 목표액을 언급하지 못함 |

둘 다 **KAN-9 반영 요청** 중 (Jira KAN-4 댓글). 스키마는 `extra="ignore"`라 v0.3 필드(`cashflow`·`tax` 등)가 붙어도 파싱은 죽지 않는다.

```json
{
  "status": "OK",
  "meta": {
    "assumptions_version": "v0.2",
    "data_version": "2026-09-02",
    "data_hash": "sha256:fa84100c",
    "window": {
      "start": "2021-08",
      "end": "2026-07",
      "months": 60
    },
    "assets_used": [
      {
        "code": "KR_EQ",
        "display_name": "국내 주식 (KODEX 200)",
        "instrument": "069500"
      },
      {
        "code": "US_EQ",
        "display_name": "해외 주식 (SPY × USD/KRW)",
        "instrument": "SPY"
      },
      {
        "code": "KR_BOND",
        "display_name": "국내 채권 (KODEX 국고채3년)",
        "instrument": "114260"
      }
    ],
    "data_basis": "실제 월간 총수익률 재생 · 기준 구간 2021-08~2026-07 · 환노출(무헤지)",
    "safe_rate_annual_pct": 2.962167,
    "generated_at": "2026-09-02T00:00:00Z",
    "warnings": []
  },
  "derived": {
    "propensity_label": "중립형",
    "invest_share_overall_pct": 54.35,
    "plan_excluded_amount": 3600000
  },
  "focus": "Q",
  "per_period": {
    "M": {
      "trajectory": [
        {
          "month": 1,
          "total": 10600000
        },
        {
          "month": 60,
          "total": 70662655
        }
      ],
      "cum_cost": 39878,
      "risk": {
        "mdd_pct": 7.63,
        "vol_annual_pct": 17.72,
        "max_drift_pct": 12.25
      },
      "gap": {
        "fv_total": 70662655,
        "shortfall": -20662655,
        "extra_monthly_required": 0,
        "months_extension": 0
      }
    },
    "Q": "… 동일 구조",
    "H": "… 동일 구조"
  },
  "goal_amount": 50000000
}
```

## 응답 규약

**처리가 정상적으로 끝나면 항상 200**이고, 설명이 나왔는지는 `status`로 구분한다. Spring이 예외 분기 없이 결과 화면을 그릴 수 있게 하기 위해서다. KAN-17 "재실패 시 설명 없이 결과만 반환"을 HTTP로 옮긴 것.

| `status` | HTTP | `explanation` | 뜻 | Spring이 할 일 |
|---|---|---|---|---|
| `OK` | 200 | 있음 | 생성·가드레일 통과 | 설명 영역 렌더 |
| `EXPLANATION_REJECTED` | 200 | `null` | 가드레일 2회 실패 | **결과 화면은 그대로**, 설명 영역에 `message` |
| `EXPLANATION_UNAVAILABLE` | 200 | `null` | 모델 호출 실패·키 없음 | 재시도 버튼 |
| `INVALID_INPUT` | 422 | `null` | 본문이 KAN-9 §5 모양이 아님 | `violations`에 어느 필드인지 |

공통 필드: `attempts`(모델 호출 횟수) · `retrieved_refs`(프롬프트에 넣은 청크) · `violations`(가드레일 내역, 로그용) · `message`(사용자 문구로 바꿀 사유).

## 응답 예시 1 — 정상 (티켓 산출물)

```json
{
  "status": "OK",
  "explanation": {
    "highlighted_period": "Q",
    "summary": {
      "text": "2021-08~2026-07 시장이 그대로 반복된다면 분기별 리밸런싱의 만기 총자산은 6,975만원으로 목표 5,000만원을 1,975만원 초과합니다. 세 주기 모두 이 구간에서는 목표를 넘겼습니다.",
      "evidence": [
        "/meta/window/start",
        "/meta/window/end",
        "/per_period/Q/gap/fv_total",
        "/per_period/Q/gap/shortfall",
        "/goal_amount"
      ]
    },
    "per_period_pros_cons": {
      "M": {
        "pros": [
          {
            "text": "최대 이탈이 12.3%로 세 주기 중 가장 작아 목표 비중에서 덜 벗어납니다.",
            "evidence": [
              "/per_period/M/risk/max_drift_pct"
            ]
          }
        ],
        "cons": [
          {
            "text": "누적 거래비용이 약 4만원으로 세 주기 중 가장 큽니다.",
            "evidence": [
              "/per_period/M/cum_cost"
            ]
          }
        ]
      },
      "Q": {
        "pros": [
          {
            "text": "누적 거래비용 2만 6천원으로 월별보다 낮습니다.",
            "evidence": [
              "/per_period/Q/cum_cost"
            ]
          }
        ],
        "cons": [
          {
            "text": "최대 낙폭이 8.5%로 월별보다 큽니다.",
            "evidence": [
              "/per_period/Q/risk/mdd_pct"
            ]
          }
        ]
      },
      "H": {
        "pros": [
          {
            "text": "누적 거래비용이 약 2만 6천원으로 가장 낮습니다.",
            "evidence": [
              "/per_period/H/cum_cost"
            ]
          }
        ],
        "cons": [
          {
            "text": "최대 이탈이 23.7%로 가장 커서 목표 비중에서 크게 벗어난 구간이 있었습니다.",
            "evidence": [
              "/per_period/H/risk/max_drift_pct"
            ]
          }
        ]
      }
    },
    "risks": [
      {
        "title": "최대 낙폭 구간의 심리적 부담",
        "detail": "분기별 기준 이 구간에서 자산이 고점 대비 8.5%까지 줄어든 시점이 있었습니다. 같은 폭의 하락이 다시 없으리라는 근거는 이 분석에 포함되어 있지 않습니다.",
        "evidence": [
          "/per_period/Q/risk/mdd_pct"
        ]
      }
    ],
    "next_actions": [
      {
        "adjustable_input": "GOAL_AMOUNT",
        "text": "이 구간에서는 목표를 초과했으므로, 목표 금액을 높인 조건으로 다시 계산해볼 수 있습니다.",
        "evidence": [
          "/per_period/Q/gap/shortfall"
        ]
      }
    ],
    "assumptions_note": {
      "text": "기준 구간 2021-08~2026-07의 실제 월간 총수익률을 재생한 결과이며, 해외 주식은 환노출(무헤지) 상태입니다. 안전저축 금리는 연 2.96%로 고정했습니다. 과거 데이터 기반이며 미래 수익을 보장하지 않습니다.",
      "evidence": [
        "/meta/window/start",
        "/meta/window/end",
        "/meta/data_basis",
        "/meta/safe_rate_annual_pct"
      ]
    },
    "retrieved_refs": []
  },
  "attempts": 1,
  "retrieved_refs": [
    "concept/rebalancing#0",
    "concept/disclaimer#0",
    "concept/baseline_window#0",
    "concept/max_drawdown#0",
    "concept/annual_volatility#0",
    "concept/target_weights#0",
    "concept/transaction_cost#0",
    "concept/safe_rate#0"
  ],
  "violations": [],
  "message": null
}
```

## 응답 예시 2 — guardrail 실패 (티켓 산출물)

```json
{
  "status": "EXPLANATION_REJECTED",
  "explanation": null,
  "attempts": 2,
  "retrieved_refs": [
    "concept/rebalancing#0",
    "concept/disclaimer#0"
  ],
  "violations": [
    "[ERROR] C5: summary: 목표 달성 확률·단정 표현 — \"분기별로 목표 달성 확률은 72%입니다\"",
    "[ERROR] C11: summary에 기준 구간 언급 없음 (규칙 6)"
  ],
  "message": "AI 설명을 생성하지 못했습니다. 분석 결과는 정상입니다."
}
```

## 내부 동작

```
[/calculate]  입력 dict → calculate.run() → engine.analyze(inputs, dataset, now) → §5 출력 / ValidationError → 422

[/rag/answer] 요청 JSON → schema.SimulationInput (extra=ignore)
   → retrieve.concept_tags_for(): 결과 필드 → 개념 태그 (결정론)
   → FileRetriever / DbRetriever: 청크 검색
   → prompt.build_user_message(결과, 청크) → Gemini Flash (구조화 출력)
   → guardrail.validate(chunk_exists=…) — C2~C13
   → 통과: OK / 실패: 위반 내역 붙여 1회 재생성 / 재실패: EXPLANATION_REJECTED
```

## 배포

- `Dockerfile` — `python:3.14-slim`(승준 엔진 기준) · `explainer/`·`engine/`·`knowledge/` 복사 · `uvicorn explainer.api:app --port 8000`, healthcheck 포함. 비밀값 미포함
- `docker-compose.ai-service.yml` — 도윤 compose에 붙일 조각 (`db` + `ai-service`). 형태는 도윤 compose 받은 뒤 맞춤
- 환경변수: `GEMINI_API_KEY` `GEMINI_MODEL` `DATABASE_URL` `EMBEDDING_MODEL` `EMBEDDING_DIM`. 배포 시 SSM `/loop/mvp/*`에서 주입

## 수용 기준 대조

| 티켓 수용 기준 | 상태 |
|---|---|
| KAN-9 결과 JSON → KAN-12 출력 스키마 | ✅ 픽스처(골든 P0)로 확인 |
| 응답의 모든 수치가 입력에 존재 (guardrail) | ✅ C4 |
| 인용 청크가 실제로 존재 | ✅ C3 + `chunk_exists` 주입 |
| KAN-13 테스트 전부 통과 | ✅ 케이스 1 · **도윤 9/3 14:18 확정: 수용 범위는 케이스 1~5.** 케이스 6(입력 오류)은 KAN-4 API 검증으로 이관(`public_api.py` + `test_public_api.py` 6-a~e) · 케이스 2~5는 승준 골든 P1~P5 대기 |
| `docker compose up`으로 backend → ai-service 호출 | ❌ 도윤 compose·Docker 환경 대기 |

## 실측 (2026-09-03 밤, 케이스 1 · gemini-3.6-flash · FileRetriever)

키를 넣고 처음 돌린 결과. 코드로만은 못 찾는 문제 4건이 나왔고 전부 `client.py`(provider 결합 지점)에서 처리했다.

| 발견 | 처리 |
|---|---|
| `response_schema`에 `dict[Period, ProsCons]`가 `additionalProperties`로 나가 Developer API가 400 | Gemini 전송용 와이어 스키마(M·Q·H 고정 필드)로 변환, `additionalProperties` 제거. 공개 계약 `Explanation`은 그대로 |
| `gemini-2.0-flash` 폐기(404), 2.5는 신규 사용자 차단 | 기본 모델 `gemini-3.6-flash` (API 권고 ID). `GEMINI_MODEL`로 덮어쓰기 가능 |
| evidence 문자열 끝에 잡음 토큰(`… me Grouped Window Start`, `… philosophy`)이 붙어 C3 반려 | 프롬프트에 evidence 형식 규칙 명시 + 와이어 스키마 `pattern`으로 형식 강제 |
| `26,310원`을 `3만원`으로 반올림해 C4 반려 | 프롬프트 "만원 단위까지" 문구가 원인 → 100만원 미만은 원 단위 그대로로 개정 (KAN-12 프롬프트 동기화) |

**통과율**: 개정 후 정상 응답 3/3이 1회 시도에 가드레일 통과 (오류 0). 개정 전은 3회 중 1회 통과.

**응답 시간**: 생성 1회 **20~36초** (입력 ~3,100토큰 · 출력 ~1,300토큰). 재생성까지 가면 60초를 넘는다.
노션 §4의 Spring→ai-service 타임아웃 **30초는 부족** — 90초 이상을 도윤에게 요청 (확정 대기).

**장애 응답**: 503(고부하)·429(쿼터)는 SDK 재시도 없이(또는 1회) `EXPLANATION_UNAVAILABLE`로 바로 돌린다.
기본 SDK 설정(5회, 백오프 최대 60초)은 한 번의 503에 수 분을 끌어 제거했다. `GEMINI_TIMEOUT_MS`(기본 45000)로 요청 상한.

**⚠ 쿼터**: 현재 키는 **무료 등급 — `gemini-3.6-flash` 하루 20회**(`GenerateRequestsPerDayPerProjectPerModel-FreeTier`).
**9/4 팀 결정: 무료 등급 유지.** 결제 전환 안 함. 따라서 운용 규칙: ① 실호출 테스트는 하루 20회 안에서 케이스 우선순위대로(응답 픽스처 생성 > 통과율 표본) ② 재생성(2회째)은 호출 1회를 더 쓰므로 반려율이 곧 쿼터 소모 ③ 심사 기간에는 팀원이 데모용 호출을 아껴야 하고, 20회 초과 시 화면은 `EXPLANATION_UNAVAILABLE` 메시지로 내려간다(결과 화면은 유지, KAN-17 규약) ④ 한도 리셋은 태평양 시간 자정(한국 16~17시). 필요해지면 별도 프로젝트 키(무료 등급 하나 더)로 20회 추가가 가장 싼 우회.

**임베딩**: `gemini-embedding-001` 768차원 실호출 정상(0.5초). pgvector 적재는 Docker 미설치로 미검증.

**HTTP**: `GET /health` 정상. `POST /rag/answer` 200 + `status`(정상 OK · 쿼터 초과 시 EXPLANATION_UNAVAILABLE) · 잘못된 본문 422 INVALID_INPUT 확인.

## 보류

레포 구조 — 노션 인프라 문서는 `contracts/simulator/rag/app`, 현재는 `explainer/`. 도윤 확인 후.
