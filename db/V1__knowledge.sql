-- KAN-16 · knowledge_* 스키마 (ai-service 소유)
-- 진실 출처: 이 파일을 backend Flyway(db/migration/)에 그대로 복사한다. 여기 사본은 로컬 개발용.
-- 대상: PostgreSQL 16 / pgvector 0.5+ (RDS는 rds.allowed_extensions에 vector 포함 필요)

CREATE EXTENSION IF NOT EXISTS vector;

-- 문서 = KAN-15가 수집한 개념 문서 1건. 재적재 판단은 content_hash로.
CREATE TABLE IF NOT EXISTS knowledge_document (
    id            BIGSERIAL PRIMARY KEY,
    source_id     TEXT        NOT NULL UNIQUE,   -- 안정 식별자. 예: concept/max_drawdown
    title         TEXT        NOT NULL,
    source_url    TEXT,                          -- KAN-15 출처 URL
    published_at  DATE,                          -- KAN-15 발행일
    license       TEXT,                          -- 재배포 가능 여부·사유
    concept_tags  TEXT[]      NOT NULL DEFAULT '{}',
    content_hash  TEXT        NOT NULL,          -- sha256(원문). 같으면 재적재 생략
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 청크 = 검색 단위. 문서가 바뀌면 그 문서의 청크를 전부 지우고 다시 넣는다.
CREATE TABLE IF NOT EXISTS knowledge_chunk (
    id            BIGSERIAL PRIMARY KEY,
    document_id   BIGINT      NOT NULL REFERENCES knowledge_document(id) ON DELETE CASCADE,
    chunk_index   INT         NOT NULL,          -- 문서 내 순서. evidence 참조는 source_id#chunk_index
    content       TEXT        NOT NULL,
    embedding     vector(768) NOT NULL,          -- 차원은 EMBEDDING_DIM과 반드시 일치
    concept_tags  TEXT[]      NOT NULL DEFAULT '{}',
    location      TEXT,                          -- 출처 위치. 섹션 제목
    char_count    INT         NOT NULL,
    UNIQUE (document_id, chunk_index)
);

-- 인덱스 선택 근거는 docs/KAN-16-지식저장소.md 참조.
-- 현 규모(수백 청크)에선 전체 스캔이 더 정확하고 충분하나, 확장 대비 HNSW를 둔다.
-- IVFFlat은 소량 데이터에서 recall이 떨어져 배제.
CREATE INDEX IF NOT EXISTS knowledge_chunk_embedding_hnsw
    ON knowledge_chunk USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS knowledge_chunk_tags_gin
    ON knowledge_chunk USING gin (concept_tags);
