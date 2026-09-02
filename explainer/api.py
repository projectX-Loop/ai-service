"""내부 HTTP 인터페이스 — Spring 백엔드가 호출한다 (KAN-17).

계약 요약
  POST /rag/answer   시뮬레이션 결과 JSON → AI 설명
  GET  /health       docker compose healthcheck

설계 원칙
  · 처리가 정상적으로 끝나면 항상 200을 준다. 설명이 나왔는지는 `status`로 구분한다.
    Spring이 예외 분기를 안 해도 결과 화면을 그릴 수 있게 하기 위해서다.
    (KAN-17: "재실패 시 설명 없이 결과만 반환하는 규약을 유지")
  · 4xx/5xx는 진짜 실패에만 쓴다 — 입력이 스키마에 안 맞거나(422), 모델이 죽었거나(503).
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from . import client as llm
from .knowledge.retrieve import default_retriever
from .schema import Explanation, SimulationInput

log = logging.getLogger("ai-service")

app = FastAPI(title="ai-service", version="0.1.0")
_retriever = default_retriever()      # DATABASE_URL 있으면 pgvector, 없으면 knowledge/*.md


class AnswerResponse(BaseModel):
    """Spring이 받는 응답. explanation이 null이면 결과 화면만 그리면 된다."""

    status: str = Field(description="OK | EXPLANATION_REJECTED | EXPLANATION_UNAVAILABLE")
    explanation: Explanation | None = None
    attempts: int | None = Field(default=None, description="모델 호출 횟수")
    retrieved_refs: list[str] = Field(default_factory=list, description="프롬프트에 넣은 지식 청크 참조 (KAN-17)")
    violations: list[str] = Field(default_factory=list, description="가드레일 위반 내역")
    message: str | None = Field(default=None, description="Spring이 사용자 문구로 바꿀 사유")


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "model": llm.MODEL,
        "credentials": llm.has_credentials(),
        "retriever": type(_retriever).__name__,
    }


@app.post("/rag/answer", response_model=AnswerResponse, response_model_exclude_none=False)
def answer(payload: dict) -> JSONResponse:
    # 1) 입력 파싱 — 스키마가 관대하므로 모르는 필드는 무시된다.
    try:
        source = SimulationInput.model_validate(payload)
    except ValidationError as e:
        return JSONResponse(
            status_code=422,
            content={
                "status": "INVALID_INPUT",
                "explanation": None,
                "violations": [f"{'.'.join(str(x) for x in d['loc'])}: {d['msg']}" for d in e.errors()],
                "message": "시뮬레이션 결과 JSON이 AI 설명 입력 규격과 맞지 않습니다.",
            },
        )

    # 2) 설명 생성 + 가드레일. 실패해도 Spring이 결과 화면은 그릴 수 있어야 한다.
    try:
        outcome = llm.explain(source, retriever=_retriever)
    except llm.ExplanationRejected as e:
        log.warning("guardrail rejected: %s", e)
        return JSONResponse(
            status_code=200,
            content={
                "status": "EXPLANATION_REJECTED",
                "explanation": None,
                "attempts": llm.MAX_ATTEMPTS,
                "violations": [str(v) for v in e.report.errors],
                "message": "AI 설명을 생성하지 못했습니다. 분석 결과는 정상입니다.",
            },
        )
    except llm.ExplanationUnavailable as e:
        log.error("model unavailable: %s", e)
        return JSONResponse(
            status_code=200,
            content={
                "status": "EXPLANATION_UNAVAILABLE",
                "explanation": None,
                "violations": [],
                "message": "AI 설명을 잠시 사용할 수 없습니다. 다시 시도해 주세요.",
            },
        )

    return JSONResponse(
        status_code=200,
        content={
            "status": "OK",
            "explanation": outcome.explanation.model_dump(mode="json"),
            "attempts": outcome.attempts,
            "retrieved_refs": outcome.chunk_refs,
            "violations": [str(v) for v in outcome.report.warnings],
            "message": None,
        },
    )
