#!/usr/bin/env python3
"""CLI — 시뮬레이션 결과 JSON을 넣으면 검증된 AI 설명을 출력한다.

  python run.py fixtures/case1_small_gap.json          # 실제 LLM 호출
  python run.py fixtures/case1_small_gap.json --check   # 검증기만 (API 키 불필요)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from explainer import client as llm
from explainer.guardrail import validate
from explainer.schema import Explanation, SimulationInput

FREQ_KO = {"MONTHLY": "월별", "QUARTERLY": "분기별", "SEMIANNUAL": "반기별"}


def render(exp: Explanation) -> str:
    lines = [
        f"■ 요약        {exp.summary.text}",
        "",
        f"■ 목표와의 거리",
        f"  {exp.goal_gap.text}",
        "",
        f"■ 주기별 비교  (선택: {FREQ_KO[exp.highlighted_frequency.value]})",
    ]
    for fc in exp.frequency_comparison:
        lines.append(f"  · {FREQ_KO[fc.frequency.value]}")
        lines.append(f"      {fc.observation}")
        lines.append(f"      {fc.tradeoff}")
    lines += ["", "■ 위험 설명"]
    for rf in exp.risk_factors:
        lines.append(f"  · {rf.title}")
        lines.append(f"      {rf.detail}")
    lines += ["", "■ 권장 행동"]
    for na in exp.next_actions:
        lines.append(f"  · [{na.adjustable_input.value}] {na.text}")
    lines += [
        "",
        "■ 데이터 기준",
        f"  기간: {exp.data_basis.period}",
        f"  가정: {' / '.join(exp.data_basis.assumptions)}",
        f"  {exp.data_basis.disclaimer}",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", type=Path, help="시뮬레이션 결과 JSON")
    ap.add_argument("--check", type=Path, metavar="RESPONSE_JSON",
                    help="LLM을 부르지 않고 기존 응답 JSON만 검증한다")
    args = ap.parse_args()

    source = SimulationInput.model_validate(json.loads(args.input.read_text(encoding="utf-8")))

    if args.check:
        exp = Explanation.model_validate(json.loads(args.check.read_text(encoding="utf-8")))
        report = validate(exp, source)
    else:
        if not llm.has_credentials():
            print("GEMINI_API_KEY가 없습니다. .env에 넣거나 --check로 검증기만 돌리세요.",
                  file=sys.stderr)
            return 2
        try:
            outcome = llm.explain(source)
        except llm.ExplanationRejected as e:
            print("EXPLANATION_REJECTED — 가드레일 2회 위반", file=sys.stderr)
            for v in e.report.errors:
                print(f"  {v}", file=sys.stderr)
            return 1
        except llm.ExplanationUnavailable as e:
            print(f"EXPLANATION_UNAVAILABLE — {e}", file=sys.stderr)
            return 3
        exp, report = outcome.explanation, outcome.report
        print(f"(시도 {outcome.attempts}회)\n")

    print(render(exp))
    print()
    print("─" * 60)
    print(f"가드레일: {'통과' if report.passed else '실패'}"
          f"  (오류 {len(report.errors)} / 경고 {len(report.warnings)})")
    for v in report.violations:
        print(f"  {v}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
