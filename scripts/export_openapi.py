"""OpenAPI 문서 생성 — KAN-4 산출물 "OpenAPI 초안 또는 동등한 API 명세".

두 파일을 docs/openapi/ 에 쓴다. 손으로 고치지 않는다 — 원본은 코드다.
  public-api.openapi.json   브라우저 ↔ Spring 공개 API. 경로·상태 코드는 노션 §4, JSON 은 explainer/public_api.py
  ai-service.openapi.json   Spring ↔ ai-service 내부 HTTP (KAN-17). FastAPI 앱에서 그대로 추출

    ./.venv/bin/python scripts/export_openapi.py            # 생성
    ./.venv/bin/python scripts/export_openapi.py --check    # 디스크 파일이 코드와 같은지 (테스트가 부른다)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pydantic.json_schema import models_json_schema  # noqa: E402

from explainer import public_api as pub  # noqa: E402
from explainer.schema import Explanation, SimulationInput  # noqa: E402

OUT_DIR = ROOT / "docs" / "openapi"
EXAMPLES = OUT_DIR / "examples"
PUBLIC_PATH = OUT_DIR / "public-api.openapi.json"
INTERNAL_PATH = OUT_DIR / "ai-service.openapi.json"
REF = "#/components/schemas/{model}"


def _example(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def _ref(model: type) -> dict:
    return {"$ref": REF.format(model=model.__name__)}


def _body(model: type, examples: list[str], description: str = "") -> dict:
    content: dict = {"schema": _ref(model)}
    if examples:
        content["examples"] = {Path(f).stem: {"value": _example(f)} for f in examples}
    return {"description": description, "content": {"application/json": content}}


def _err(description: str, example: str) -> dict:
    return _body(pub.ErrorEnvelope, [example], description)


def _public_id_param() -> dict:
    return {
        "name": "public_id", "in": "path", "required": True,
        "schema": {"type": "string", "format": "uuid"},
        "description": "POST /plans 응답의 plan.public_id",
    }


def build_public() -> dict:
    """공개 API. 경로·상태 코드·흐름 = 노션 「프론트-백엔드 계약 정리」 §4 (도윤 9/3 14:30)."""
    models = [pub.PlanInputs, pub.PlanResponse, pub.ExplanationResponse,
              pub.UniverseResponse, pub.SamplesResponse, pub.ErrorEnvelope]
    _, defs = models_json_schema([(m, "validation") for m in models], ref_template=REF)

    paths = {
        "/plans": {
            "post": {
                "summary": "plan 생성 + 계산",
                "description": (
                    "Kan-9 §2 정적 검증 → 현재 스냅샷 로드 → plan·inv_account·inv_holding 저장(한 트랜잭션) → "
                    "ai-service POST /calculate (본문 = 요청 dict 그대로, 타임아웃 10초) → 201.\n\n"
                    "계산 실패 시 plan 은 저장된 채 502 CALCULATION_FAILED (retryable, public_id 동봉)."
                ),
                "requestBody": {"required": True, **_body(pub.PlanInputs, ["plans.request.P0.json"], "Kan-9 §2 입력 8필드 (v0.2). v0.3 필드가 있으면 400 UNSUPPORTED_FIELD")},
                "responses": {
                    "201": _body(pub.PlanResponse, ["plans.response.P0.json"], "plan + calculation (analyze() 출력 그대로)"),
                    "400": _err("입력 검증 실패 (여러 건을 errors[] 로 한 번에) · v0.3 필드 · 데이터 의존 오류", "error.validation.json"),
                    "500": _err("ai-service 스냅샷과 DB data_snapshot(is_current) 불일치", "error.snapshot_mismatch.json"),
                    "502": _err("계산 실패. plan 은 저장됨 → GET /plans/{public_id} 로 재시도", "error.calculation_failed.json"),
                },
            }
        },
        "/plans/{public_id}": {
            "get": {
                "summary": "plan 재조회 = 재계산",
                "description": "저장된 inputs 로 다시 계산한다(결과 저장 없음). 새로고침·공유 링크 대응. 본문은 POST /plans 와 동일.",
                "parameters": [_public_id_param()],
                "responses": {
                    "200": _body(pub.PlanResponse, ["plans.response.P0.json"]),
                    "404": _err("PLAN_NOT_FOUND", "error.not_found.json"),
                    "502": _err("CALCULATION_FAILED", "error.calculation_failed.json"),
                },
            }
        },
        "/plans/{public_id}/explanation": {
            "post": {
                "summary": "AI 설명 (결과 화면과 분리해서 나중에 채운다)",
                "description": (
                    "재계산 → analyze() 출력 + rebalancing.focus + goal.amount → ai-service POST /rag/answer (타임아웃 30초).\n\n"
                    "ai-service 규약대로 **처리가 끝나면 항상 200**, 성패는 `status`. 결과 화면은 그대로 두고 설명 영역만 바꾼다. "
                    "ai-service 불가·타임아웃만 502 EXPLANATION_UNAVAILABLE (retryable)."
                ),
                "parameters": [_public_id_param()],
                "responses": {
                    "200": _body(pub.ExplanationResponse,
                                 ["explanation.response.ok.json", "explanation.response.rejected.json", "explanation.response.unavailable.json"],
                                 "status 로 분기. OK 면 explanation, 아니면 message"),
                    "404": _err("PLAN_NOT_FOUND", "error.not_found.json"),
                    "502": _err("ai-service 불가·타임아웃", "error.explanation_unavailable.json"),
                },
            }
        },
        "/universe": {
            "get": {
                "summary": "입력 폼 자산 선택지 + 데이터 기준",
                "description": "data_snapshot(is_current) + 카탈로그. group=base 3종은 항상, optional(US_EQ_KR)은 카탈로그에 있을 때.",
                "responses": {"200": _body(pub.UniverseResponse, ["universe.response.json"])},
            }
        },
        "/samples": {
            "get": {
                "summary": "대표 페르소나 입력값 (PRD 수용기준 5 · KAN-14)",
                "description": "로그인 없이 샘플로 재현. inputs 를 그대로 POST /plans 에 보내면 된다.",
                "responses": {"200": _body(pub.SamplesResponse, ["samples.response.json"])},
            }
        },
    }

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Loop 공개 API — 브라우저 ↔ Spring",
            "version": "0.2.0",
            "description": (
                "KAN-4. 9/7 MVP = Kan-9 v0.2 경로 8필드 고정.\n\n"
                "· 경로·상태 코드·흐름: 노션 「프론트-백엔드 계약 정리」 §4 (권도윤)\n"
                "· JSON 본문: ai-service `explainer/public_api.py` (성종현, 9/3 카톡 분담)\n"
                "· 계산 결과 = Kan-9 §5 (`Calculation`), AI 설명 = Kan-9 §7 + evidence (`Explanation`)\n"
                "· 단위: 금액 KRW 정수 · 기간 개월 · 비율 정수 퍼센트(0~100)\n"
                "· 오류: 모든 4xx/5xx 는 `ErrorEnvelope`. 프론트는 `code`·`retryable` 로만 분기\n\n"
                "이 파일은 scripts/export_openapi.py 가 생성한다. 손으로 고치지 말 것."
            ),
        },
        "servers": [{"url": "/api/v1"}],
        "paths": paths,
        "components": {"schemas": defs["$defs"]},
    }


def build_internal() -> dict:
    """내부 HTTP (KAN-17). FastAPI 앱이 원본. 요청 본문만 dict 라 SimulationInput 스키마를 덧붙인다."""
    from explainer.api import app  # 지연 import — retriever 초기화가 있다

    spec = app.openapi()
    _, defs = models_json_schema([(SimulationInput, "validation"), (Explanation, "validation")], ref_template=REF)
    spec.setdefault("components", {}).setdefault("schemas", {}).update(defs["$defs"])
    body = spec["paths"]["/rag/answer"]["post"]["requestBody"]["content"]["application/json"]
    body["schema"] = _ref(SimulationInput)
    body["examples"] = {"case1_small_gap": {"value": json.loads((ROOT / "fixtures/case1_small_gap.json").read_text(encoding="utf-8"))}}
    spec["info"]["description"] = (
        "Spring ↔ ai-service 내부 계약. 요청 = KAN-11 analyze() 출력 + focus + goal_amount. "
        "처리가 끝나면 항상 200 + status, 입력 불일치만 422. 상세: docs/KAN-17-내부HTTP계약.md"
    )
    return spec


def _dump(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="파일을 쓰지 않고 디스크와 비교. 다르면 1")
    args = ap.parse_args(argv)

    targets = {PUBLIC_PATH: build_public(), INTERNAL_PATH: build_internal()}
    stale = []
    for path, spec in targets.items():
        text = _dump(spec)
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(path.relative_to(ROOT))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            print(f"wrote {path.relative_to(ROOT)}  ({len(spec['paths'])} paths, {len(spec['components']['schemas'])} schemas)")

    if stale:
        print("stale (코드와 다름 — scripts/export_openapi.py 를 다시 돌릴 것):", *map(str, stale), sep="\n  ")
        return 1
    if args.check:
        print("openapi up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
