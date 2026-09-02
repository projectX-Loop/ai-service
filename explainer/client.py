"""AI 설명 모듈 — 시뮬레이션 결과를 받아 검증된 설명을 돌려준다.

KAN-17의 POST /rag/answer 가 실행하는 코드가 이것이다.
백엔드는 explain()만 호출하면 된다.

모델: Gemini Flash (2026-09-02 팀 확정). provider에 묶인 코드는 이 파일뿐이고
schema / prompt / guardrail 은 provider와 무관하다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from . import prompt
from .guardrail import Report, validate
from .knowledge.retrieve import Retriever
from .schema import Explanation, SimulationInput

# 모델 ID는 바뀌므로 환경변수로 덮을 수 있게 둔다.
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
MAX_ATTEMPTS = 2          # 최초 1회 + 재생성 1회. 무한 재시도는 3분 기준을 깬다.


class ExplanationRejected(RuntimeError):
    """재생성해도 가드레일을 통과하지 못함 → EXPLANATION_REJECTED."""

    def __init__(self, report: Report) -> None:
        self.report = report
        super().__init__("가드레일 검증 실패: " + "; ".join(str(v) for v in report.errors))


class ExplanationUnavailable(RuntimeError):
    """모델 호출 자체가 실패 → EXPLANATION_UNAVAILABLE."""


@dataclass
class ExplainOutcome:
    explanation: Explanation
    report: Report
    attempts: int
    chunk_refs: list[str]          # 프롬프트에 넣은 청크 참조 (KAN-17 retrieved_refs 원천)


def _config() -> types.GenerateContentConfig:
    """구조화 출력 강제. 스키마가 형식을 보장하고 가드레일이 내용을 검증한다."""
    return types.GenerateContentConfig(
        system_instruction=prompt.SYSTEM,
        response_mime_type="application/json",
        response_schema=Explanation,
        temperature=0,          # 같은 입력에 같은 설명이 나오도록
    )


def _parse(response) -> Explanation:
    """response.parsed 를 우선 쓰고, 없으면 본문 JSON을 직접 파싱한다."""
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, Explanation):
        return parsed
    if isinstance(parsed, dict):
        return Explanation.model_validate(parsed)

    text = getattr(response, "text", None)
    if not text:
        # 안전 필터 등으로 본문이 비어 돌아오는 경우
        raise ExplanationUnavailable("모델이 빈 응답을 반환했습니다 (안전 필터 가능성)")
    return Explanation.model_validate(json.loads(text))


def explain(source: SimulationInput, *, client: genai.Client | None = None,
            retriever: Retriever | None = None) -> ExplainOutcome:
    """KAN-17 결합: 결과 JSON → 개념 청크 검색 → 프롬프트 → Gemini → 가드레일(청크 실존 포함)."""
    client = client or genai.Client(api_key=_api_key())

    # 검색은 결과 필드 기반(결정론). 실패해도 설명 자체는 진행한다 — 청크 없이.
    chunks: list[dict] = []
    chunk_exists = None
    if retriever is not None:
        try:
            found = retriever.retrieve(source)
            chunks = [{"ref": c.ref, "title": c.title, "location": c.location, "content": c.content}
                      for c in found]
            chunk_exists = retriever.exists
        except Exception:
            chunks, chunk_exists = [], None

    contents: list[types.Content] = [
        types.Content(role="user", parts=[types.Part(text=prompt.build_user_message(source, chunks))])
    ]

    last_report: Report | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = client.models.generate_content(
                model=MODEL, contents=contents, config=_config()
            )
        except genai_errors.APIError as e:
            raise ExplanationUnavailable(str(e)) from e

        explanation = _parse(response)
        report = validate(explanation, source, chunk_exists=chunk_exists)
        if report.passed:
            return ExplainOutcome(explanation, report, attempt, [c["ref"] for c in chunks])

        last_report = report
        if attempt < MAX_ATTEMPTS:
            contents.append(
                types.Content(
                    role="model",
                    parts=[types.Part(text=explanation.model_dump_json())],
                )
            )
            contents.append(
                types.Content(
                    role="user",
                    parts=[types.Part(
                        text=prompt.build_retry_message([str(v) for v in report.errors])
                    )],
                )
            )

    raise ExplanationRejected(last_report)


def _api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise ExplanationUnavailable("GEMINI_API_KEY가 설정되지 않았습니다")
    return key


def has_credentials() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
