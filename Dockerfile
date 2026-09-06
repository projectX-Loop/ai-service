# ai-service — Spring이 내부 HTTP로 호출하는 AI 설명 서비스 (KAN-17)
# 승준 엔진(engine/) 기준 Python 3.14. 로컬 venv와 동일 (FROM 줄에 주석 금지 — 9/4 빌드 실패로 발견)
FROM python:3.14-slim

WORKDIR /app

# 의존성 먼저 — 코드만 바뀔 때 레이어 캐시를 살린다
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY explainer/ ./explainer/
COPY engine/ ./engine/
COPY knowledge/ ./knowledge/
COPY run.py .
COPY fixtures/ ./fixtures/

# 비밀값은 이미지에 넣지 않는다. 전부 환경변수로 주입 (.env / SSM Parameter Store)
ENV PYTHONUNBUFFERED=1 \
    PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"

CMD ["uvicorn", "explainer.api:app", "--host", "0.0.0.0", "--port", "8000"]
