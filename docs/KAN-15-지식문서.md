# KAN-15 — RAG 지식 문서 세트 정의 및 수집

> 담당 성종현 · 선행 없음 · 후속 KAN-16
> **방식 (2026-09-02 도윤 합의)**: B — 정의는 직접 작성하고 공적 자료를 출처로 인용. 원문 보관·청킹은 하지 않는다.
> **상태 (2026-09-03)**: 개념 8/8 작성 완료. 외부 용어 출처 URL 1건 보강 중.

## 왜 원문을 수집하지 않고 직접 쓰는가

티켓 문구는 "공적 자료 원문을 보관·청킹"이지만 세 가지 이유로 B를 택했다.
1. **RAG에 넣는 문서의 품질이 곧 AI 설명의 품질이다.** 금감원 PDF를 통째로 넣으면 AI가 "보고서 3장 2절"을 인용하게 되고 사용자에게 도움이 안 된다. 우리 서비스 맥락(기준 구간·재현 원칙)에 맞춘 세 문단이 낫다.
2. **개념 3개(데이터 기준 기간·현금성 이자 가정·거래비용 가정)는 우리 내부 가정이라 외부 문서가 없다.** KAN-9 계약 문서가 출처다.
3. 원문 수집은 재배포 라이선스 확인·PDF 파싱·무관 내용 노이즈가 따라오고, D-5에 감당할 수 없다.

수용 기준은 그대로 만족한다 — 개념별 출처 매핑(아래 표), URL·발행일 기록(프론트매터), 재배포 불가 문서 제외(원문 미보관이므로 해당 없음).

## 개념 ↔ 문서 ↔ 출처 매핑 (수용 기준 1·2)

| 개념 | 파일 | source_id | 출처 URL | 발행일 | 라이선스 | 태그 |
|---|---|---|---|---|---|---|
| 연환산 변동성 | `annual_volatility.md` | `concept/annual_volatility` | https://app.notion.com/p/3cfea37df5ce80979260ec4b9b2f4246 | 2026-09-02 | 팀 내부 문서 (KAN-9 v0.2 §4.4 vol_annual_pct 정의). 자체 작성. 용어 출처 TODO(성종현) | `annual_volatility, risk, vol_annual_pct` |
| 데이터 기준 기간의 의미 | `baseline_window.md` | `concept/baseline_window` | https://app.notion.com/p/3cfea37df5ce80979260ec4b9b2f4246 | 2026-09-02 | 팀 내부 문서 (KAN-9 v0.2 확정 ①·§3.1). 자체 작성 | `baseline_window, assumptions, data_basis` |
| 투자 유의 문구 | `disclaimer.md` | `concept/disclaimer` | **TODO** | — | 자체 작성. 문구 형식은 금융투자협회 표준투자권유준칙 참고 | `disclaimer, assumptions` |
| 최대 낙폭 (MDD) | `max_drawdown.md` | `concept/max_drawdown` | https://app.notion.com/p/3cfea37df5ce80979260ec4b9b2f4246 | 2026-09-02 | 팀 내부 문서 (KAN-9 v0.2 §4.4 mdd_pct 정의). 정의 문장은 자체 작성. 용어 출처 TODO(성종현) 금융투자협회 URL 보강 | `max_drawdown, risk, mdd_pct` |
| 리밸런싱 | `rebalancing.md` | `concept/rebalancing` | https://app.notion.com/p/3cfea37df5ce80979260ec4b9b2f4246 | 2026-09-02 | 팀 내부 문서 (KAN-9 v0.2 §4.2 리밸런싱 규칙). 자체 작성. 용어 출처 TODO(성종현) | `rebalancing, target_weights, max_drift_pct` |
| 현금성 자산의 이자 가정 | `safe_rate.md` | `concept/safe_rate` | https://app.notion.com/p/3cfea37df5ce80979260ec4b9b2f4246 | 2026-09-02 | 팀 내부 문서 (KAN-9 v0.2 확정 ③). 금리 출처는 한국은행 ECOS 121Y002 정기예금 가중평균금리(신규취급액) | `safe_rate, cash_assumption, assumptions` |
| 목표 비중 | `target_weights.md` | `concept/target_weights` | https://app.notion.com/p/3cfea37df5ce80979260ec4b9b2f4246 | 2026-09-02 | 팀 내부 문서 (KAN-9 분석계약 v0.2 §2·§4). 정의 문장은 자체 작성 | `target_weights, rebalancing, portfolio` |
| 거래 비용과 수수료 | `transaction_cost.md` | `concept/transaction_cost` | https://app.notion.com/p/3cfea37df5ce80979260ec4b9b2f4246 | 2026-09-02 | 팀 내부 문서 (KAN-9 v0.2 확정 ④). 정의 문장은 자체 작성. 증권사 수수료 체계는 TODO(성종현) 금융투자협회 자료 URL 보강 | `transaction_cost, rebalancing` |

`source_url`이 **TODO**인 1건은 금융투자협회·한국거래소 용어 페이지 URL을 찾아 넣는다 (성종현, ~10분). 정의 문장 자체는 자체 작성이라 라이선스 무관.

## 메타데이터 스키마 = `knowledge_document` 컬럼

| 프론트매터 | 컬럼 | 뜻 |
|---|---|---|
| `source_id` | `source_id` UNIQUE | 안정 식별자. evidence 참조 `chunk:<source_id>#<idx>`의 앞부분 |
| `title` | `title` | 화면·프롬프트에 보일 이름 |
| `source_url` | `source_url` | 출처 (KAN-15 수용 기준 2) |
| `published_at` | `published_at` | 발행일 (KAN-15 수용 기준 2) |
| `license` | `license` | 재배포 가능 여부·사유 (KAN-15 수용 기준 3) |
| `concept_tags` | `concept_tags TEXT[]` | 검색 필터. `retrieve.py`가 결과 필드 → 이 태그로 청크를 고른다 |

## 문서 작성 규칙

- 절 구조: `## 정의` → `## 왜 보는가/필요한가` → `## 이 서비스에서` (+ 관련 개념). 첫 절이 검색 시 기본으로 들어가는 청크다.
- **숫자를 쓰지 않는다.** 청크는 개념 설명 전용이고, 수치는 시뮬레이터 결과에서만 온다 (가드레일 C13). 예시 숫자가 필요하면 "예를 들어 1000만원이 750만원까지"처럼 가상의 값임이 드러나게 쓴다.
- 우열 판정·권유 문장을 쓰지 않는다 (KAN-9 금지 규칙). "비용과 이탈은 맞바꾸는 관계"처럼 사실만.
- 금융 지식 없는 사용자 기준. 전문 용어는 괄호로 풀어 쓴다.

## 검수

@이승준 정의 문장 8개 금융 검수 요청 중 (Jira KAN-15 댓글). AI가 사용자에게 그대로 보여주는 문장이므로 틀리면 안 된다.

## 노션 정리 페이지

티켓 산출물 "노션 정리 페이지 링크" — 이 문서를 옮기거나 링크할 예정. **미완.**
