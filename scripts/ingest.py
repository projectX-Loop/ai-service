#!/usr/bin/env python3
"""적재 스크립트 (KAN-16): knowledge/*.md → 청크 → 임베딩 → pgvector.

  python scripts/ingest.py                 # 실제 임베딩 (GEMINI_API_KEY 필요)
  python scripts/ingest.py --fake          # 가짜 임베더로 구조만 검증 (키 불필요)
  python scripts/ingest.py --dry-run       # DB·임베딩 없이 청킹 결과만 출력

재실행 안전: 문서 해시가 같으면 건너뛴다. 같은 문서를 몇 번 돌려도 청크 수는 변하지 않는다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from explainer.knowledge.chunking import chunk_markdown, content_hash, parse_frontmatter  # noqa: E402

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"


def load_documents() -> list[tuple[dict, str, str, Path]]:
    """(메타, 본문, 원문해시, 경로). 원문 전체(프론트매터 포함)를 해시한다."""
    docs = []
    for path in sorted(KNOWLEDGE_DIR.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(raw)
        if "source_id" not in meta:
            print(f"건너뜀 (source_id 없음): {path.name}", file=sys.stderr)
            continue
        docs.append((meta, body, content_hash(raw), path))
    return docs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fake", action="store_true", help="FakeEmbedder 사용 (키 불필요)")
    ap.add_argument("--dry-run", action="store_true", help="청킹만 하고 DB·임베딩 생략")
    args = ap.parse_args()

    docs = load_documents()
    print(f"문서 {len(docs)}건")

    if args.dry_run:
        total = 0
        for meta, body, h, path in docs:
            chunks = chunk_markdown(body)
            total += len(chunks)
            print(f"\n{meta['source_id']}  ({path.name}, hash {h[:8]})")
            for c in chunks:
                print(f"  #{c.index} [{c.location}] {c.char_count}자")
        print(f"\n청크 합계 {total}")
        return 0

    from explainer.knowledge import store
    from explainer.knowledge.embedding import FakeEmbedder, GeminiEmbedder

    embedder = FakeEmbedder() if args.fake else GeminiEmbedder()
    conn = store.connect()
    before = store.counts(conn)

    changed = skipped = 0
    for meta, body, h, path in docs:
        tags = [t.strip() for t in meta.get("concept_tags", "").split(",") if t.strip()]
        dmeta = store.DocumentMeta(
            source_id=meta["source_id"], title=meta.get("title", path.stem),
            source_url=meta.get("source_url") or None, published_at=meta.get("published_at") or None,
            license=meta.get("license") or None, concept_tags=tags,
        )
        doc_id, is_changed = store.upsert_document(conn, dmeta, h)
        if not is_changed:
            skipped += 1
            continue
        chunks = chunk_markdown(body)
        vectors = embedder.embed([c.content for c in chunks], task="RETRIEVAL_DOCUMENT")
        store.insert_chunks(conn, doc_id, chunks, vectors, tags)
        changed += 1
        print(f"적재: {meta['source_id']}  청크 {len(chunks)}")

    conn.commit()
    after = store.counts(conn)
    print(f"\n변경 {changed} / 건너뜀 {skipped}")
    print(f"문서 {before[0]} → {after[0]}, 청크 {before[1]} → {after[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
