#!/usr/bin/env python3
"""검색 확인 CLI (KAN-16 수용 기준: 샘플 질의 top-3에 정답 개념 포함).

  python scripts/search.py "최대낙폭이 뭐야"
  python scripts/search.py "최대낙폭이 뭐야" --tags max_drawdown
  python scripts/search.py --eval          # docs/KAN-16-지식저장소.md 의 샘플 질의 5건 일괄 평가
"""

from __future__ import annotations

import argparse
from dotenv import load_dotenv
load_dotenv()   # ai-service/.env → 환경변수. 없으면 조용히 통과
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 샘플 질의 5건 + 정답 개념 태그 (수용 기준 근거 기록)
EVAL_SET = [
    ("최대낙폭이 무슨 뜻이야",                    "max_drawdown"),
    ("연환산 변동성은 어떻게 계산해",              "annual_volatility"),
    ("리밸런싱을 왜 하는 거야",                    "rebalancing"),
    ("분기마다 다시 맞추는 게 무슨 의미야",         "rebalancing"),
    ("자산이 고점 대비 얼마나 떨어졌는지 보는 지표", "max_drawdown"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?")
    ap.add_argument("--tags", help="쉼표 구분 개념 태그로 좁히기")
    ap.add_argument("-k", type=int, default=3)
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--eval", action="store_true")
    args = ap.parse_args()

    from explainer.knowledge import store
    from explainer.knowledge.embedding import FakeEmbedder, GeminiEmbedder

    embedder = FakeEmbedder() if args.fake else GeminiEmbedder()
    conn = store.connect()

    def run(q: str, tags: list[str] | None):
        [qv] = embedder.embed([q], task="RETRIEVAL_QUERY")
        return store.search(conn, qv, k=args.k, tags=tags)

    if args.eval:
        passed = 0
        for q, answer in EVAL_SET:
            hits = run(q, None)
            ok = any(answer in h.concept_tags for h in hits)
            passed += ok
            print(f"{'✅' if ok else '❌'} {q}")
            for h in hits:
                print(f"     {h.score:.3f}  {h.ref}  [{h.location}]  {h.concept_tags}")
        print(f"\n{passed}/{len(EVAL_SET)} 통과 (수용 기준: 전부)")
        return 0 if passed == len(EVAL_SET) else 1

    if not args.query:
        ap.error("질의를 주거나 --eval을 쓰세요")
    tags = [t.strip() for t in args.tags.split(",")] if args.tags else None
    for h in run(args.query, tags):
        print(f"\n[{h.score:.3f}] {h.ref}  ·  {h.title} › {h.location}")
        print(f"  출처: {h.source_url}")
        print(f"  {h.content[:200]}{'…' if len(h.content) > 200 else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
