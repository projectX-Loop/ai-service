"""KAN-16 파이프라인 테스트 — DB·네트워크 없이 도는 부분만.

청킹 규칙, 프론트매터 파싱, 해시 기반 재적재 판단, 가짜 임베더 결정성.
DB 적재·검색은 docker compose Postgres에서 scripts/ingest.py --fake 로 별도 확인.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from explainer.knowledge.chunking import (  # noqa: E402
    MAX_CHARS, MIN_CHARS, Chunk, chunk_markdown, content_hash, parse_frontmatter,
)
from explainer.knowledge.embedding import EMBEDDING_DIM, FakeEmbedder  # noqa: E402

KNOWLEDGE = Path(__file__).resolve().parent.parent / "knowledge"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


# ── 프론트매터
meta, body = parse_frontmatter((KNOWLEDGE / "max_drawdown.md").read_text())
check("프론트매터 source_id 파싱", meta.get("source_id") == "concept/max_drawdown", str(meta))
check("프론트매터 제거 후 본문 시작이 헤딩", body.lstrip().startswith("## "), body[:30])

# ── 청킹: 실제 문서 3개
total = 0
for p in sorted(KNOWLEDGE.glob("*.md")):
    _, b = parse_frontmatter(p.read_text())
    chunks = chunk_markdown(b)
    total += len(chunks)
    check(f"{p.name}: 청크 ≥1", len(chunks) >= 1)
    check(f"{p.name}: 인덱스 연속", [c.index for c in chunks] == list(range(len(chunks))))
    check(f"{p.name}: 모든 청크에 location", all(c.location for c in chunks))
    check(f"{p.name}: 상한 준수", all(c.char_count <= MAX_CHARS * 1.5 for c in chunks),
          f"max={max(c.char_count for c in chunks)}")
check("문서 3개 청크 합계 > 0", total > 0, str(total))

# ── 청킹: 긴 섹션은 문단으로 나뉘고 겹침이 생긴다
para = "이것은 충분히 긴 문단입니다. " * 12          # ~200자
long_doc = "## 긴 절\n\n" + "\n\n".join(f"{i}번째 문단. " + para for i in range(6))
chunks = chunk_markdown(long_doc)
check("긴 섹션이 2개 이상으로 분할", len(chunks) >= 2, str(len(chunks)))
if len(chunks) >= 2:
    first_last_para = chunks[0].content.split("\n\n")[-1]
    check("연속 청크에 직전 문단 겹침", first_last_para in chunks[1].content)
check("분할된 청크도 같은 location", all(c.location == "긴 절" for c in chunks))

# ── 청킹: 짧은 꼬리는 앞에 붙는다
tail_doc = "## 절\n\n" + ("가" * 500) + "\n\n" + "짧은 꼬리."
chunks = chunk_markdown(tail_doc)
check("짧은 꼬리 병합 (MIN_CHARS 미만)", len(chunks) == 1 and "짧은 꼬리" in chunks[0].content)

# ── 해시 기반 재적재 판단
raw = (KNOWLEDGE / "rebalancing.md").read_text()
check("같은 원문 → 같은 해시", content_hash(raw) == content_hash(raw))
check("한 글자 바뀌면 다른 해시", content_hash(raw) != content_hash(raw + " "))

# ── 가짜 임베더
fe = FakeEmbedder()
v1, v2 = fe.embed(["최대낙폭", "최대낙폭"], task="RETRIEVAL_DOCUMENT")
v3 = fe.embed(["변동성"], task="RETRIEVAL_QUERY")[0]
check(f"가짜 임베더 차원 = {EMBEDDING_DIM}", len(v1) == EMBEDDING_DIM)
check("같은 텍스트 → 같은 벡터 (결정성)", v1 == v2)
check("다른 텍스트 → 다른 벡터", v1 != v3)
check("단위 벡터 (코사인용)", abs(sum(x * x for x in v1) - 1.0) < 1e-6)

# ── 보고
fails = [r for r in results if not r[1]]
for name, ok, detail in results:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  → {detail}" if (detail and not ok) else ""))
print(f"\n=== {len(results) - len(fails)}/{len(results)} 통과 ===")
sys.exit(1 if fails else 0)
