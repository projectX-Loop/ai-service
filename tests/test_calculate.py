"""POST /calculate — 승준 엔진 어댑터 (KAN-17 · 노션 §4). 네트워크 없음.

engine/ 에 승준 파일이 없으면(아직 push 전) 엔진 의존 케이스는 SKIP 으로 표시하고 0 으로 끝난다.
있으면 골든 P0 와 원 단위 deep-equal 까지 본다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from explainer import calculate as calc  # noqa: E402
from explainer import public_api as pub  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
results: list[tuple[str, bool]] = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  → {detail}" if detail and not ok else ""))


def strip(d: dict) -> dict:
    d = json.loads(json.dumps(d)); d.get("meta", {}).pop("generated_at", None); return d


def main() -> int:
    from fastapi.testclient import TestClient
    from explainer.api import app
    c = TestClient(app)

    health = c.get("/health").json()
    check("/health 200 + engine 키 존재", "engine" in health)

    if not calc.available():
        print("SKIP  engine/ 에 승준 엔진·스냅샷 없음 — /calculate 는 503 이어야 함")
        r = c.post("/calculate", json=json.loads((ROOT / "fixtures/inputs/P0.json").read_text(encoding="utf-8")))
        check("엔진 부재 시 503 ENGINE_UNAVAILABLE", r.status_code == 503 and r.json().get("code") == "ENGINE_UNAVAILABLE", str(r.json())[:120])
        check("엔진 부재 시 /health engine=null", health["engine"] is None)
        return _finish()

    check("/health engine.data_hash 는 sha256:", str(health["engine"].get("data_hash", "")).startswith("sha256:"))

    p0 = json.loads((ROOT / "fixtures/inputs/P0.json").read_text(encoding="utf-8"))
    r = c.post("/calculate", json=p0)
    check("P0 → 200", r.status_code == 200, str(r.json())[:120])
    out = r.json()
    check("응답 status OK · per_period M/Q/H", out.get("status") == "OK" and set(out.get("per_period", {})) == {"M", "Q", "H"})

    # 골든 P0 (= fixtures/case1_small_gap.json 에서 focus·goal_amount 뺀 것) 과 원 단위 일치
    fx = json.loads((ROOT / "fixtures/case1_small_gap.json").read_text(encoding="utf-8"))
    golden = {k: v for k, v in fx.items() if k not in ("focus", "goal_amount")}
    check("골든 P0 와 deep-equal (generated_at 제외)", strip(out) == strip(golden))

    # 공개 계약 Calculation 으로 파싱 가능 + 예시 plans.response.P0.calculation 과 일치
    try:
        pub.Calculation.model_validate(out); ok = True
    except Exception as e:
        ok = False; print("   ", e)
    check("public_api.Calculation 파싱", ok)
    ex = json.loads((ROOT / "docs/openapi/examples/plans.response.P0.json").read_text(encoding="utf-8"))
    check("docs/openapi/examples/plans.response.P0.calculation == /calculate 출력", strip(ex["calculation"]) == strip(out))

    # /rag/answer 입력으로 그대로 이어짐 (Spring 조립: 출력 + focus + goal_amount)
    from explainer.schema import SimulationInput
    SimulationInput.model_validate({**out, "focus": "Q", "goal_amount": p0["goal"]["amount"]})
    check("/calculate 출력 + focus + goal_amount → SimulationInput 파싱", True)

    # 검증 실패 → 422 errors[] (엔진 오류 코드 그대로)
    bad = json.loads(json.dumps(p0)); bad["goal"]["amount"] = 100
    r = c.post("/calculate", json=bad)
    check("goal.amount 100 → 422 GOAL_AMOUNT_RANGE", r.status_code == 422 and any(e["code"] == "GOAL_AMOUNT_RANGE" for e in r.json()["errors"]), str(r.json())[:160])
    bad = json.loads(json.dumps(p0)); bad["portfolio"]["assets"][0]["weight"] = 30
    r = c.post("/calculate", json=bad)
    check("비중 합 90 → 422 WEIGHTS_SUM", r.status_code == 422 and any(e["code"] == "WEIGHTS_SUM" for e in r.json()["errors"]), str(r.json())[:160])
    bad = json.loads(json.dumps(p0)); bad["goal"]["horizon_months"] = 121
    r = c.post("/calculate", json=bad)
    check("기간 121 → 422 GOAL_HORIZON_RANGE", r.status_code == 422 and any(e["code"] == "GOAL_HORIZON_RANGE" for e in r.json()["errors"]), str(r.json())[:160])
    bad = json.loads(json.dumps(p0)); bad["rebalancing"] = {"focus": ["Q"]}
    r = c.post("/calculate", json=bad)
    check("focus 배열 → 422 FOCUS_INVALID (크래시 아님, 승준 B-2)", r.status_code == 422 and any(e["code"] == "FOCUS_INVALID" for e in r.json()["errors"]), str(r.json())[:160])
    r = c.post("/calculate", json={"goal": 5, "funds": [], "alloc": "x"})
    check("블록이 스칼라 → 422 (500 아님)", r.status_code == 422, str(r.json())[:160])

    # 결정론: 같은 입력 두 번
    a = strip(c.post("/calculate", json=p0).json()); b = strip(c.post("/calculate", json=p0).json())
    check("같은 입력 두 번 → 동일 결과", a == b)

    # P1 (현금흐름 경로) 도 200
    p1 = json.loads((ROOT / "fixtures/inputs/P1.json").read_text(encoding="utf-8"))
    r = c.post("/calculate", json=p1)
    check("P1 (cashflow·target_month) → 200, cashflow.months_zero 5", r.status_code == 200 and r.json()["cashflow"]["months_zero"] == 5, str(r.json())[:120])
    return _finish()


def _finish() -> int:
    fails = [n for n, ok in results if not ok]
    print(f"\n=== {len(results) - len(fails)}/{len(results)} 통과 ===")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
