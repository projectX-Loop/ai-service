# KAN-4 — MVP API 계약 (성종현 몫: AI 설명 입출력 규격 + 공개 API 제안)

> 담당 성종현(AI 설명 입력·출력 규격) / 권도윤(백엔드 API 최종 통합) · 의존 KAN-9·KAN-11·KAN-12
> **상태 (2026-09-03)**: AI 측 계약은 KAN-9 §5·§7에 정렬 완료(→ `KAN-17`). 공개 API는 도윤 확정 전이라 **아래는 제안**.
> 이력: 8/29 초안(KAN-9 확정 전 추정) → 9/2 KAN-9 대조 → 9/3 현행화. 옛 초안의 `/analyses`·TTL 저장·오류 12종은 KAN-9·ERD로 대체되어 폐기.

## 이 문서의 범위

KAN-4는 9/2 분담에서 사실상 둘로 나뉘었다.

| 부분 | 담당 | 문서 |
|---|---|---|
| 브라우저가 부르는 **공개 API** (Spring) | 권도윤 — "입출력 데이터 구조를 확정해 공유" | 이 문서의 §3은 **제안**. 도윤 확정본이 기준 |
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

## 3. 공개 API 제안 (도윤 확정 전)

입력은 **KAN-9 §2 그대로**(8필드: `goal.amount`, `goal.horizon_months`, `funds.initial`, `funds.monthly`, `alloc.initial`, `alloc.monthly`, `portfolio.assets[]`, `rebalancing.focus`). 투자 성향은 받지 않는다(확정 ⑪ — 배분율에서 파생).

| 메서드·경로 | 역할 | 반환 |
|---|---|---|
| `POST /api/v1/plans` | 입력 검증 + `plan` 저장 | `public_id` |
| `POST /api/v1/plans/{public_id}/analysis` | `/calculate` 호출 (캐시 히트 시 재사용) | KAN-9 §5 결과 JSON |
| `POST /api/v1/plans/{public_id}/explanation` | 결과 + `focus` + `goal_amount`로 `/rag/answer` 호출 | KAN-12 §7 설명 JSON (+ `status`) |
| `GET /api/v1/samples` | 대표 페르소나 P0·P1 입력 | PRD 수용 기준 5 "로그인 없이 샘플로 재현" |

`analysis_id`·TTL 같은 별도 저장 개념은 두지 않는다 — `public_id` + `plan.version`으로 충분하다 (ERD).

### 오류 규약

**입력 검증·계산 오류 코드는 KAN-11 엔진이 이미 갖고 있다** (`GOAL_AMOUNT_RANGE`, `ALLOC_SUM_INITIAL/MONTHLY`, `WEIGHTS_SUM`, `NO_FUNDS`, `ASSET_NOT_IN_CATALOG`, `INSUFFICIENT_HISTORY`, `FOCUS_INVALID` … 30여 종). 공개 API는 **그대로 전달**하는 것을 제안한다. 코드 목록의 기준은 KAN-11 문서(노션)다.

AI 설명 단계는 두 코드만 추가된다 — `EXPLANATION_REJECTED`(가드레일 2회 실패) · `EXPLANATION_UNAVAILABLE`(모델 호출 실패). 둘 다 결과 화면은 유지된다.

모든 오류에 `retryable` 플래그를 둔다 — 프론트가 "재시도 버튼"과 "입력 수정 요구"를 이 값으로 분기한다 (PRD "수정·재시도할 수 있는 메시지").

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
| 프론트 담당자가 추가 설명 없이 입력·결과 화면을 연결할 수 있다 | 🟡 공개 API는 도윤 확정 후. 내부 계약은 KAN-17에 예시 JSON 포함 |
| AI 응답은 KAN-12의 가드레일과 입력·출력 규격을 따른다 | ✅ 테스트 88/88 |

## 6. 폐기된 옛 초안 (8/29~9/2)

기록용. KAN-9 확정 전 PRD·KAN-11 티켓 문구만으로 추정해 썼던 것들 — `POST /analyses` + `analysis_id` + TTL 1시간 저장(→ ERD "계산 결과 미저장"으로 폐기), 오류 12종 자체 정의(→ KAN-11 코드 전달로 폐기), `DOMESTIC_EQUITY` 등 자산 코드 4종(→ KAN-9 카탈로그 코드), `selected_frequency` 필수(→ `rebalancing.focus` 선택), `CASH_DEPOSIT` 자산군(→ KAN-9 `alloc.*.safe` 배분 계층). 당시 대조표와 정정 내역은 Jira KAN-4 댓글 3건에 남아 있다.
