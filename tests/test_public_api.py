"""공개 API JSON 계약 (KAN-4) — 예시 JSON 이 계약과 맞는가, OpenAPI 가 코드와 같은가. 네트워크 없음.

검증하는 것
  · docs/openapi/examples/*.json 전부가 대응 모델을 통과한다
  · 공개 응답의 calculation + focus + goal_amount = 내부 /rag/answer 요청 (Spring 조립 규칙) — 픽스처와 바이트 단위로 같다
  · 공개 explanation 응답의 explanation 은 KAN-12 Explanation 그대로 (픽스처와 동일)
  · Kan-9 정적 검증 규칙(합 100, 중복, v0.3 필드 거부)이 모델에 살아 있다
  · 오류 예시의 code 가 HTTP_ERROR_CODES 표에 있고 retryable 이 표와 같다
  · docs/openapi/*.json 이 scripts/export_openapi.py 출력과 같다 (--check)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import ValidationError  # noqa: E402

from explainer import public_api as pub  # noqa: E402
from explainer.schema import Explanation, SimulationInput  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "docs/openapi/examples"
results = []


def check(n, ok, d=""):
    results.append((n, ok)); print(f"{'PASS' if ok else 'FAIL'}  {n}" + (f"  → {d}" if d and not ok else ""))


def load(name):
    return json.loads((EX / name).read_text(encoding="utf-8"))


# 1) 예시 ↔ 모델
MODEL_OF = {
    "plans.request.P0.json": pub.PlanInputs,
    "plans.response.P0.json": pub.PlanResponse,
    "explanation.response.ok.json": pub.ExplanationResponse,
    "explanation.response.rejected.json": pub.ExplanationResponse,
    "explanation.response.unavailable.json": pub.ExplanationResponse,
    "universe.response.json": pub.UniverseResponse,
    "samples.response.json": pub.SamplesResponse,
    "error.validation.json": pub.ErrorEnvelope,
    "error.unsupported_field.json": pub.ErrorEnvelope,
    "error.not_found.json": pub.ErrorEnvelope,
    "error.calculation_failed.json": pub.ErrorEnvelope,
    "error.explanation_unavailable.json": pub.ErrorEnvelope,
    "error.snapshot_mismatch.json": pub.ErrorEnvelope,
}
on_disk = sorted(p.name for p in EX.glob("*.json"))
check("예시 파일 목록이 테스트 표와 같다", on_disk == sorted(MODEL_OF), f"disk={on_disk}")
for name, model in MODEL_OF.items():
    try:
        model.model_validate(load(name)); check(f"예시 통과: {name}", True)
    except (ValidationError, FileNotFoundError) as e:
        check(f"예시 통과: {name}", False, str(e)[:200])

# 2) Spring 조립 규칙: calculation + focus + goal_amount == /rag/answer 요청 (픽스처)
resp = load("plans.response.P0.json")
req = load("plans.request.P0.json")
assembled = {**resp["calculation"], "focus": req["rebalancing"]["focus"], "goal_amount": req["goal"]["amount"]}
fixture = json.loads((ROOT / "fixtures/case1_small_gap.json").read_text(encoding="utf-8"))
check("calculation + focus + goal_amount == fixtures/case1_small_gap.json", assembled == fixture)
try:
    SimulationInput.model_validate(assembled); check("조립 결과가 SimulationInput 을 통과한다", True)
except ValidationError as e:
    check("조립 결과가 SimulationInput 을 통과한다", False, str(e)[:200])
check("공개 calculation 에는 focus·goal_amount 가 없다", "focus" not in resp["calculation"] and "goal_amount" not in resp["calculation"])
check("plan.inputs == 요청 본문", resp["plan"]["inputs"] == req)

# 3) explanation 은 KAN-12 Explanation 그대로
ok = load("explanation.response.ok.json")
good = json.loads((ROOT / "fixtures/case1_response_good.json").read_text(encoding="utf-8"))
check("explanation.ok 의 explanation == fixtures/case1_response_good.json", ok["explanation"] == good)
check("explanation.ok 는 Explanation 을 통과한다", Explanation.model_validate(ok["explanation"]) is not None)
check("공개 응답에 attempts·violations·retrieved_refs 가 없다", not ({"attempts", "violations", "retrieved_refs"} & set(ok)))
for name in ("explanation.response.rejected.json", "explanation.response.unavailable.json"):
    d = load(name)
    check(f"{name}: explanation null + message 있음", d["explanation"] is None and bool(d["message"]))


# 4) Kan-9 정적 검증이 모델에 살아 있다 — KAN-13 케이스 6(입력 오류)은 KAN-4 API 검증으로 이관됨 (도윤 9/3 14:18)
def rejects(label, payload, code):
    try:
        pub.PlanInputs.model_validate(payload); check(f"거부: {label}", False, "통과해버림")
    except ValidationError as e:
        check(f"거부: {label} → {code}", code in str(e) or code == "UNSUPPORTED_FIELD" and "extra_forbidden" in str(e), str(e)[:160])


def variant(mutate):
    d = json.loads(json.dumps(req)); mutate(d); return d


# KAN-13 케이스 6-a ~ 6-e
rejects("6-a 비중 합 110", variant(lambda d: d["portfolio"]["assets"][0].__setitem__("weight", 50)), "WEIGHTS_SUM")
rejects("6-b 필수값 누락 (goal 없음)", variant(lambda d: d.pop("goal")), "missing")
rejects("6-c 자금 없음 (initial·monthly 0)", variant(lambda d: d["funds"].update(initial=0, monthly=0)), "NO_FUNDS")
rejects("6-d 주기 미선택 (focus 없음)", variant(lambda d: d["rebalancing"].pop("focus")), "missing")
rejects("6-e 유니버스 밖 자산 REAL_ESTATE", variant(lambda d: d["portfolio"]["assets"][2].__setitem__("code", "REAL_ESTATE")), "ASSET_NOT_IN_CATALOG")
# 그 밖의 Kan-9 정적 규칙
rejects("alloc.initial 합 110", variant(lambda d: d["alloc"]["initial"].__setitem__("invest", 80)), "ALLOC_SUM_INITIAL")
rejects("자산 코드 중복", variant(lambda d: d["portfolio"]["assets"][1].__setitem__("code", "KR_EQ")), "PORTFOLIO_ASSET_DUP")
rejects("horizon 6개월", variant(lambda d: d["goal"].__setitem__("horizon_months", 6)), "greater_than_equal")
rejects("v0.3 필드 goal.target_month", variant(lambda d: d["goal"].__setitem__("target_month", "2031-08")), "UNSUPPORTED_FIELD")
rejects("focus Y", variant(lambda d: d["rebalancing"].__setitem__("focus", "Y")), "enum")
try:
    pub.Portfolio.model_validate({"assets": [{"code": "KR_EQ", "weight": 40}, {"code": "US_EQ_KR", "weight": 40}, {"code": "KR_BOND", "weight": 20}]})
    check("optional 자산 US_EQ_KR 도 카탈로그로 통과", True)
except ValidationError as e:
    check("optional 자산 US_EQ_KR 도 카탈로그로 통과", False, str(e)[:160])
rejects("자산 4개 (최대 3)", variant(lambda d: d["portfolio"]["assets"].append({"code": "US_EQ_KR", "weight": 0})), "too_long")

# 5) 오류 예시 ↔ HTTP_ERROR_CODES 표
for name in (n for n in MODEL_OF if n.startswith("error.")):
    d = load(name)
    row = pub.HTTP_ERROR_CODES.get(d["code"])
    check(f"{name}: code {d['code']} 가 표에 있고 retryable={d['retryable']}", row is not None and row[1] == d["retryable"])
val = load("error.validation.json")
check("VALIDATION_ERROR 는 errors[] 를 2건 이상 담는다", len(val["errors"]) >= 2 and all(e["field"] for e in val["errors"]))
check("CALCULATION_FAILED 는 public_id 를 동봉한다", load("error.calculation_failed.json")["public_id"])

# 6) OpenAPI 파일이 코드와 같다
r = subprocess.run([sys.executable, str(ROOT / "scripts/export_openapi.py"), "--check"], capture_output=True, text=True)
check("docs/openapi/*.json 이 export_openapi.py 출력과 같다", r.returncode == 0, (r.stdout + r.stderr).strip()[-300:])
spec = json.loads((ROOT / "docs/openapi/public-api.openapi.json").read_text(encoding="utf-8"))
check("공개 API 경로 5개", set(spec["paths"]) == {"/plans", "/plans/{public_id}", "/plans/{public_id}/explanation", "/universe", "/samples"})


def refs(o):
    if isinstance(o, dict):
        if "$ref" in o:
            yield o["$ref"]
        for v in o.values():
            yield from refs(v)
    elif isinstance(o, list):
        for v in o:
            yield from refs(v)


missing = {r for r in refs(spec) if r.split("/")[-1] not in spec["components"]["schemas"]}
check("모든 $ref 가 components 에 있다", not missing, str(missing))
check("Explanation·PeriodResult 가 공개 스펙 컴포넌트에 있다 (schema.py 재사용)", {"Explanation", "PeriodResult", "Meta"} <= set(spec["components"]["schemas"]))

n_ok = sum(1 for _, ok in results if ok)
print(f"\n{n_ok}/{len(results)} passed")
sys.exit(0 if n_ok == len(results) else 1)
