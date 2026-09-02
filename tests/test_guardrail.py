"""가드레일 검증기 테스트. LLM 호출 없이 돌아간다."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from explainer.guardrail import validate
from explainer.schema import Explanation, SimulationInput

SOURCE = SimulationInput.model_validate(
    json.loads((Path(__file__).resolve().parents[1] / "fixtures/case1_small_gap.json").read_text())
)

GOOD = {
    "highlighted_frequency": "QUARTERLY",
    "summary": {
        "text": "세 주기 모두 목표에 못 미치며, 분기별이 285만원 차이로 가장 근접합니다.",
        "evidence": ["/results/1/goal_gap"],
    },
    "goal_gap": {
        "text": "목표 5,000만원 대비 분기별 리밸런싱은 4,715만원으로 285만원(5.7%) 부족합니다. "
                "월 납입액을 4만 5천원 늘리면 이 차이를 메울 수 있습니다.",
        "evidence": ["/user_profile/goal_amount", "/results/1/final_value",
                     "/results/1/goal_gap", "/results/1/additional_monthly_required"],
    },
    "frequency_comparison": [
        {"frequency": "MONTHLY",
         "observation": "누적 거래비용 68만원으로 가장 높습니다.",
         "tradeoff": "대신 최대낙폭 -23.8%로 세 주기 중 가장 작습니다.",
         "evidence": ["/results/0/cumulative_cost", "/results/0/risk_metrics/max_drawdown"]},
        {"frequency": "QUARTERLY",
         "observation": "최종 자산 4,715만원으로 가장 높습니다.",
         "tradeoff": "거래비용 24.5만원, 최대낙폭 -25.1%입니다.",
         "evidence": ["/results/1/final_value", "/results/1/cumulative_cost",
                      "/results/1/risk_metrics/max_drawdown"]},
        {"frequency": "SEMIANNUAL",
         "observation": "거래비용 12.8만원으로 가장 낮습니다.",
         "tradeoff": "대신 최대낙폭 -26.7%로 가장 큽니다.",
         "evidence": ["/results/2/cumulative_cost", "/results/2/risk_metrics/max_drawdown"]},
    ],
    "risk_factors": [
        {"title": "최대낙폭 구간의 심리적 부담",
         "detail": "분기별 기준 투자 기간 중 자산이 -25.1%까지 줄어든 시점이 있었습니다.",
         "evidence": ["/results/1/risk_metrics/max_drawdown"]},
    ],
    "next_actions": [
        {"adjustable_input": "MONTHLY_CONTRIBUTION",
         "text": "월 납입액을 4만 5천원 늘린 조건으로 다시 계산해볼 수 있습니다.",
         "evidence": ["/user_profile/monthly_contribution",
                      "/results/1/additional_monthly_required"]},
    ],
    "data_basis": {
        "period": "2019-07-01 ~ 2026-06-30",
        "assumptions": ["배당 재투자 가정", "거래비용 편도 0.015% 반영", "세금 미반영"],
        "disclaimer": "과거 데이터 기반 시뮬레이션 결과이며 미래 수익을 보장하지 않습니다. 투자 자문이 아닙니다.",
    },
}


def clone(**patch):
    import copy
    d = copy.deepcopy(GOOD)
    d.update(patch)
    return d


def run(name, payload, expect_pass):
    exp = Explanation.model_validate(payload)
    rep = validate(exp, SOURCE)
    ok = rep.passed == expect_pass
    print(f"{'PASS' if ok else 'FAIL'}  {name}  (통과={rep.passed}, 기대={expect_pass})")
    for v in rep.violations:
        print(f"        {v}")
    return ok


def main():
    results = []

    results.append(run("정상 응답은 통과한다", GOOD, True))

    results.append(run(
        "C4 — 입력에 없는 샤프비율을 지어내면 잡힌다",
        clone(risk_factors=[{
            "title": "위험 대비 성과 부진",
            "detail": "샤프비율이 0.85로 낮습니다.",
            "evidence": ["/results/1/risk_metrics/sharpe_ratio"]}]),
        False))

    results.append(run(
        "C5 — 달성 확률을 말하면 잡힌다",
        clone(summary={"text": "현재 계획으로 목표 달성 확률은 72%입니다.",
                       "evidence": ["/results/1/goal_gap"]}),
        False))

    results.append(run(
        "C5 — 특정 상품을 권하면 잡힌다",
        clone(next_actions=[{"adjustable_input": "MONTHLY_CONTRIBUTION",
                             "text": "KODEX 200을 매수하시면 좋겠습니다.",
                             "evidence": ["/user_profile/monthly_contribution"]}]),
        False))

    results.append(run(
        "C3 — 없는 경로를 근거로 대면 잡힌다",
        clone(summary={"text": "분기별이 285만원 차이로 가장 근접합니다.",
                       "evidence": ["/results/9/goal_gap"]}),
        False))

    results.append(run(
        "C10 — 선택된 주기를 안 지키면 잡힌다",
        clone(highlighted_frequency="MONTHLY"),
        False))

    results.append(run(
        "C8 — 가정 문구를 바꾸면 잡힌다",
        clone(data_basis={"period": "2019-07-01 ~ 2026-06-30",
                          "assumptions": ["배당 재투자 가정"],
                          "disclaimer": "투자 자문이 아닙니다."}),
        False))

    print()
    print(f"=== {sum(results)}/{len(results)} 통과 ===")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
