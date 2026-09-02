"""청킹 규칙 (KAN-16). 순수 함수 — 네트워크·DB 없음.

규칙
  1. 마크다운 `## ` 섹션 단위로 먼저 자른다. 출처 위치(location)가 섹션 제목이 된다.
  2. 섹션이 MAX_CHARS를 넘으면 문단(빈 줄) 단위로 나눠 그리디하게 채운다.
  3. 나뉜 조각 사이에는 직전 문단 하나를 겹친다(overlap). 문맥 단절을 줄인다.
  4. MIN_CHARS 미만의 꼬리 조각은 앞 조각에 붙인다.

왜 이 값인가 — 근거는 docs/KAN-16-지식저장소.md.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

MAX_CHARS = 600
MIN_CHARS = 80

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_SECTION = re.compile(r"^##\s+(.+)$", re.M)


@dataclass(frozen=True)
class Chunk:
    index: int
    content: str
    location: str          # 섹션 제목

    @property
    def char_count(self) -> int:
        return len(self.content)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """`---` 블록의 key: value 를 dict로. 본문은 나머지."""
    m = _FRONTMATTER.match(text)
    if not m:
        return {}, text
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, text[m.end():]


def _sections(body: str) -> list[tuple[str, str]]:
    """(섹션 제목, 섹션 본문) 목록. 첫 헤딩 앞 텍스트는 '(서두)'."""
    parts = _SECTION.split(body)
    out: list[tuple[str, str]] = []
    if parts[0].strip():
        out.append(("(서두)", parts[0].strip()))
    for i in range(1, len(parts), 2):
        out.append((parts[i].strip(), parts[i + 1].strip()))
    return out


def _pack(paragraphs: list[str], max_chars: int) -> list[str]:
    """문단을 max_chars 안에서 그리디로 묶고, 조각 사이에 직전 문단 하나를 겹친다."""
    pieces: list[str] = []
    cur: list[str] = []
    for p in paragraphs:
        if cur and len("\n\n".join(cur + [p])) > max_chars:
            pieces.append("\n\n".join(cur))
            cur = [cur[-1], p]          # overlap = 직전 문단
        else:
            cur.append(p)
    if cur:
        pieces.append("\n\n".join(cur))
    # 짧은 꼬리는 앞에 붙인다
    if len(pieces) >= 2 and len(pieces[-1]) < MIN_CHARS:
        pieces[-2] = pieces[-2] + "\n\n" + pieces[-1]
        pieces.pop()
    return pieces


def chunk_markdown(body: str, *, max_chars: int = MAX_CHARS) -> list[Chunk]:
    chunks: list[Chunk] = []
    for title, text in _sections(body):
        if not text:
            continue
        if len(text) <= max_chars:
            pieces = [text]
        else:
            paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
            pieces = _pack(paragraphs, max_chars)
        for piece in pieces:
            chunks.append(Chunk(index=len(chunks), content=piece, location=title))
    return chunks
