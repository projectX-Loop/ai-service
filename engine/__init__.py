"""engine — 승준 KAN-11 리밸런싱 시뮬레이터 v0.3 (계산 엔진). 소유: 이승준.

원본: projectX-Loop/LSJ `KAN-11-시뮬레이터/v0.3/src/core`. 파일 4개:
    engine.py · dataset.py · cashflow.py · errors.py        (표준 라이브러리만, 외부 의존 없음)
스냅샷은 engine/data/ (SNAPSHOT.json · asset_catalog.json · CSV 4개). 편집기로 열지 말 것 — 로드 시 SHA-256 대조.

HTTP 층(POST /calculate · 기동 시 Dataset 로드 · /health data_hash · ValidationError → 422)은 성종현이 explainer/에 붙인다.
엔진 코드 수정은 승준만. 스냅샷 갱신(9/6 동결)도 승준이 파일 교체.
"""

# 패키지 루트 재수출 — api/ 가 레이아웃(평면 vs core/)에 상관없이 `from .. import analyze` 로 쓴다.
# 기존 `from engine.engine import analyze` (explainer/calculate.py) 는 그대로 동작한다.
from .cashflow import build_profile, contribution_path, validate_cashflow
from .dataset import Dataset, DatasetError, add_months, month_index, months_between
from .engine import ASSUMPTIONS_VERSION, analyze
from .errors import ValidationError

__all__ = [
    "ASSUMPTIONS_VERSION", "Dataset", "DatasetError", "ValidationError",
    "analyze", "build_profile", "contribution_path", "validate_cashflow",
    "add_months", "month_index", "months_between",
]
