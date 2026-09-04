"""AI 설명 모듈 — 시뮬레이션 결과를 받아 검증된 설명을 돌려준다.

KAN-17의 POST /rag/answer 가 실행하는 코드가 이것이다.
백엔드는 explain()만 호출하면 된다.

모델: Gemini Flash (2026-09-02 팀 확정). provider에 묶인 코드는 이 파일뿐이고
schema / prompt / guardrail 은 provider와 무관하다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field

from . import prompt
from .guardrail import Report, validate
from .knowledge.retrieve import Retriever
from .schema import Explanation, Period, ProsCons, SimulationInput

# 모델 ID는 바뀌므로 환경변수로 덮을 수 있게 둔다.
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
MAX_ATTEMPTS = 2          # 최초 1회 + 재생성 1회. 무한 재시도는 3분 기준을 깬다.
REQUEST_TIMEOUT_MS = int(os.environ.get("GEMINI_TIMEOUT_MS", "45000"))   # 실측 1회 생성 20~36초


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
    retry_reasons: list[str] = field(default_factory=list)   # 1회차가 반려됐을 때의 위반 목록 (로그·KAN-13 근거)


# ─── Gemini 와이어 스키마
# Explanation.per_period_pros_cons 는 dict[Period, ProsCons] 라 JSON Schema에서
# additionalProperties 로 나오는데, Gemini Developer API는 이를 거부한다(Vertex만 지원).
# 그래서 모델에게 보내는 스키마만 M·Q·H 고정 필드 객체로 바꾼다. 생성되는 JSON은
# {"M": …, "Q": …, "H": …} 로 dict 형태와 글자 단위로 같아서 Explanation 으로 그대로 파싱된다.
# 공개 계약(schema.py · Spring DTO · 가드레일)은 건드리지 않는다.


class _WirePerPeriod(BaseModel):
    model_config = ConfigDict(extra="forbid")

    M: ProsCons = Field(description="월별 리밸런싱의 장단점")
    Q: ProsCons = Field(description="분기별 리밸런싱의 장단점")
    H: ProsCons = Field(description="반기별 리밸런싱의 장단점")


class _WireExplanation(Explanation):
    """Explanation 과 필드·설명이 같고 per_period_pros_cons 만 고정 키 객체."""

    per_period_pros_cons: _WirePerPeriod = Field(  # type: ignore[assignment]
        description="M·Q·H 세 주기 전부. 각 장점≥1·단점≥1"
    )


def _strip_additional_properties(node):
    """extra="forbid" 가 만드는 additionalProperties:false 도 Developer API는 거부한다. 재귀 제거."""
    if isinstance(node, dict):
        node.pop("additionalProperties", None)
        for v in node.values():
            _strip_additional_properties(v)
    elif isinstance(node, list):
        for v in node:
            _strip_additional_properties(v)
    return node


# evidence 항목 형식. 실호출에서 모델이 경로 뒤에 잡음 토큰을 붙이는 일이 잦아(C3 반려) 스키마로 막는다.
EVIDENCE_PATTERN = r"^(/[A-Za-z0-9_./\-]+|chunk:[A-Za-z0-9_./\-]+#[0-9]+)$"


def _constrain_evidence(node) -> None:
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict) and isinstance(props.get("evidence"), dict):
            items = props["evidence"].get("items")
            if isinstance(items, dict) and items.get("type") == "string":
                items["pattern"] = EVIDENCE_PATTERN
        for v in node.values():
            _constrain_evidence(v)
    elif isinstance(node, list):
        for v in node:
            _constrain_evidence(v)


def _response_schema() -> dict:
    schema = _strip_additional_properties(_WireExplanation.model_json_schema())
    _constrain_evidence(schema)
    return schema


def _config() -> types.GenerateContentConfig:
    """구조화 출력 강제. 스키마가 형식을 보장하고 가드레일이 내용을 검증한다."""
    return types.GenerateContentConfig(
        system_instruction=prompt.SYSTEM,
        response_mime_type="application/json",
        response_schema=_response_schema(),
        temperature=0,          # 같은 입력에 같은 설명이 나오도록
        # 도구를 안 쓰므로 AFC 끔 — 켜두면 SDK가 매 호출 경고 로그를 찍는다.
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )


def _parse(response) -> Explanation:
    """response.parsed 를 우선 쓰고, 없으면 본문 JSON을 직접 파싱한다."""
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, _WireExplanation):
        # 와이어 모양 → 계약 모양. JSON은 동일하므로 덤프 후 재검증만 한다.
        return Explanation.model_validate(parsed.model_dump(mode="json"))
    if isinstance(parsed, Explanation):
        return parsed
    if isinstance(parsed, BaseModel):
        return Explanation.model_validate(parsed.model_dump(mode="json"))
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
    client = client or genai.Client(api_key=_api_key(), http_options=_http_options())

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
            return ExplainOutcome(explanation, report, attempt, [c["ref"] for c in chunks],
                                  retry_reasons=[str(v) for v in last_report.errors] if last_report else [])

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


def _http_options() -> types.HttpOptions:
    """SDK 재시도·타임아웃 상한. 기본값(5회, 백오프 최대 60초)은 503 한 번에 수 분을 끌어
    Spring 쪽 타임아웃을 넘긴다. 실패는 빨리 EXPLANATION_UNAVAILABLE로 돌려 Spring이 재시도하게 한다."""
    return types.HttpOptions(
        timeout=REQUEST_TIMEOUT_MS,
        # 429(쿼터)는 몇 초 뒤 재시도해도 안 풀리고 서버가 준 retryDelay(30초+)를 기다리게 되므로 제외.
        retry_options=types.HttpRetryOptions(attempts=2, initial_delay=1.0, max_delay=2.0,
                                             http_status_codes=[408, 500, 502, 503, 504]),
    )


def _api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise ExplanationUnavailable("GEMINI_API_KEY가 설정되지 않았습니다")
    return key


def has_credentials() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
