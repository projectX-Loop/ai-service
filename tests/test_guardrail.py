"""가드레일 테스트 — KAN-9 §5·§7 정렬본. LLM 호출 없음.

일부러 틀린 답을 넣어 검증기가 잡는지 확인한다. 정상 응답은 통과해야 한다.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from explainer.guardrail import validate  # noqa: E402
from explainer.schema import Explanation, SimulationInput  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SRC = SimulationInput.model_validate(json.load(open(ROOT / "fixtures/case1_small_gap.json")))
GOOD = json.load(open(ROOT / "fixtures/case1_response_good.json"))
results: list[tuple[str, bool]] = []


def clone(**patch):
    d = copy.deepcopy(GOOD)
    for k, v in patch.items():
        d[k] = v
    return d


def run(name, payload, expect_pass, source=SRC):
    exp = Explanation.model_validate(payload)
    rep = validate(exp, source)
    ok = rep.passed == expect_pass
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  (통과={rep.passed}, 기대={expect_pass})")
    if not expect_pass or not ok:
        for v in rep.errors[:3]:
            print(f"        {v}")


def main() -> int:
    run("정상 응답은 통과한다", GOOD, True)

    # C4 — 입력에 없는 수치 (샤프비율은 KAN-11이 산출하지 않는 지표)
    run("C4 — 입력에 없는 샤프비율을 지어내면 잡힌다",
        clone(risks=[{"title": "낮은 샤프비율", "detail": "샤프비율이 0.85로 낮습니다.",
                      "evidence": ["/per_period/Q/risk/mdd_pct"]}]), False)

    # C5 규칙 1 — 달성 확률
    run("C5 — 달성 확률을 말하면 잡힌다",
        clone(summary={"text": "2021-08~2026-07 기준 분기별로 목표 달성 확률은 72%입니다.",
                       "evidence": ["/meta/window/start"]}), False)

    # C5 규칙 2 — 상품 권유
    run("C5 — 특정 상품을 권하면 잡힌다",
        clone(next_actions=[{"adjustable_input": "MONTHLY_CONTRIBUTION",
                             "text": "KODEX 200을 매수하시면 좋겠습니다.",
                             "evidence": ["/per_period/Q/gap/shortfall"]}]), False)

    # C5 규칙 4 — 성향 라벨 인격 단정
    run("C5 — 성향 라벨을 성격으로 확장하면 잡힌다 (규칙 4)",
        clone(risks=[{"title": "성향", "detail": "중립형 성향의 분이시네요. 최대 낙폭 8.5%를 견디실 수 있습니다.",
                      "evidence": ["/derived/propensity_label", "/per_period/Q/risk/mdd_pct"]}]), False)

    # C5 규칙 7 — 지출 훈계
    run("C5 — 지출을 훈계하면 잡힌다 (규칙 7)",
        clone(next_actions=[{"adjustable_input": "MONTHLY_CONTRIBUTION",
                             "text": "낭비가 많으니 아끼세요.",
                             "evidence": ["/per_period/Q/gap/shortfall"]}]), False)

    # C12 규칙 5 — 조건절 없는 미래 단정
    run("C12 — '5년 뒤 ~가 됩니다'는 잡힌다 (규칙 5)",
        clone(summary={"text": "분기별 리밸런싱으로 5년 뒤 6,975만원이 됩니다. 기준 구간 2021-08~2026-07.",
                       "evidence": ["/per_period/Q/gap/fv_total", "/meta/window/start"]}), False)

    # C11 규칙 6 — 기준 구간 미언급
    run("C11 — summary에 기준 구간이 없으면 잡힌다 (규칙 6)",
        clone(summary={"text": "분기별 만기 총자산은 6,975만원으로 목표 5,000만원을 초과합니다.",
                       "evidence": ["/per_period/Q/gap/fv_total", "/goal_amount"]}), False)

    # C3 — 없는 경로
    run("C3 — 없는 경로를 근거로 대면 잡힌다",
        clone(summary={"text": "2021-08~2026-07 기준 분기별 결과입니다.",
                       "evidence": ["/per_period/X/gap/shortfall"]}), False)

    # C10 — focus 불일치
    run("C10 — 선택된 주기를 안 지키면 잡힌다",
        clone(highlighted_period="M"), False)

    # C10 — focus 없으면 WARN만 (통과)
    src_nofocus = SimulationInput.model_validate({**json.load(open(ROOT / "fixtures/case1_small_gap.json")), "focus": None})
    run("C10 — focus 없으면 경고만 내고 통과한다",
        clone(highlighted_period=None), True, source=src_nofocus)

    # C6 — 주기 누락
    d = clone(); del d["per_period_pros_cons"]["H"]
    run("C6 — 주기 하나를 빼면 잡힌다", d, False)

    # C8 — assumptions_note 기준 구간 없음
    run("C8 — assumptions_note에 기준 구간이 없으면 잡힌다",
        clone(assumptions_note={"text": "환노출 상태입니다. 미래 수익을 보장하지 않습니다.",
                                "evidence": ["/meta/data_basis"]}), False)

    # C13 — 청크만 근거인 문장에 수치
    run("C13 — 청크만 근거로 든 문장에 숫자가 있으면 잡힌다",
        clone(risks=[{"title": "최대 낙폭", "detail": "최대 낙폭은 고점 대비 하락폭이며 8.5%였습니다.",
                      "evidence": ["chunk:concept/max_drawdown#0"]}],
              retrieved_refs=["concept/max_drawdown#0"]), False)

    # 청크 근거 정상 (수치 없음) — 형식만 검사, DB 없음
    run("청크를 개념 설명에만 쓰면 통과한다",
        clone(risks=[{"title": "최대 낙폭 구간의 심리적 부담",
                      "detail": "최대 낙폭(고점 대비 가장 크게 떨어진 폭)이 분기별 기준 8.5%였습니다.",
                      "evidence": ["/per_period/Q/risk/mdd_pct", "chunk:concept/max_drawdown#0"]}],
              retrieved_refs=["concept/max_drawdown#0"]), True)

    
    # ── 2026-09-04 승준 KAN-12 변경점 2·3 + 계약 §6.2 개정 ⑦ (C14 자동 부분 · C16 · C17 · C18 · C8 환노출)
    def with_summary(text, ev=None):
        d = clone(); d["summary"] = {"text": text, "evidence": ev or ["/meta/window/start", "/meta/window/end", "/goal_amount"]}; return d

    run("C14 고정 문구: 값 없는 '반기가 가장 저렴' 반려",
        with_summary("2021-08~2026-07 시장이 반복된다면 목표 5,000만원을 넘습니다. 반기 리밸런싱이 가장 저렴합니다."), False)
    run("C14 값 인용 비교는 허용: '분기 26,310원, 반기 25,917원'",
        with_summary("2021-08~2026-07 시장이 반복된다면 목표 5,000만원을 넘습니다. 이 조건에서는 분기 26,310원, 반기 25,917원입니다.",
                     ["/meta/window/start", "/meta/window/end", "/goal_amount", "/per_period/Q/cum_cost", "/per_period/H/cum_cost"]), True)
    run("C14 '항상' 서술 반려",
        with_summary("2021-08~2026-07 시장이 반복된다면 목표 5,000만원을 넘습니다. 자주 리밸런싱하면 이탈이 항상 작습니다."), False)
    run("C14 MDD 억제 서술 반려",
        with_summary("2021-08~2026-07 시장이 반복된다면 목표 5,000만원을 넘습니다. 자주 리밸런싱하면 낙폭이 줄어듭니다."), False)
    run("C5 분산 효과 서술 반려",
        with_summary("2021-08~2026-07 시장이 반복된다면 목표 5,000만원을 넘습니다. 채권을 섞어 위험을 낮췄습니다."), False)
    run("C5 환율 우열 서술 반려",
        with_summary("2021-08~2026-07 시장이 반복된다면 목표 5,000만원을 넘습니다. 환율 덕분에 수익이 좋았습니다."), False)

    # C16 — extension_status가 OK가 아닌데 연장 개월을 도달 안내로 서술
    src_beyond = copy.deepcopy(SRC.model_dump(mode="json"))
    for p in ("M", "Q", "H"):
        src_beyond["per_period"][p]["gap"].update({"extension_status": "BEYOND_INPUT_LIMIT", "months_extension": None, "months_extension_raw": 71})
    run("C16 BEYOND_INPUT_LIMIT 인데 '71개월 더 납입하면 도달' 반려",
        with_summary("2021-08~2026-07 시장이 반복된다면 목표 5,000만원에 미치지 못합니다. 71개월 더 납입하면 도달합니다.",
                     ["/meta/window/start", "/meta/window/end", "/goal_amount", "/per_period/Q/gap/months_extension_raw"]),
        False, source=SimulationInput.model_validate(src_beyond))
    run("C16 BEYOND_INPUT_LIMIT 에서 '범위를 넘습니다' 서술은 허용",
        with_summary("2021-08~2026-07 시장이 반복된다면 분기 리밸런싱 기준 목표 5,000만원에 미치지 못합니다. 데이터상 71개월이면 도달하나 입력 가능 범위를 넘습니다.",
                     ["/meta/window/start", "/meta/window/end", "/goal_amount", "/per_period/Q/gap/months_extension_raw"]),
        True, source=SimulationInput.model_validate(src_beyond))

    # C14 퇴화 — 세 주기 결과가 같을 때 우열 서술
    src_same = copy.deepcopy(SRC.model_dump(mode="json"))
    for p in ("M", "Q", "H"):
        src_same["per_period"][p]["gap"]["fv_total"] = 50284194; src_same["per_period"][p]["cum_cost"] = 0
    run("C14 퇴화(M=Q=H)에서 '월 리밸런싱은 비용이 큽니다' 반려",
        with_summary("2021-08~2026-07 시장이 반복된다면 목표 5,000만원을 넘습니다. 월 리밸런싱은 비용이 큽니다."),
        False, source=SimulationInput.model_validate(src_same))

    # C8 — 해외 자산 있는데 환노출 미언급 → ERROR
    d = clone(); d["assumptions_note"] = {"text": "기준 구간 2021-08~2026-07의 실제 월간 총수익률을 재생한 결과이며 안전저축 금리는 연 2.96%로 고정했습니다.",
                                          "evidence": ["/meta/window/start", "/meta/window/end", "/meta/safe_rate_annual_pct"]}
    run("C8 해외 자산(US_EQ) 있는데 환노출 미언급 반려", d, False)

    # C3 — 새 필드 경로가 인용 가능해야 함 (스키마에 들어왔는지)
    run("C3 새 gap 필드 경로 인용 가능 (/per_period/Q/gap/extension_status)",
        with_summary("2021-08~2026-07 시장이 반복된다면 분기 리밸런싱 기준 목표 5,000만원을 넘습니다.",
                     ["/meta/window/start", "/meta/window/end", "/goal_amount", "/per_period/Q/gap/extension_status", "/per_period/Q/gap/status"]), True)
    run("C3 cashflow.profile 은 스키마 밖 — 인용하면 반려",
        with_summary("2021-08~2026-07 시장이 반복된다면 목표 5,000만원을 넘습니다.",
                     ["/meta/window/start", "/meta/window/end", "/goal_amount", "/cashflow/profile"]), False)

    # ── 응답 픽스처 전수: caseN_response_good ↔ caseN 입력 (9/4 실호출 생성분 포함)
    pairs = {"case1_small_gap": "case1", "case2_large_gap": "case2", "case3_high_cost": "case3", "case4_high_drawdown": "case4", "case5_no_difference": "case5"}
    for inp, tag in pairs.items():
        rp = ROOT / f"fixtures/{tag}_response_good.json"
        if not rp.exists():
            continue
        src_i = SimulationInput.model_validate(json.load(open(ROOT / f"fixtures/{inp}.json")))
        run(f"픽스처 {tag}_response_good ↔ {inp} 통과", json.load(open(rp)), True, source=src_i)

    fails = [n for n, ok in results if not ok]
    print(f"\n=== {len(results) - len(fails)}/{len(results)} 통과 ===")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
