# docs — ai-service 설계 문서

코드가 구현하는 규격이다. 기준의 층위는 세 겹이다.

```
Jira · Notion   ← 유일한 기준(SSOT). 요구사항·계약·ERD·회의 결정
      ↓
   docs/        ← 내 설계. Jira·Notion에 없는 것(체크리스트·프롬프트·근거)만. 충돌하면 위가 이긴다
      ↓
    코드        ← docs를 따른다. 스펙을 바꿀 땐 docs를 먼저 고치고 코드를 맞춘다
```
파일명의 KAN 번호는 Jira 티켓이고, 팀은 티켓 번호로 대화하므로 유지한다.

| 문서 | 티켓 | 한 줄 | 구현 파일 | 언제 보나 |
|---|---|---|---|---|
| [KAN-04-API-명세.md](KAN-04-API-명세.md) | KAN-4 (JSON 응답 방식 = 성종현, BE↔FE 설계 = 도윤 · 9/3 카톡) | **호출 흐름·계산/설명 분리 근거·공개 API JSON 설계·KAN-9 대조표** | — (Spring은 도윤) | "전체가 어떻게 이어지나", "KAN-9와 맞나" |
| [openapi/](openapi/) | KAN-4 | **공개 API OpenAPI(코드에서 생성) + 내부 HTTP OpenAPI + 예시 JSON 13개** — 도윤에게 주는 전달물 | `public_api.py` `scripts/export_openapi.py` | "응답 JSON의 정확한 모양", "Spring DTO를 뭘 보고 만드나" |
| [KAN-12-AI-응답규격.md](KAN-12-AI-응답규격.md) | KAN-12 | AI 설명의 **입출력 스키마·프롬프트·금지 규칙·검증 체크리스트 C1~C18** | `schema.py` `prompt.py` `guardrail.py` | "AI가 뭘 받고 뭘 내놓나", "guardrail이 뭘 검사하나" |
| [KAN-13-테스트세트.md](KAN-13-테스트세트.md) | KAN-13 | 품질 테스트 **케이스 6종(1~5 실측 픽스처) + 공통 검사 A1~A18 + 승준 검출 T1~T21** | `tests/test_guardrail.py` `fixtures/` | "이 응답이 통과인지 어떻게 판정하나" |
| [KAN-15-지식문서.md](KAN-15-지식문서.md) | KAN-15 | **개념 8개 ↔ 문서 ↔ 출처·라이선스 매핑**, 작성 규칙 | `../knowledge/*.md` | "이 개념의 출처가 뭐냐", "문서를 어떻게 쓰나" |
| [KAN-16-지식저장소.md](KAN-16-지식저장소.md) | KAN-16 | **청킹·임베딩·인덱스 선택 근거**, 검색 결합 | `knowledge/*.py` `db/` `scripts/` | "왜 600자, 왜 HNSW, 청크는 어떻게 고르나" |
| [KAN-17-내부HTTP계약.md](KAN-17-내부HTTP계약.md) | KAN-17 | **`POST /rag/answer` 요청·응답 규약**, 예시 JSON 정상·실패 | `api.py` `client.py` `Dockerfile` | "Spring에서 어떻게 부르나", "status가 뭐가 있나" |

## 이 폴더에 없는 것

| 찾는 것 | 어디 |
|---|---|
| 요구사항·수용 기준·진행 상태 | Jira `KAN-4` `KAN-12` `KAN-13` `KAN-15` `KAN-16` `KAN-17` |
| 시뮬레이터 입출력 계약 (SSOT) | Notion **Kan-9** — 이 문서들은 §5·§7을 따른다 |
| PRD · ERD · 인프라 구조 | Notion 프로젝트x 워크스페이스 |
| 실행 방법 | [../README.md](../README.md) |
| 작업 이력·인수인계 | `내작업/DEVLOG.md` (레포 밖, 개인) |
| RAG 원재료 (개념 문서 8개) | [../knowledge/](../knowledge/) — **문서가 아니라 코드가 읽는 데이터** |

## 문서↔코드 일치 검사

`KAN-12`의 시스템 프롬프트 블록은 `prompt.py`의 `SYSTEM`과 글자 단위로 같아야 한다.
스키마 필드도 `schema.py`와 같아야 한다. 고칠 때 둘 다 고친다.

`openapi/*.json`은 손으로 고치지 않는다 — `scripts/export_openapi.py`가 생성하고, `--check`가 코드와 같은지 본다(`tests/test_public_api.py`가 돌린다). 계약을 바꾸면 `public_api.py` → 스크립트 재실행 → 예시 JSON 순.
