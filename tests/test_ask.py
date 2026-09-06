"""ask() 결합 루프 — 가짜 Gemini 클라이언트로 네트워크 없이 검증 (질문답변 스트레치).

test_explain.py와 같은 패턴. validate_ask()가 C6·C8·C10·C11(Explanation 고유 구조 검사)
없이도 C2~C5·C12~C14·C16~C18을 그대로 잡는지 확인한다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from explainer import api as api_module  # noqa: E402
from explainer import client as llm  # noqa: E402
from explainer.knowledge.retrieve import FileRetriever  # noqa: E402
from explainer.schema import AskAnswer, Claim, SimulationInput  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SRC = SimulationInput.model_validate(json.load(open(ROOT / "fixtures/case1_small_gap.json")))

GOOD = {
    "claim": {
        "text": "2021-08~2026-07 시장이 그대로 반복된다면 분기별 리밸런싱의 만기 총자산은 6,975만원입니다.",
        "evidence": ["/meta/window/start", "/meta/window/end", "/per_period/Q/gap/fv_total"],
    },
    "retrieved_refs": [],
}

results = []
def check(n, ok, d=""):
    results.append((n, ok)); print(f"{'PASS' if ok else 'FAIL'}  {n}" + (f"  → {d}" if d and not ok else ""))


class FakeGemini:
    def __init__(self, responses):
        self._responses = list(responses); self.prompts = []
        self.models = self
    def generate_content(self, *, model, contents, config):
        self.prompts.append(contents[-1].parts[0].text if contents else "")
        payload = self._responses.pop(0)
        return SimpleNamespace(parsed=AskAnswer.model_validate(payload), text=json.dumps(payload))


retriever = FileRetriever()

# 1) 정상: 1회 통과, 질문이 프롬프트에 들어감
fake = FakeGemini([GOOD])
out = llm.ask(SRC, "분기별로 하면 얼마나 모여?", client=fake, retriever=retriever)
check("정상 응답 1회 통과", out.attempts == 1)
check("프롬프트에 질문 포함", "분기별로 하면 얼마나 모여?" in fake.prompts[0])
check("프롬프트에 결과 JSON 포함", '"per_period"' in fake.prompts[0])

# 2) 청크를 evidence로 인용한 응답도 통과 (exists 검증 포함)
good_with_chunk = json.loads(json.dumps(GOOD))
good_with_chunk["claim"]["evidence"].append("chunk:concept/max_drawdown#0")
good_with_chunk["retrieved_refs"] = ["concept/max_drawdown#0"]
out2 = llm.ask(SRC, "최대 낙폭이 뭐야?", client=FakeGemini([good_with_chunk]), retriever=retriever)
check("청크 evidence 인용 응답 통과", out2.attempts == 1 and out2.report.passed)

# 3) 없는 청크를 인용하면 C3 → 재시도 후 두 번째 정상 응답으로 통과
bad_chunk = json.loads(json.dumps(GOOD))
bad_chunk["claim"]["evidence"].append("chunk:concept/max_drawdown#99")
bad_chunk["retrieved_refs"] = ["concept/max_drawdown#99"]
fake3 = FakeGemini([bad_chunk, GOOD])
out3 = llm.ask(SRC, "질문", client=fake3, retriever=retriever)
check("없는 청크 인용 → 재시도 1회 후 통과", out3.attempts == 2)
check("재시도 프롬프트에 위반 내역 전달", "존재하지 않는 청크" in fake3.prompts[1] if len(fake3.prompts) > 1 else False)

# 4) 두 번 다 실패 → ExplanationRejected (C5 금지 표현)
bad = json.loads(json.dumps(GOOD))
bad["claim"] = {"text": "목표 달성 확률은 90%입니다.", "evidence": ["/goal_amount"]}
try:
    llm.ask(SRC, "질문", client=FakeGemini([bad, bad]), retriever=retriever)
    check("2회 실패 시 ExplanationRejected", False)
except llm.ExplanationRejected as e:
    check("2회 실패 시 ExplanationRejected", any(v.check == "C5" for v in e.report.errors))

# 5) retriever 없이도 동작
out5 = llm.ask(SRC, "질문", client=FakeGemini([GOOD]), retriever=None)
check("retriever 없이 동작 · chunk_refs 빈 배열", out5.chunk_refs == [] and out5.report.passed)

# 6) Explanation 고유 구조 검사(C6·C8·C10·C11)가 answer에는 안 걸린다 —
#    세 주기 언급도, assumptions_note도, focus 라벨도 없는 짧은 답이어도 통과해야 한다
short = {"claim": {"text": "네, 맞습니다.", "evidence": ["/goal_amount"]}, "retrieved_refs": []}
out6 = llm.ask(SRC, "목표금액 5천만원 맞아?", client=FakeGemini([short]), retriever=retriever)
check("기준구간 언급 없는 짧은 답도 통과 (C11 미적용)", out6.report.passed, [str(v) for v in out6.report.errors])

# 7) 멀티턴 — history가 프롬프트에 들어가고, evidence는 여전히 새로 검증된다
history = [{"question": "분기별로 하면 얼마나 모여?", "answer": "6,975만원입니다."}]
fake7 = FakeGemini([GOOD])
out7 = llm.ask(SRC, "그럼 반기별로는?", client=fake7, retriever=retriever, history=history)
check("history가 프롬프트에 포함됨", "분기별로 하면 얼마나 모여?" in fake7.prompts[0] and "6,975만원입니다." in fake7.prompts[0])
check("history 있어도 evidence는 새로 검증 통과", out7.report.passed)

# 8) history 없이도(빈 리스트·None) 기존과 동일하게 동작 — 하위호환
out8 = llm.ask(SRC, "질문", client=FakeGemini([GOOD]), retriever=retriever, history=[])
check("history 빈 배열도 정상 동작", out8.report.passed)
out8b = llm.ask(SRC, "질문", client=FakeGemini([GOOD]), retriever=retriever)
check("history 생략(기본 None)도 정상 동작", out8b.report.passed)

# 9) HTTP 계약 회귀 검사 — public_api.QuestionResponse.answer는 Claim 그대로다(schema.AskAnswer
#    래퍼를 내부적으로 쓰더라도 응답 몸체엔 안 나가야 한다). 9/6 이 계약과 실제 구현이 갈렸던 걸 여기서 잡는다.
_original_ask = api_module.llm.ask
api_module.llm.ask = lambda *a, **k: SimpleNamespace(
    answer=SimpleNamespace(claim=Claim(text="분기별 답변입니다.", evidence=["/per_period/Q/gap/fv_total"])),
    attempts=1,
    chunk_refs=[],
    report=SimpleNamespace(warnings=[]),
)
try:
    http_client = TestClient(api_module.app)
    resp = http_client.post("/rag/ask", json={**json.loads(SRC.model_dump_json()), "question": "분기별로 하면 얼마?"})
    body = resp.json()
    check("HTTP 응답 answer.text로 바로 접근 가능(공개 계약)", body.get("answer", {}).get("text") == "분기별 답변입니다.")
    check("HTTP 응답에 claim 래퍼가 노출되지 않음", "claim" not in (body.get("answer") or {}))
finally:
    api_module.llm.ask = _original_ask

fails = [n for n, ok in results if not ok]
print(f"\n=== {len(results)-len(fails)}/{len(results)} 통과 ===")
sys.exit(1 if fails else 0)
