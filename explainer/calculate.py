"""POST /calculate 어댑터 — 승준 엔진(engine/)을 HTTP 층에 붙인다 (KAN-17 · 노션 §4).

경계
  engine/   승준 소유. analyze(inputs, dataset, now) 하나만 부른다. 여기서 엔진 코드를 고치지 않는다.
  이 파일   종현 소유. 기동 시 Dataset 1회 로드, 예외 → HTTP 상태 매핑, 엔진 부재 시 503.

Dataset은 로드할 때마다 스냅샷 SHA-256을 다시 계산해 대조하므로(승준 dataset.py) 프로세스당 1회만 로드하고 재사용한다.
`now`는 API에서 주입한다 — 엔진은 현재 시각을 읽지 않는다 (노션 KAN-11 유의).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

log = logging.getLogger("ai-service.calculate")

DATA_DIR = Path(__file__).resolve().parent.parent / "engine" / "data"


class EngineUnavailable(RuntimeError):
    """engine/ 에 승준 코드·스냅샷이 없거나 로드에 실패 → 503 ENGINE_UNAVAILABLE."""


class CalculationFailed(RuntimeError):
    """엔진이 ValidationError 아닌 예외로 죽음 → 500 CALCULATION_FAILED (Spring은 502로 변환)."""


@dataclass
class InvalidInputs(Exception):
    """엔진 정적·데이터 의존 검증 실패 → 422. errors = [{code, field, message}] 그대로."""

    errors: list[dict]


def available() -> bool:
    try:
        import engine.engine  # noqa: F401
        import engine.dataset  # noqa: F401
    except Exception:
        return False
    return (DATA_DIR / "SNAPSHOT.json").exists()


@lru_cache(maxsize=1)
def dataset():
    """프로세스당 1회. 실패하면 EngineUnavailable — /calculate 는 503, /health 는 engine=null."""
    if not available():
        raise EngineUnavailable("engine/ 에 엔진 코드 또는 engine/data 스냅샷이 없습니다 (승준 push 대기)")
    try:
        from engine.dataset import Dataset
        return Dataset.load(str(DATA_DIR))
    except Exception as e:  # DatasetError(해시 불일치·결측) 포함
        raise EngineUnavailable(f"스냅샷 로드 실패: {e}") from e


def info() -> dict | None:
    """GET /health 용. 엔진이 없으면 None."""
    try:
        ds = dataset()
        from engine.engine import ASSUMPTIONS_VERSION
    except EngineUnavailable:
        return None
    return {
        "assumptions_version": ASSUMPTIONS_VERSION,
        "data_version": getattr(ds, "data_version", None),
        "data_hash": getattr(ds, "data_hash", None),
        "latest_month": getattr(ds, "latest_month", None),
    }


def run(inputs: dict, *, now: datetime | None = None) -> dict:
    """Kan-9 §2 입력 dict → §5 출력 dict. 검증 실패는 InvalidInputs, 그 외 엔진 예외는 CalculationFailed."""
    ds = dataset()
    from engine.engine import analyze
    from engine.errors import ValidationError
    try:
        return analyze(inputs, dataset=ds, now=now or datetime.now(timezone.utc))
    except ValidationError as e:
        raise InvalidInputs(errors=list(getattr(e, "errors", []) or [{"code": "VALIDATION_ERROR", "field": None, "message": str(e)}]))
    except Exception as e:
        log.exception("engine crashed")
        raise CalculationFailed(str(e)) from e
