"""engine/api — POST /calculate. 엔진을 HTTP 로 노출하는 최소 어댑터.

계약: KAN-9 §2 입력 dict → analyze() → §5 출력 dict. 가공 없이 그대로 통과시킨다.

검증은 엔진이 한다 — SSOT 는 계약(KAN-9)이고 그 구현이 engine.validate_static + _resolve 다.
여기서 pydantic 으로 다시 타이핑하지 않는다: 엔진은 isinstance(int) 엄격 검사인데 pydantic
기본(lax) 모드는 "50000000" 문자열을 통과시켜 판정이 갈린다
(KAN-10-기술검토/KAN-10-라이브러리-평가-파이프라인적합성.md §5.3 StrictInt 주의).

상태 코드·오류 봉투는 ai-service docs/KAN-17-내부HTTP계약.md 와 같게 맞춘다:
    200  §5 출력. status = "OK" | "NO_PLAN_FUNDS"   (NO_PLAN_FUNDS 는 오류가 아니라 메시지)
    422  {"code": "VALIDATION_ERROR",   "retryable": false, "errors": [{code, field, message}, ...]}
    500  {"code": "CALCULATION_FAILED", "retryable": true,  "message": ...}
    503  {"code": "ENGINE_UNAVAILABLE", "retryable": true,  "message": ...}   스냅샷 부재·해시 불일치

실행: engine/ 의 부모 디렉터리에서
    python -m uvicorn engine.api.app:app --port 8000

CLAUDE.md §2 준수 — 결정론(now 주입·난수 없음)·무네트워크, 요청 본문은 로그에 남기지 않는다.
"""

from __future__ import annotations

import copy
import logging
import os
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .. import ASSUMPTIONS_VERSION, Dataset, DatasetError, ValidationError, analyze

log = logging.getLogger("engine.api")

_PKG = Path(__file__).resolve().parent.parent
# 스냅샷 위치는 배치에 따라 다르다 — 원본 레포는 engine/input/data, 평면 사본은 engine/data.
_CANDIDATES = (_PKG / "input" / "data", _PKG / "data")


def data_dir() -> Path:
    """ENGINE_DATA_DIR 우선(스냅샷 동결·교체용). 없으면 SNAPSHOT.json 이 있는 후보 디렉터리."""
    env = os.getenv("ENGINE_DATA_DIR")
    if env:
        return Path(env)
    for p in _CANDIDATES:
        if (p / "SNAPSHOT.json").is_file():
            return p
    raise DatasetError("스냅샷 디렉터리 없음: " + " · ".join(str(p) for p in _CANDIDATES))


@lru_cache(maxsize=1)
def dataset() -> Dataset:
    """프로세스당 1회. Dataset.load 는 파일 5종 SHA-256 대조 + 전 계열 연속성 검사라 비싸다."""
    return Dataset.load(str(data_dir()))


app = FastAPI(title="project-x engine", version=ASSUMPTIONS_VERSION)


@app.post("/calculate")
def calculate(inputs: dict) -> JSONResponse:
    """KAN-9 §2 입력 dict → §5 출력 dict (M/Q/H 전부, 한 응답)."""
    try:
        ds = dataset()
    except (DatasetError, OSError) as e:
        log.error("snapshot unavailable: %s", e)
        return JSONResponse(status_code=503,
                            content={"code": "ENGINE_UNAVAILABLE", "retryable": True, "message": str(e)})
    try:
        # analyze 가 입력 dict 를 건드릴 수 있다 (tools/demo_run.py:85 와 같은 이유로 복사본을 넘긴다).
        out = analyze(copy.deepcopy(inputs), dataset=ds, now=datetime.now(timezone.utc))
    except ValidationError as e:
        return JSONResponse(status_code=422,
                            content={"code": "VALIDATION_ERROR", "retryable": False, "errors": e.errors})
    except Exception as e:                       # 엔진 결함. 요청 본문은 로그에 남기지 않는다
        log.exception("engine crashed")
        return JSONResponse(status_code=500,
                            content={"code": "CALCULATION_FAILED", "retryable": True, "message": str(e)})
    return JSONResponse(status_code=200, content=out)
