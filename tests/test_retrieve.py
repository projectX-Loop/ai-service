"""KAN-17 검색 결합 — DB·네트워크 없이 파일 폴백으로 검증."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from explainer.knowledge.retrieve import ALWAYS_TAGS, FileRetriever, concept_tags_for  # noqa: E402
from explainer.schema import SimulationInput  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SRC = SimulationInput.model_validate(json.load(open(ROOT / "fixtures/case1_small_gap.json")))
results = []
def check(n, ok, d=""):
    results.append((n, ok)); print(f"{'PASS' if ok else 'FAIL'}  {n}" + (f"  → {d}" if d and not ok else ""))

# 태그 매핑 — 결과 필드로부터 결정론적으로
tags = concept_tags_for(SRC)
for t in ["rebalancing", "disclaimer", "baseline_window", "max_drawdown", "annual_volatility",
          "target_weights", "transaction_cost", "safe_rate"]:
    check(f"태그 '{t}' 도출", t in tags, str(tags))
check("ALWAYS 태그가 항상 앞에", tags[:len(ALWAYS_TAGS)] == ALWAYS_TAGS)

# safe_rate가 없는 입력이면 그 태그는 빠진다
d = json.load(open(ROOT / "fixtures/case1_small_gap.json")); del d["meta"]["safe_rate_annual_pct"]
check("safe_rate_annual_pct 없으면 safe_rate 태그 제외",
      "safe_rate" not in concept_tags_for(SimulationInput.model_validate(d)))

# 파일 폴백 — knowledge/*.md 8개
r = FileRetriever()
chunks = r.retrieve(SRC)
check("개념 8개 전부 청크 반환", len(chunks) == 8, f"{len(chunks)}: {[c.ref for c in chunks]}")
check("ref 형식 source_id#idx", all("#" in c.ref and c.ref.startswith("concept/") for c in chunks))
check("첫 청크는 '정의' 절", all(c.location in ("정의", "이 서비스가 하는 것과 하지 않는 것") for c in chunks),
      str({c.ref: c.location for c in chunks}))
check("청크 본문 비어있지 않음", all(len(c.content) > 30 for c in chunks))
check("중복 문서 없음", len({c.ref.split('#')[0] for c in chunks}) == len(chunks))

# exists — C3가 쓰는 실존 확인
check("exists: 실제 청크 True", r.exists("concept/max_drawdown#0"))
check("exists: 없는 인덱스 False", not r.exists("concept/max_drawdown#99"))
check("exists: 없는 문서 False", not r.exists("concept/nope#0"))

fails = [n for n, ok in results if not ok]
print(f"\n=== {len(results)-len(fails)}/{len(results)} 통과 ===")
sys.exit(1 if fails else 0)
