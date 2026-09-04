# fixtures — 승준 KAN-11 v0.3 골든·실험 실측 (2026-09-04)

원본: `projectX-Loop/LSJ` `engine-development` 브랜치 `KAN-11-시뮬레이터/v0.3/output/{golden,experiments_after}/`.
각 파일 = 엔진 `result` + `focus`(입력 `rebalancing.focus`) + `goal_amount`(입력 `goal.amount`). KAN-17 `/rag/answer` 본문 그대로.
`t/`는 KAN-13 검출 케이스(T1~T21) 전용 payload. 응답 픽스처(`*_response_good.json`)는 실호출로 만들고 사람이 검수한 것만 둔다.

| 파일 | 실험 | 취지 | 비고 |
|---|---|---|---|
| `case1_small_gap.json` | 골든 P0 | 케이스 1 — v0.2 상수 경로, 목표 초과(간극 작음은 아님, 골든 P1~P5도 전부 초과라 재배치 보류) | window 2021-08~2026-07 · focus Q |
| `case2_large_gap.json` | X01f | 케이스 2 목표 간극 큼 — 목표 15억, 부족 5.1억, ΔM 250만, extension_status BEYOND_INPUT_LIMIT(raw 71) → T12 | window 2016-08~2026-07 · focus Q · cashflow 있음 |
| `case3_high_cost.json` | X14c | 케이스 3 거래 비용 높음 — 기보유 KR_EQ 1,000만 단독 시작, cum_cost M 51,519 최고, max_drift 120% 포화 → T20 | window 2021-08~2026-07 · focus Q · cashflow 있음 |
| `case4_high_drawdown.json` | X16d | 케이스 4 낙폭 높음 — KODEX 코스닥150 편입, MDD M 14.48 > Q 13.79 > H 13.71 역전 → T21 | window 2021-08~2026-07 · focus Q · cashflow 있음 |
| `case5_no_difference.json` | X03a | 케이스 5 주기 차이 없음 — US_EQ 100% 단일 자산, M=Q=H 완전 동일(퇴화) → T4·T19 | window 2021-08~2026-07 · focus Q · cashflow 있음 |
| `t/X04a_cost_order_reversal.json` | X04a | T18 — n=12, 비용 M 10,734 > H 7,607 > Q 7,461 (Q<H 붕괴) | window 2025-08~2026-07 · focus Q · cashflow 있음 |
| `t/X05b_beyond_data_window.json` | X05b | T13 — 목표 100억, BEYOND_DATA_WINDOW | window 2021-08~2026-07 · focus Q · cashflow 있음 |
| `t/X08i_delta_m_ratio.json` | X08i | T6 — ΔM 927,599 / 여유자금 600,000 = ratio 1.546 | window 2021-08~2026-07 · focus Q · cashflow 있음 |
| `t/X05a_already_met.json` | X05a | T17 — 목표 100만, 초과 달성인데 MDD 8.5% 보고 | window 2021-08~2026-07 · focus Q · cashflow 있음 |
| `t/X03d_vol_alone.json` | X03d | T9 — 투자 50%인데 vol_annual_pct 13.66 그대로(목표 비중 기준) | window 2021-08~2026-07 · focus Q · cashflow 있음 |
| `t/X02a_safe_only.json` | X02a | T4·T12 — 안전 100%, assets_used 빈 배열, BEYOND_INPUT_LIMIT raw 87 | window 2021-08~2026-07 · focus Q · cashflow 있음 |
