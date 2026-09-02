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

PERIOD_KO = {"M": "월별", "Q": "분기별", "H": "반기별"}


def render(exp: Explanation) -> str:
    hp = exp.highlighted_period.value if exp.highlighted_period else None
    lines = [
        f"■ 요약        {exp.summary.text}",
        "",
        f"■ 주기별 장단점  (강조: {PERIOD_KO.get(hp, '없음')})",
    ]
    for p in ("M", "Q", "H"):
        pc = exp.per_period_pros_cons.get(p) or exp.per_period_pros_cons.get(type(exp.highlighted_period)(p)) if exp.highlighted_period else None
        pc = pc or next((v for k, v in exp.per_period_pros_cons.items() if k.value == p), None)
        if pc is None:
            continue
        lines.append(f"  · {PERIOD_KO[p]}")
        for c in pc.pros:
            lines.append(f"      + {c.text}")
        for c in pc.cons:
            lines.append(f"      − {c.text}")
    lines += ["", "■ 위험 설명"]
    for r in exp.risks:
        lines.append(f"  · {r.title}")
        lines.append(f"      {r.detail}")
    lines += ["", "■ 권장 행동"]
    for na in exp.next_actions:
        lines.append(f"  · [{na.adjustable_input.value}] {na.text}")
    lines += ["", "■ 데이터 기준", f"  {exp.assumptions_note.text}"]
    if exp.retrieved_refs:
        lines += ["", f"■ 인용 청크  {', '.join(exp.retrieved_refs)}"]
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
