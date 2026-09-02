"""검색 결합 (KAN-17) — 시뮬레이터 결과를 보고 어떤 개념 청크를 프롬프트에 넣을지 정한다.

두 경로가 같은 인터페이스를 갖는다.
  DbRetriever   pgvector (store.search, tags 필터). DATABASE_URL이 있을 때
  FileRetriever knowledge/*.md 를 직접 읽어 태그로 고름. DB 없이도 파이프라인 전체가 돈다

검색 트리거는 사용자 질문이 아니라 결과 JSON의 필드다 (결정론). 어떤 청크가 들어갈지
예측 가능해서 테스트하기 쉽고, 청크는 개념 설명 전용이라 수치 검증(C4)을 건드리지 않는다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..schema import SimulationInput
from .chunking import chunk_markdown, parse_frontmatter

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent.parent / "knowledge"

# 결과 JSON의 어느 필드가 있으면 어떤 개념을 설명해야 하는가
FIELD_TO_TAGS: list[tuple[str, list[str]]] = [
    ("meta.window",              ["baseline_window"]),
    ("per_period.*.risk.mdd_pct", ["max_drawdown"]),
    ("per_period.*.risk.vol_annual_pct", ["annual_volatility"]),
    ("per_period.*.risk.max_drift_pct",  ["target_weights"]),
    ("per_period.*.cum_cost",    ["transaction_cost"]),
    ("meta.safe_rate_annual_pct", ["safe_rate"]),
]
ALWAYS_TAGS = ["rebalancing", "disclaimer"]


@dataclass(frozen=True)
class Chunk:
    ref: str          # source_id#index — evidence 표기 chunk:<ref>
    title: str
    location: str
    content: str
    concept_tags: list[str]


class Retriever(Protocol):
    def retrieve(self, source: SimulationInput, *, per_tag: int = 1) -> list[Chunk]: ...
    def exists(self, ref: str) -> bool: ...


def concept_tags_for(source: SimulationInput) -> list[str]:
    """결과에 실제로 있는 필드로부터 설명이 필요한 개념 태그를 결정론적으로 뽑는다."""
    d = source.model_dump(mode="json", exclude_none=True)
    tags: list[str] = list(ALWAYS_TAGS)

    def present(path: str) -> bool:
        parts = path.split(".")
        def walk(node, i):
            if i == len(parts):
                return node is not None
            key = parts[i]
            if key == "*":
                return isinstance(node, dict) and any(walk(v, i + 1) for v in node.values())
            return isinstance(node, dict) and key in node and walk(node[key], i + 1)
        return walk(d, 0)

    for path, t in FIELD_TO_TAGS:
        if present(path):
            for x in t:
                if x not in tags:
                    tags.append(x)
    return tags


class FileRetriever:
    """knowledge/*.md 를 읽어 태그가 맞는 문서의 첫 청크(정의)를 돌려준다. DB·임베딩 불필요."""

    def __init__(self, knowledge_dir: Path = KNOWLEDGE_DIR) -> None:
        self._docs: dict[str, tuple[dict, list]] = {}
        for p in sorted(knowledge_dir.glob("*.md")):
            meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
            if "source_id" in meta:
                self._docs[meta["source_id"]] = (meta, chunk_markdown(body))

    def _tags(self, meta: dict) -> list[str]:
        return [t.strip() for t in meta.get("concept_tags", "").split(",") if t.strip()]

    def retrieve(self, source: SimulationInput, *, per_tag: int = 1) -> list[Chunk]:
        wanted = concept_tags_for(source)
        out: list[Chunk] = []
        seen: set[str] = set()
        for tag in wanted:
            n = 0
            for sid, (meta, chunks) in self._docs.items():
                if tag in self._tags(meta) and sid not in seen and chunks:
                    c = chunks[0]                       # 정의 절
                    out.append(Chunk(ref=f"{sid}#{c.index}", title=meta.get("title", sid),
                                     location=c.location, content=c.content,
                                     concept_tags=self._tags(meta)))
                    seen.add(sid); n += 1
                    if n >= per_tag:
                        break
        return out

    def exists(self, ref: str) -> bool:
        sid, _, idx = ref.rpartition("#")
        doc = self._docs.get(sid)
        return bool(doc) and idx.isdigit() and int(idx) < len(doc[1])


class DbRetriever:
    """pgvector 경로. 태그 필터 + 코사인 top-k. 질의 임베딩은 개념 태그명을 쓴다."""

    def __init__(self, conn=None, embedder=None) -> None:
        from . import store
        from .embedding import GeminiEmbedder
        self._store = store
        self._conn = conn or store.connect()
        self._embedder = embedder or GeminiEmbedder()

    def retrieve(self, source: SimulationInput, *, per_tag: int = 1) -> list[Chunk]:
        out: list[Chunk] = []
        for tag in concept_tags_for(source):
            [qv] = self._embedder.embed([tag], task="RETRIEVAL_QUERY")
            for h in self._store.search(self._conn, qv, k=per_tag, tags=[tag]):
                out.append(Chunk(ref=h.ref, title=h.title, location=h.location or "",
                                 content=h.content, concept_tags=h.concept_tags))
        return out

    def exists(self, ref: str) -> bool:
        return self._store.chunk_exists(self._conn, ref)


def default_retriever() -> Retriever:
    """DATABASE_URL이 있으면 DB, 없으면 파일. api.py와 run.py가 쓴다."""
    if os.environ.get("DATABASE_URL"):
        try:
            return DbRetriever()
        except Exception:      # DB 못 붙으면 파일로 내려간다 — 설명이 통째로 죽는 것보다 낫다
            pass
    return FileRetriever()
