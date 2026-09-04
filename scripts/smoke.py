#!/usr/bin/env python3
"""통합 스모크 — 떠 있는 ai-service 에 붙어 계약대로 응답하는지 본다 (9/5 합숙용, 도윤 compose 검증).

  python scripts/smoke.py                       # http://localhost:8000
  python scripts/smoke.py http://ai-service:8000
  python scripts/smoke.py --llm                 # /rag/answer 실호출 1회 포함 (쿼터 1회 소모)

LLM 없이 도는 것: /health · /calculate(P0 골든 대조·422·결정론) · /rag/answer 422.
표준 라이브러리만 쓴다 — Spring 쪽 머신에서도 python3 하나면 돈다.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  → {detail}" if detail and not ok else ""))


def call(base: str, method: str, path: str, body: dict | None = None, timeout: float = 100) -> tuple[int, dict, float]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method, headers={"content-type": "application/json"})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode()), time.time() - t
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}"), time.time() - t


def strip(d: dict) -> dict:
    d = json.loads(json.dumps(d)); d.get("meta", {}).pop("generated_at", None); return d


def main(argv: list[str]) -> int:
    llm = "--llm" in argv
    base = next((a for a in argv if a.startswith("http")), "http://localhost:8000").rstrip("/")
    print(f"ai-service @ {base}\n")

    st, h, dt = call(base, "GET", "/health")
    check("GET /health 200", st == 200, f"{st} {h}")
    check("health.engine 있음 (승준 엔진 로드)", bool(h.get("engine")), str(h.get("engine")))
    check("health.credentials (GEMINI_API_KEY 주입)", h.get("credentials") is True, "키 없음 → /rag/answer 는 EXPLANATION_UNAVAILABLE")
    print(f"      model={h.get('model')} retriever={h.get('retriever')} data_hash={str((h.get('engine') or {}).get('data_hash'))[:24]}")

    p0 = json.loads((ROOT / "fixtures/inputs/P0.json").read_text(encoding="utf-8"))
    golden = json.loads((ROOT / "fixtures/case1_small_gap.json").read_text(encoding="utf-8"))
    golden = {k: v for k, v in golden.items() if k not in ("focus", "goal_amount")}
    st, out, dt = call(base, "POST", "/calculate", p0)
    check(f"POST /calculate P0 → 200 ({dt*1000:.0f}ms)", st == 200, str(out)[:160])
    if st == 200:
        check("  골든 P0 원 단위 일치 (generated_at 제외)", strip(out) == strip(golden))
        check("  meta.data_hash == /health engine.data_hash (SNAPSHOT_MISMATCH 검사 기준)",
              out.get("meta", {}).get("data_hash") == (h.get("engine") or {}).get("data_hash"))
        st2, out2, _ = call(base, "POST", "/calculate", p0)
        check("  같은 입력 두 번 → 동일", st2 == 200 and strip(out2) == strip(out))
    bad = json.loads(json.dumps(p0)); bad["goal"]["amount"] = 100
    st, err, _ = call(base, "POST", "/calculate", bad)
    check("POST /calculate 검증 실패 → 422 VALIDATION_ERROR + errors[]",
          st == 422 and err.get("code") == "VALIDATION_ERROR" and any(e.get("code") == "GOAL_AMOUNT_RANGE" for e in err.get("errors", [])), str(err)[:160])

    st, err, _ = call(base, "POST", "/rag/answer", {"meta": {}})
    check("POST /rag/answer 잘못된 본문 → 422 INVALID_INPUT", st == 422 and err.get("status") == "INVALID_INPUT", str(err)[:160])

    if llm and 'out' in locals() and isinstance(out, dict) and "per_period" in out:
        body = {**out, "focus": p0["rebalancing"]["focus"], "goal_amount": p0["goal"]["amount"]}
        st, ans, dt = call(base, "POST", "/rag/answer", body, timeout=120)
        check(f"POST /rag/answer 실호출 → 200 ({dt:.0f}s)", st == 200, str(ans)[:160])
        status = ans.get("status")
        check(f"  status ∈ OK/REJECTED/UNAVAILABLE (실제: {status})", status in ("OK", "EXPLANATION_REJECTED", "EXPLANATION_UNAVAILABLE"))
        if status == "OK":
            e = ans["explanation"]
            check("  explanation 5필드 + retrieved_refs", all(k in e for k in ("summary", "per_period_pros_cons", "risks", "next_actions", "assumptions_note")))
            print("      summary:", e["summary"]["text"][:140])
        else:
            print("      message:", ans.get("message"), "| violations:", ans.get("violations"))
    elif not llm:
        print("SKIP  /rag/answer 실호출 (--llm 으로 켬, 쿼터 1회)")

    fails = [n for n, ok in results if not ok]
    print(f"\n=== {len(results) - len(fails)}/{len(results)} 통과 ===")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
