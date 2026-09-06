"""임베딩 (KAN-16). Gemini 임베딩 계열로 통일.

모델·차원은 환경변수로 덮을 수 있다. pgvector 컬럼이 vector(768)로 고정되어 있으므로
EMBEDDING_DIM을 바꾸면 db/V1__knowledge.sql 도 같이 바꾸고 전체 재적재해야 한다.

⚠ 모델 ID와 SDK 표면은 첫 실호출 때 확인할 것. 키가 없어 검증하지 못했다.
"""

from __future__ import annotations

import hashlib
import os
from typing import Protocol

EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "gemini-embedding-001")
EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "768"))


class Embedder(Protocol):
    def embed(self, texts: list[str], *, task: str) -> list[list[float]]: ...


class GeminiEmbedder:
    """task: 'RETRIEVAL_DOCUMENT'(적재) | 'RETRIEVAL_QUERY'(검색)."""

    def __init__(self, client=None) -> None:
        if client is None:
            from google import genai
            key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
            if not key:
                raise RuntimeError("GEMINI_API_KEY가 설정되지 않았습니다")
            client = genai.Client(api_key=key)
        self._client = client

    def embed(self, texts: list[str], *, task: str) -> list[list[float]]:
        from google.genai import types
        resp = self._client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=texts,
            config=types.EmbedContentConfig(task_type=task, output_dimensionality=EMBEDDING_DIM),
        )
        vectors = [list(e.values) for e in resp.embeddings]
        for v in vectors:
            if len(v) != EMBEDDING_DIM:
                raise RuntimeError(f"임베딩 차원 불일치: {len(v)} != {EMBEDDING_DIM}")
        return vectors


class FakeEmbedder:
    """네트워크 없이 파이프라인을 검증하기 위한 결정론적 가짜 임베더.
    같은 텍스트 → 같은 벡터. 의미 유사도는 없다 (테스트·로컬 구조 검증 전용)."""

    def embed(self, texts: list[str], *, task: str) -> list[list[float]]:
        out = []
        for t in texts:
            h = hashlib.sha256(t.encode()).digest()
            vec = [((h[i % 32] / 255.0) * 2 - 1) for i in range(EMBEDDING_DIM)]
            norm = sum(x * x for x in vec) ** 0.5 or 1.0
            out.append([x / norm for x in vec])
        return out
