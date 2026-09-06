# engine/ — 계산 엔진 (KAN-11, 이승준)

| 항목 | 내용 |
|---|---|
| 들어온 것 | `engine.py` `dataset.py` `cashflow.py` `errors.py` — LSJ `v0.3/src/core` 그대로 (평면 import → 상대 import 만 변경) |
| 스냅샷 | `data/` — LSJ `v0.3/input/data` 6개 파일. **바이너리 그대로 복사** (해시 검증) · data_version 2026-09-02 · latest_month 2026-07 |
| HTTP | `api/` — FastAPI `POST /calculate` (승준 엔진 자족용, 단독 실행 `python -m uvicorn engine.api.app:app`). 배포는 그대로 `explainer.api:app` — 와이어 계약(200/422/500/503)은 KAN-17 과 동일하게 맞췄다 |
| import | 패키지 안이므로 `from . import cashflow` · `from .dataset import Dataset` · `from .errors import ValidationError, err` |
| 호출 | `from engine.engine import analyze` · `from engine.dataset import Dataset` → `analyze(inputs, dataset=Dataset.load("engine/data"), now=...)` |
| 테스트·골든 | LSJ 레포에 둔다. ai-service는 `fixtures/`에 골든·실험 payload 사본만 |
| 소유 | 엔진·스냅샷 = 승준 / HTTP 층 = 종현 / Spring 클라이언트 = 도윤 |

노션 「프론트-백엔드 계약 정리」 §4: Spring → `POST /calculate`(타임아웃 10초) → ai-service가 M/Q/H 전부 계산해 §5 출력 반환.

---

## HTTP API

`POST /calculate` 하나. 요청 본문(KAN-9 §2 입력 dict) → 시뮬레이션 결과(§5 출력 dict).
검증은 엔진이 하고 이 층은 아무것도 덧붙이지 않는다 — 요청 스키마 = `analyze()` 의 `inputs`,
응답 스키마 = `analyze()` 의 반환값 그대로다.

요청·응답 스키마는 배포되는 `explainer.api:app` 과 엔진 자족용 `engine.api.app:app` 이 **동일**하다
(둘 다 `analyze()` 를 그대로 통과시킨다). 아래는 어느 쪽을 띄우든 같다.

```bash
uvicorn explainer.api:app --port 8000       # 배포 경로 (Dockerfile CMD)
uvicorn engine.api.app:app --port 8000      # 엔진 자족 경로 — /calculate 만

curl -X POST localhost:8000/calculate -H 'content-type: application/json' \
     -d @fixtures/inputs/P0.json
```

### 요청

금액은 **원 단위 정수**. v0.3 신규 필드는 전부 선택 — 비우면 v0.2 동작.

```json
{
  "goal": {"amount": 50000000, "horizon_months": 60},
  "funds": {"initial": 10000000, "monthly": 600000},
  "alloc": {"initial": {"invest": 70, "safe": 30, "other": 0},
            "monthly": {"invest": 50, "safe": 40, "other": 10}},
  "portfolio": {"assets": [{"code": "KR_EQ", "weight": 40},
                           {"code": "US_EQ", "weight": 40},
                           {"code": "KR_BOND", "weight": 20}]},
  "rebalancing": {"focus": "Q"}
}
```

| 필드 | 타입 | 범위·규칙 | 필수 |
|---|---|---|---|
| `goal.amount` | int | 1,000,000 ~ 10,000,000,000 | ✓ |
| `goal.horizon_months` | int | 12 ~ 120. `target_month` 와 **동시 입력 불가** | 둘 중 하나 |
| `goal.target_month` | `"YYYY-MM"` | 시작월 기준 역산 개월이 12~120 | 둘 중 하나 |
| `goal.type` | enum | `lump_sum` \| `housing` \| `wedding` \| `education` \| `business` — UI 프리셋, 계산 무영향 | |
| `funds.initial` | int | 0 이상. 비상자금은 포함하지 않는다 | ✓ |
| `funds.monthly` | int | 0 이상. `cashflow` 와 **동시 입력 불가** | `cashflow` 없으면 ✓ |
| `funds.composition` | object | `{cash, holdings[{code, amount}], locked[{amount, release_month}]}`. 합 = `funds.initial` | |
| `alloc.initial` · `alloc.monthly` | object | `{invest, safe, other}` 각 0~100, **합 = 100** | ✓ |
| `portfolio.assets` | array | 1~3개. `{code, weight}`, weight 0 초과 100 이하, **합 = 100**, 코드 중복 불가 | 투자 배분 > 0 이면 ✓ |
| `rebalancing.focus` | `M` \| `Q` \| `H` | 화면 강조용 — **계산은 항상 3주기 전부** | |
| `cashflow` | object | 1층 현금흐름 경로. 아래 참조 | |
| `options` | object | 2층 옵션. 아래 참조 | |

카탈로그 자산 코드: `KR_EQ`(KODEX 200) · `US_EQ`(SPY×환율, `foreign_listed`) · `US_EQ_KR`(TIGER 미국S&P500, 국내상장) · `KR_BOND`(KODEX 국고채3년) 외 확장 6종 — 전체는 `data/asset_catalog.json`.

**`cashflow`** — `funds.monthly` 대신 소득·지출에서 월 납입 경로 M_t 를 만든다.

```json
"cashflow": {
  "mode": "summary",
  "income": {"basis": "net", "regular_monthly": 3000000,
             "bonus": [{"month": 1, "amount": 2000000}, {"month": 9, "amount": 500000}]},
  "expense": {"fixed_monthly": 1300000, "variable_monthly": 1100000,
              "variable_by_month": {"2": 1.4, "9": 1.3}},
  "debt": {"monthly_payment": 0},
  "emergency_fund": {"current": 3000000, "target_months": 3},
  "growth_mode": "flat"
}
```

`mode` `summary`\|`profile` · `income.basis` **`"net"` 고정**(세전→세후 환산은 엔진 밖) ·
`bonus` 0~4개, `month` 1~12 중복 불가 · `variable_by_month` 배수 0~3 ·
`emergency_fund.target_months` 0~12(기본 3) · `growth_mode` `flat`\|`replay` ·
`mode=profile` 이면 `profile{income[12], expense[12]}` 정수 12개씩.
**적자월(F_t < 0) 불허** → `CASHFLOW_DEFICIT_MONTHS`.

**`options`** — 기본값은 v0.2 동작.

| 필드 | 값 | 기본 |
|---|---|---|
| `safe_rate_mode` | `fixed_avg` \| `replay` | `fixed_avg` |
| `lot_rounding` | bool — 1주 단위 매수 | `false` |
| `account` | `{"type": "general"\|"isa", "isa_exempt_limit": 2000000}` | 없음(세금 미계산) |

### 응답 — 200

`analyze()` 반환값 그대로. `status` = `"OK"` \| `"NO_PLAN_FUNDS"`.

```json
{
  "status": "OK",
  "meta": {...}, "derived": {...}, "cashflow": {...},
  "per_period": {"M": {...}, "Q": {...}, "H": {...}}
}
```

| 블록 | 필드 | 의미 |
|---|---|---|
| `meta` | `assumptions_version` `data_version` `data_hash` | 재현성 증빙. `data_hash` 는 `sha256:…` |
| | `window {start, end, months}` | 기준 구간 = 최신 확정월에서 역산 |
| | `start_month` `target_month` | 시작월 = 최신 확정월 + 1 |
| | `assets_used[]` | `{code, display_name, instrument, tax_class, history}` |
| | `cashflow_source` | `none` \| `summary` \| `profile` — 원자료는 싣지 않음 |
| | `series_used[]` `options` `safe_rate_annual_pct` | 실효 시계열·옵션·r̄ |
| | `data_basis` | 가정 요약 문장 — **화면 필수 노출** |
| | `warnings[]` | 계산 계속되는 경고 (`LOCKED_RELEASE_OUT_OF_RANGE`) |
| | `generated_at` | 주입된 `now` 의 ISO 문자열 |
| `derived` | `propensity_label` | 전체 투자비중 W: <30% 안정형 · 30~60% 중립형 · >60% 공격형 |
| | `invest_share_overall_pct` `plan_excluded_amount` | W(%) · 계획 외 자금 |
| `cashflow` | `profile` `monthly_contribution[n]` | 12개월 프로파일(`none` 이면 null) · 월 납입 경로 M_t |
| | `surplus_rate_pct` `bonus_share_pct` `months_zero` | 흑자율 · 상여 의존도 · 납입 0인 달 수 |
| | `emergency_filled_month` `growth_effect_pct` `surplus_headroom` | 비상자금 충당 완료 t · replay 효과 · ΔM 분모(상수 경로는 null) |
| `per_period.{M,Q,H}` | `trajectory[n]` | `{month, invest, safe, total, contribution, cash_residual}` 월말 기록 |
| | `cum_cost` | 누적 거래비용 (편도 `commission + fx_spread`) |
| | `risk` | `mdd_pct` `vol_annual_pct` `worst_month_pct` `max_drift_pct` |
| | `tax` | `options.account` 지정 시에만 — `realized_cum` `fv_after_tax` |
| | `gap` | 아래 |

`gap` 블록:

| 필드 | 의미 |
|---|---|
| `fv_total` | 만기 총자산 |
| `shortfall` | `goal − FV`. 음수면 초과 달성 |
| `extra_monthly_required` | ΔM — 추가 필요 월 납입액. 산출 불가면 `null` |
| `extra_monthly_ratio` | ΔM ÷ `surplus_headroom`. 상수 경로는 `null` |
| `months_extension` | 재제출 가능한 연장 개월(n′ ≤ 120)일 때만. 아니면 `null` |
| `months_extension_raw` | 자르기 전 n′−n. **재계산 입력으로 쓰면 `GOAL_HORIZON_RANGE` 로 거부된다** |
| `extension_status` | `OK` \| `BEYOND_INPUT_LIMIT` \| `BEYOND_DATA_WINDOW` \| `SERIES_NOT_AVAILABLE` |
| `status` | `already_met` \| `exact` \| `short` \| `unreachable` |
| `basis` | `pre_tax` \| `after_tax` |
| `delta_m_model` | `lot_rounding` 시에만 `"continuous"` |

`status: "NO_PLAN_FUNDS"` 는 오류가 아니라 메시지다(배분이 '기타' 100% 등으로 계획 자금 0) —
`{status, message, meta}` 만 온다.

### 응답 — 오류

| 상태 | 본문 |
|---|---|
| 422 | `{"code":"VALIDATION_ERROR","retryable":false,"errors":[{code, field, message}, …]}` |
| 500 | `{"code":"CALCULATION_FAILED","retryable":true,"message":…}` |
| 503 | `{"code":"ENGINE_UNAVAILABLE","retryable":true,"message":…}` — 스냅샷 부재·해시 불일치 |

**정적 검증**(데이터 불필요)은 오류를 **여러 개 한꺼번에** 반환하고, 통과하면 **데이터 의존 검증**이
**첫 오류에서 중단**한다. 일부 항목은 `months` · `max_months` · `required`/`available` 같은 추가 키를 동봉한다.

```json
{"code": "VALIDATION_ERROR", "retryable": false, "errors": [
  {"code": "GOAL_AMOUNT_RANGE", "field": "goal.amount", "message": "목표 금액은 100만 ~ 100억 원의 정수"},
  {"code": "WEIGHTS_SUM", "field": "portfolio.assets", "message": "..."}
]}
```

| 단계 | 코드 |
|---|---|
| 정적 | `GOAL_AMOUNT_RANGE` `GOAL_SPEC_CONFLICT` `GOAL_HORIZON_RANGE` `OPTION_INVALID` `FUNDS_INITIAL_RANGE` `FUNDS_MONTHLY_RANGE` `MONTHLY_SOURCE_CONFLICT` `COMPOSITION_SUM` `HOLDING_NOT_IN_PORTFOLIO` `ALLOC_SUM_INITIAL` `ALLOC_SUM_MONTHLY` `PORTFOLIO_REQUIRED` `PORTFOLIO_ASSET_DUP` `PORTFOLIO_WEIGHT_RANGE` `NUMBER_NOT_FINITE` `WEIGHTS_SUM` `FOCUS_INVALID` `ACCOUNT_TYPE_UNSUPPORTED` `CASHFLOW_FIELD_RANGE` `INCOME_BASIS_INVALID` `BONUS_MONTH_INVALID` `PROFILE_LENGTH` |
| 데이터 | `ASSET_NOT_IN_CATALOG` `INSUFFICIENT_HISTORY`(`max_months` 동봉) `SERIES_NOT_AVAILABLE` `LOCKED_RELEASE_PAST` `ACCOUNT_ASSET_INELIGIBLE` `ISA_LIMIT_EXCEEDED` |
| 양쪽 | `TARGET_MONTH_RANGE` `NO_FUNDS` `ISA_HORIZON_TOO_SHORT` `CASHFLOW_DEFICIT_MONTHS` |

경고(계산 계속, `meta.warnings[]`): `LOCKED_RELEASE_OUT_OF_RANGE`.
**금액 0 항목은 검증 대상이 아니다** — 조용히 무시된다.

### 운영 메모

- 스냅샷 경로: 이 레포에서는 `engine/data/`. `engine.api.app` 은 `ENGINE_DATA_DIR` 로 덮어쓸 수 있다.
  프로세스당 1회만 로드한다(`Dataset.load` 는 파일 5종 SHA-256 전수 대조라 비싸다).
- `now` 는 서버가 주입한다 — 요청 본문으로 받지 않는다(결정론). `meta.generated_at` 에만 영향하므로
  같은 입력은 그 필드를 빼면 원 단위로 동일한 응답이 나온다.
- 요청 본문은 로그에 남기지 않는다(민감정보 미저장).
- 소요: 60개월 1~2ms, 120개월 + ΔM + 연장 최대 약 210ms. 계약 비기능 요건 p95 < 3초.
- 요청 스키마를 pydantic 으로 다시 타이핑하지 말 것 — 엔진은 `isinstance(int)` 엄격 검사인데
  pydantic 기본(lax) 모드는 `"50000000"` 문자열을 통과시켜 판정이 갈린다. 쓰려면 `StrictInt`.
- HTTP 계약(Spring 연동·타임아웃·오류 변환)은 `docs/KAN-17-내부HTTP계약.md`, 생성된 스펙은 `docs/openapi/`.
- 수식·한계·페르소나 전문은 LSJ 레포 `KAN-11-시뮬레이터/v0.3/README-실행방법.md` 와 계약 `KAN-9`.
- 이 절의 원본은 `korpronate-lab/project-x` 의 `engine/README.md` — 엔진 계약이 바뀌면 그쪽을 먼저 고친다.
