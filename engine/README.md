# engine/ — 계산 엔진 (KAN-11, 이승준)

| 항목 | 내용 |
|---|---|
| 들어올 것 | `engine.py` `dataset.py` `cashflow.py` `errors.py` — LSJ `v0.3/src/core` 그대로 |
| 스냅샷 | `data/` — LSJ `v0.3/input/data` 6개 파일. **바이너리 그대로 복사** (해시 검증) |
| import | 패키지 안이므로 `from . import cashflow` · `from .dataset import Dataset` · `from .errors import ValidationError, err` |
| 호출 | `from engine.engine import analyze` · `from engine.dataset import Dataset` → `analyze(inputs, dataset=Dataset.load("engine/data"), now=...)` |
| 테스트·골든 | LSJ 레포에 둔다. ai-service는 `fixtures/`에 골든·실험 payload 사본만 |
| 소유 | 엔진·스냅샷 = 승준 / HTTP 층 = 종현 / Spring 클라이언트 = 도윤 |

노션 「프론트-백엔드 계약 정리」 §4: Spring → `POST /calculate`(타임아웃 10초) → ai-service가 M/Q/H 전부 계산해 §5 출력 반환.
