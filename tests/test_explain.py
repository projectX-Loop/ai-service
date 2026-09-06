"""explain() 결합 루프 — 가짜 Gemini 클라이언트로 네트워크 없이 검증.

청크가 프롬프트에 들어가는가, 재시도가 도는가, 2회 실패 시 반려되는가, retrieved_refs가 맞는가.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from explainer import client as llm  # noqa: E402
from explainer.knowledge.retrieve import FileRetriever  # noqa: E402
from explainer.schema import Explanation, SimulationInput  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SRC = SimulationInput.model_validate(json.load(open(ROOT / "fixtures/case1_small_gap.json")))
GOOD = json.load(open(ROOT / "fixtures/case1_response_good.json"))
results = []
def check(n, ok, d=""):
    results.append((n, ok)); print(f"{'PASS' if ok else 'FAIL'}  {n}" + (f"  → {d}" if d and not ok else ""))


class FakeGemini:
    """generate_content 호출마다 준비된 응답을 순서대로 돌려준다. 받은 프롬프트를 기록한다."""
    def __init__(self, responses):
        self._responses = list(responses); self.prompts = []
        self.models = self
    def generate_content(self, *, model, contents, config):
        self.prompts.append(contents[-1].parts[0].text if contents else "")   # 마지막 메시지(재시도면 재시도 문구)
        payload = self._responses.pop(0)
        return SimpleNamespace(parsed=Explanation.model_validate(payload), text=json.dumps(payload))


retriever = FileRetriever()

# 1) 정상: 1회에 통과, 청크 8개가 프롬프트에 들어감
fake = FakeGemini([GOOD])
out = llm.explain(SRC, client=fake, retriever=retriever)
check("정상 응답 1회 통과", out.attempts == 1)
check("chunk_refs 8개 반환", len(out.chunk_refs) == 8, str(out.chunk_refs))
check("프롬프트에 청크 삽입됨", "[chunk:concept/max_drawdown#0]" in fake.prompts[0])
check("프롬프트에 결과 JSON 포함", '"per_period"' in fake.prompts[0])
check("프롬프트에 청크 숫자 인용 금지 문구", "청크의 숫자는 인용하지" in fake.prompts[0])

# 2) 청크를 evidence로 인용한 응답도 통과 (exists 검증 포함)
good_with_chunk = json.loads(json.dumps(GOOD))
good_with_chunk["risks"][0]["evidence"].append("chunk:concept/max_drawdown#0")
good_with_chunk["retrieved_refs"] = ["concept/max_drawdown#0"]
out2 = llm.explain(SRC, client=FakeGemini([good_with_chunk]), retriever=retriever)
check("청크 evidence 인용 응답 통과", out2.attempts == 1 and out2.report.passed)

# 3) 없는 청크를 인용하면 C3 → 재시도 후 두 번째 정상 응답으로 통과
bad_chunk = json.loads(json.dumps(GOOD))
bad_chunk["risks"][0]["evidence"].append("chunk:concept/max_drawdown#99")
bad_chunk["retrieved_refs"] = ["concept/max_drawdown#99"]
fake3 = FakeGemini([bad_chunk, GOOD])
out3 = llm.explain(SRC, client=fake3, retriever=retriever)
check("없는 청크 인용 → 재시도 1회 후 통과", out3.attempts == 2)
check("재시도 프롬프트에 위반 내역 전달", "존재하지 않는 청크" in fake3.prompts[1] if len(fake3.prompts) > 1 else False)

# 4) 두 번 다 실패 → ExplanationRejected
bad = json.loads(json.dumps(GOOD))
bad["summary"] = {"text": "달성 확률 90%입니다.", "evidence": ["/goal_amount"]}
try:
    llm.explain(SRC, client=FakeGemini([bad, bad]), retriever=retriever)
    check("2회 실패 시 ExplanationRejected", False)
except llm.ExplanationRejected as e:
    check("2회 실패 시 ExplanationRejected", any(v.check == "C5" for v in e.report.errors))

# 5) retriever 없이도 동작 (청크 0개)
out5 = llm.explain(SRC, client=FakeGemini([GOOD]), retriever=None)
check("retriever 없이 동작 · chunk_refs 빈 배열", out5.chunk_refs == [] and out5.report.passed)

fails = [n for n, ok in results if not ok]
print(f"\n=== {len(results)-len(fails)}/{len(results)} 통과 ===")
sys.exit(1 if fails else 0)
