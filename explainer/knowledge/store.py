"""pgvector 적재·검색 (KAN-16).

접속 정보는 DATABASE_URL 환경변수로만 받는다. 코드에 비밀값 없음.
로컬(docker compose)과 RDS 모두 같은 코드로 동작한다 — 차이는 URL뿐.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import psycopg
from pgvector import Vector
from pgvector.psycopg import register_vector

from .chunking import Chunk


def connect() -> psycopg.Connection:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL이 설정되지 않았습니다")
    conn = psycopg.connect(url)
    register_vector(conn)
    return conn


@dataclass(frozen=True)
class DocumentMeta:
    source_id: str
    title: str
    source_url: str | None
    published_at: str | None      # YYYY-MM-DD
    license: str | None
    concept_tags: list[str]


@dataclass(frozen=True)
class SearchHit:
    ref: str                      # evidence 표기: source_id#chunk_index (재적재에도 안정)
    content: str
    location: str | None
    title: str
    source_url: str | None
    concept_tags: list[str]
    score: float                  # 코사인 유사도 (1 - distance)


def upsert_document(conn: psycopg.Connection, meta: DocumentMeta, content_hash: str) -> tuple[int, bool]:
    """문서 메타를 넣거나 갱신한다. (document_id, 내용이_바뀌었는가)

    해시가 같으면 청크를 건드리지 않는다 → 재실행해도 청크 수 불변 (수용 기준).
    """
    with conn.cursor() as cur:
        cur.execute("SELECT id, content_hash FROM knowledge_document WHERE source_id = %s", (meta.source_id,))
        row = cur.fetchone()
        if row and row[1] == content_hash:
            return row[0], False
        cur.execute(
            """
            INSERT INTO knowledge_document
                (source_id, title, source_url, published_at, license, concept_tags, content_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_id) DO UPDATE SET
                title = EXCLUDED.title, source_url = EXCLUDED.source_url,
                published_at = EXCLUDED.published_at, license = EXCLUDED.license,
                concept_tags = EXCLUDED.concept_tags, content_hash = EXCLUDED.content_hash,
                updated_at = now()
            RETURNING id
            """,
            (meta.source_id, meta.title, meta.source_url, meta.published_at,
             meta.license, meta.concept_tags, content_hash),
        )
        doc_id = cur.fetchone()[0]
        cur.execute("DELETE FROM knowledge_chunk WHERE document_id = %s", (doc_id,))
        return doc_id, True


def insert_chunks(conn: psycopg.Connection, doc_id: int, chunks: list[Chunk],
                  vectors: list[list[float]], concept_tags: list[str]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO knowledge_chunk
                (document_id, chunk_index, content, embedding, concept_tags, location, char_count)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            # list[float] 그대로 보내면 float8[] 로 나간다 — 대입은 암시적 캐스트로 통과하지만 <=> 연산은 실패.
            # 9/4 Docker 실검증에서 발견. Vector 로 감싸 vector 타입으로 보낸다.
            [(doc_id, c.index, c.content, Vector(v), concept_tags, c.location, c.char_count)
             for c, v in zip(chunks, vectors)],
        )


def search(conn: psycopg.Connection, query_vector: list[float], *, k: int = 3,
           tags: list[str] | None = None) -> list[SearchHit]:
    """코사인 유사도 top-k. tags를 주면 그 개념 태그를 가진 청크로 좁힌다.

    tags 필터가 있으면 「시뮬레이터 결과 필드 → 개념」 결정론 경로,
    없으면 KAN-16이 말하는 「개념 질의」 벡터 검색 경로다. 둘 다 이 함수 하나로 처리한다.
    """
    where = "WHERE c.concept_tags && %s" if tags else ""
    qv = Vector(query_vector)          # float8[] 가 아니라 vector 로. (9/4 실검증: 없으면 'operator does not exist: vector <=> double precision[]')
    params: list = [qv]
    if tags:
        params.append(tags)
    params += [qv, k]
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT d.source_id, c.chunk_index, c.content, c.location, d.title, d.source_url,
                   c.concept_tags, 1 - (c.embedding <=> %s) AS score
            FROM knowledge_chunk c JOIN knowledge_document d ON d.id = c.document_id
            {where}
            ORDER BY c.embedding <=> %s
            LIMIT %s
            """,
            params,
        )
        return [
            SearchHit(ref=f"{r[0]}#{r[1]}", content=r[2], location=r[3], title=r[4],
                      source_url=r[5], concept_tags=list(r[6]), score=float(r[7]))
            for r in cur.fetchall()
        ]


def chunk_exists(conn: psycopg.Connection, ref: str) -> bool:
    """guardrail C3 확장용 — evidence의 chunk:<ref>가 실제로 DB에 있는가."""
    source_id, _, idx = ref.rpartition("#")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM knowledge_chunk c JOIN knowledge_document d ON d.id = c.document_id "
            "WHERE d.source_id = %s AND c.chunk_index = %s",
            (source_id, int(idx)),
        )
        return cur.fetchone() is not None


def counts(conn: psycopg.Connection) -> tuple[int, int]:
    with conn.cursor() as cur:
        cur.execute("SELECT (SELECT count(*) FROM knowledge_document), (SELECT count(*) FROM knowledge_chunk)")
        d, c = cur.fetchone()
        return d, c
