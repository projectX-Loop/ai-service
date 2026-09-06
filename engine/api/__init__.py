"""engine.api — 엔진의 HTTP 층 (FastAPI). 엔드포인트는 POST /calculate 하나.

    python -m uvicorn engine.api.app:app --port 8000      # engine/ 의 부모 디렉터리에서

engine/core 는 표준 라이브러리만 쓴다. fastapi·uvicorn 의존은 이 하위 패키지 안에서 끝난다
(engine/api/requirements.txt). api 를 쓰지 않으면 설치할 필요도 없다.

여기서 app 을 재수출하지 않는다 — 그러면 engine.api.app 이 모듈이 아니라 FastAPI 인스턴스를
가리켜 `from engine.api.app import ...` 가 깨진다.
"""
