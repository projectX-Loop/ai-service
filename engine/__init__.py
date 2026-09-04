"""engine — 승준 KAN-11 리밸런싱 시뮬레이터 v0.3 (계산 엔진). 소유: 이승준.

원본: projectX-Loop/LSJ `KAN-11-시뮬레이터/v0.3/src/core`. 여기엔 파일 4개가 들어온다:
    engine.py · dataset.py · cashflow.py · errors.py        (표준 라이브러리만, 외부 의존 없음)
스냅샷은 engine/data/ (SNAPSHOT.json · asset_catalog.json · CSV 4개). 편집기로 열지 말 것 — 로드 시 SHA-256 대조.

HTTP 층(POST /calculate · 기동 시 Dataset 로드 · /health data_hash · ValidationError → 422)은 성종현이 explainer/에 붙인다.
엔진 코드 수정은 승준만. 스냅샷 갱신(9/6 동결)도 승준이 파일 교체.
"""
