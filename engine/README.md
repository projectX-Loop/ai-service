# engine/ — 계산 엔진 (KAN-11, 이승준)

| 항목 | 내용 |
|---|---|
| 들어온 것 | `engine.py` `dataset.py` `cashflow.py` `errors.py` — LSJ `v0.3/src/core` 그대로 (평면 import → 상대 import 만 변경) |
| 스냅샷 | `data/` — LSJ `v0.3/input/data` 6개 파일. **바이너리 그대로 복사** (해시 검증) · data_version 2026-09-02 · latest_month 2026-07 |
| HTTP | `api/` — FastAPI `POST /calculate` (승준 엔진 자족용, 단독 실행 `python -m uvicorn engine.api.app:app`). 배포는 그대로 `explainer.api:app` — 와이어 계약(200/422/500/503)은 KAN-17 과 동일하게 맞췄다 |
| import | 패키지 안이므로 `from . import cashflow` · `from .dataset import Dataset` · `from .errors import ValidationError, err` |
| 호출 | `from engine.engine import analyze` · `from engine.dataset import Dataset` → `analyze(inputs, dataset=Dataset.load("engine/data"), now=...)` |
| 테스트·골든 | LSJ 레포에 둔다. ai-service는 `fixtures/`에 골든·실험 payload 사본만 |
| 소유 | 엔진·스냅샷 = 승준 / HTTP 층 = 종현 / Spring 클라이언트 = 도윤 |

노션 「프론트-백엔드 계약 정리」 §4: Spring → `POST /calculate`(타임아웃 10초) → ai-service가 M/Q/H 전부 계산해 §5 출력 반환.
