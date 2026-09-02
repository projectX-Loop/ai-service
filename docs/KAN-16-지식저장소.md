# KAN-16 — pgvector 지식 저장소 및 임베딩 파이프라인

> 담당 성종현 · 선행 KAN-15, KAN-3 · 후속 KAN-17
> **상태 (2026-09-02)**: 파이프라인 구현 완료. DB·임베딩 실행 검증은 미완 (Docker·키 없음)

## 구조

```
knowledge/*.md            원문 (KAN-15). 프론트매터에 메타데이터
   ↓ chunking.py          섹션 → 문단 그리디 패킹, 겹침 1문단
   ↓ embedding.py         Gemini 임베딩 (RETRIEVAL_DOCUMENT / RETRIEVAL_QUERY)
   ↓ store.py             pgvector 적재·검색
db/V1__knowledge.sql      스키마. 원본은 backend Flyway로 이관
scripts/ingest.py         적재 (재실행 안전)
scripts/search.py         검색 확인 + 샘플 질의 5건 평가
```

## 선택 근거

### 청킹 — 섹션 우선, 600자 상한, 겹침 1문단
KAN-15 문서는 개념 하나당 짧은 설명(정의·왜 보는가·주의)이라 **섹션이 곧 의미 단위**다.
섹션 제목이 그대로 출처 위치(`location`)가 되어 "어느 문서의 어느 절"을 evidence로 남길 수 있다.
600자는 한국어 기준 한 개념의 한 절이 넘지 않는 크기이며, 넘을 때만 문단 단위로 나눈다.
겹침을 직전 문단 하나로 둔 이유는 잘린 경계에서 지시어("이것은…")가 근거를 잃지 않게 하기 위해서다.

### 임베딩 — Gemini 임베딩, 768차원
팀 인프라가 Gemini Flash라 같은 SDK·키를 쓴다. 차원은 pgvector 컬럼 `vector(768)`에 고정된다.
**차원을 바꾸면 스키마 변경 + 전체 재적재**이므로 처음에 정하고 움직이지 않는다.
모델 ID는 `EMBEDDING_MODEL` 환경변수로 덮을 수 있다 (기본 `gemini-embedding-001`, 실호출 검증 전).

### 인덱스 — HNSW (cosine)
현 규모(개념 8~30개, 청크 수십~수백)에서는 **인덱스 없는 전체 스캔이 정확하고 충분히 빠르다.**
그럼에도 HNSW를 두는 이유는 확장 대비이며, **IVFFlat을 배제한 이유**는 소량 데이터에서
클러스터 수 대비 행이 부족해 recall이 떨어지기 때문이다. HNSW는 소량에서도 정확도 손실이 없다.

### 중복 방지 — content_hash
`knowledge_document.content_hash`(sha256 원문)가 같으면 청크를 건드리지 않는다.
바뀌었으면 그 문서의 청크를 전부 지우고 다시 넣는다. 그래서 **재실행 시 청크 수 불변**이 보장된다.

### evidence 참조 형식 — `chunk:<source_id>#<chunk_index>`
BIGSERIAL id는 재적재하면 바뀐다. `source_id#index`는 안정적이라 AI 응답의 evidence에 이걸 쓴다.
guardrail C3 확장은 `store.chunk_exists(ref)`로 실존을 확인한다.

### 검색 경로 두 가지를 함수 하나로
- **질의 검색** (KAN-16 문구): 질의 임베딩 → 코사인 top-k
- **태그 필터** (KAN-17 설계): 시뮬레이터 결과에 `max_drawdown`이 있으면 `tags=["max_drawdown"]`로 좁혀 검색
`store.search(..., tags=...)` 하나로 둘 다 된다. 실제 설명 생성은 태그 경로가 기본이다 —
어떤 청크가 들어갈지 예측 가능해 테스트가 쉽다.

## 수용 기준 대조

| 수용 기준 | 대응 | 검증 |
|---|---|---|
| 전체 적재, 재실행 시 청크 수 불변 | `content_hash` 비교 | `tests/test_knowledge.py` (가짜 임베더·인메모리) — **DB 실행은 미검증** |
| 샘플 질의 5건 top-3에 정답 | `scripts/search.py --eval` | **미검증** (키·DB 필요) |
| 검색 결과에 출처 동반 | `SearchHit.title/source_url/location` | 코드 확인 |
| 설정 전부 환경변수, 비밀값 없음 | `DATABASE_URL`, `GEMINI_API_KEY`, `EMBEDDING_*` | 확인 |

## 실행

```bash
# 로컬 Postgres (docker compose)
docker compose -f docker-compose.ai-service.yml up -d db
psql "$DATABASE_URL" -f db/V1__knowledge.sql

# 적재 · 검색
python scripts/ingest.py --dry-run     # 청킹만 확인 (키·DB 불필요)
python scripts/ingest.py --fake        # 가짜 임베더로 DB 적재 구조 검증
python scripts/ingest.py               # 실제
python scripts/search.py --eval
```

## 확정 대기
| 항목 | 담당 |
|---|---|
| DDL을 backend Flyway에 이관 (`V{n}__knowledge.sql`) | 권도윤 |
| RDS에서 `CREATE EXTENSION vector` 가능 여부 | 권도윤 |
| 임베딩 모델 ID 실호출 확인 | 성종현 (키 수령 후) |
| 샘플 질의 5건 정답셋은 KAN-15 문서 확정 후 재조정 | 성종현 |
