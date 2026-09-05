"""공개 API(브라우저 ↔ Spring)의 JSON 계약 — KAN-4.

9/3 카톡 분담: **JSON 응답 방식은 성종현이 정해서 도윤에게 전달**, Spring·프론트 설계·구현은 도윤.
이 모듈이 그 전달물의 원본이다. 실행 코드가 아니다 — ai-service는 이 엔드포인트를 서빙하지 않는다.

역할
  1. Spring DTO의 원본. `scripts/export_openapi.py` 가 여기서 OpenAPI(docs/openapi/)를 뽑는다.
  2. `docs/openapi/examples/*.json` 이 계약과 맞는지 `tests/test_public_api.py` 가 검증한다.
  3. 계산 결과(KAN-9 §5)·AI 설명(KAN-9 §7)은 `schema.py` 모델을 그대로 재사용한다 — 어휘가 갈라지지 않게.

기준
  · 경로·상태 코드·흐름(HTTP 층): 노션 「프론트-백엔드 계약 정리」 §4 (도윤, 9/3 14:30)
  · 입력 8필드·검증 코드: 노션 「Kan-9」 §2 (v0.2 고정). 여기의 범위 제약은 Kan-9 정적 검증의 사본이며 기준은 Kan-9
  · 계산 출력: Kan-9 §5 = `schema.Meta/Derived/PeriodResult`
  · AI 설명: Kan-9 §7 + evidence = `schema.Explanation`
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .schema import Claim, Derived, Explanation, Meta, Period, PeriodResult

# ─────────────────────────────────────────── 입력 (Kan-9 §2, v0.2 8필드)
#
# extra="forbid" — v0.3 필드(goal.target_month, goal.type, funds.composition, cashflow, options)는
# 9/7 API가 받지 않는다. Spring은 이 경우 400 UNSUPPORTED_FIELD 로 거부한다 (노션 §0).


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


CATALOG = ("KR_EQ", "US_EQ", "KR_BOND", "US_EQ_KR")
"""Kan-9 자산 카탈로그 코드. base 3종 + optional US_EQ_KR. 기준은 노션 Kan-9 · 런타임 목록은 GET /universe.
KAN-13 케이스 6-e(유니버스 밖 자산)가 KAN-4 API 검증으로 이관됨 (도윤 9/3 14:18) — 여기서 잡는다."""


class Goal(Strict):
    amount: int = Field(ge=1_000_000, le=10_000_000_000, description="목표 금액 KRW. 범위 밖 → GOAL_AMOUNT_RANGE")
    horizon_months: int = Field(ge=12, le=120, description="목표 기간(개월). 범위 밖 → GOAL_HORIZON_RANGE")


class Funds(Strict):
    initial: int = Field(ge=0, description="초기 투자금 KRW. 음수 → FUNDS_INITIAL_RANGE")
    monthly: int = Field(ge=0, description="월 납입액 KRW (상수). 음수 → FUNDS_MONTHLY_RANGE. initial·monthly 동시 0 → NO_FUNDS")

    @model_validator(mode="after")
    def _no_funds(self) -> "Funds":
        if self.initial == 0 and self.monthly == 0:
            raise ValueError("NO_FUNDS: 초기 투자금과 월 납입액이 모두 0")
        return self


class AllocSplit(Strict):
    """자금 배분율. 정수 퍼센트, 합 100. '투자 성향' 입력은 없고 이 배분율에서 파생된다 (Kan-9 확정 ⑪)."""

    invest: int = Field(ge=0, le=100)
    safe: int = Field(ge=0, le=100)
    other: int = Field(ge=0, le=100)

    @property
    def total(self) -> int:
        return self.invest + self.safe + self.other


class Alloc(Strict):
    initial: AllocSplit = Field(description="합 100 아니면 ALLOC_SUM_INITIAL")
    monthly: AllocSplit = Field(description="합 100 아니면 ALLOC_SUM_MONTHLY")

    @model_validator(mode="after")
    def _sums(self) -> "Alloc":
        if self.initial.total != 100:
            raise ValueError("ALLOC_SUM_INITIAL: alloc.initial 합이 100이 아님")
        if self.monthly.total != 100:
            raise ValueError("ALLOC_SUM_MONTHLY: alloc.monthly 합이 100이 아님")
        return self


class AssetWeight(Strict):
    code: str = Field(description="Kan-9 카탈로그 코드 (KR_EQ · US_EQ · KR_BOND · US_EQ_KR). 밖이면 ASSET_NOT_IN_CATALOG")
    weight: int = Field(ge=1, le=100, description="정수 퍼센트. 0 < w ≤ 100 아니면 PORTFOLIO_WEIGHT_RANGE")


class Portfolio(Strict):
    assets: list[AssetWeight] = Field(min_length=1, max_length=3, description="1~3개, weight 합 100 (WEIGHTS_SUM), code 중복 불가 (PORTFOLIO_ASSET_DUP)")

    @model_validator(mode="after")
    def _weights(self) -> "Portfolio":
        if sum(a.weight for a in self.assets) != 100:
            raise ValueError("WEIGHTS_SUM: portfolio.assets weight 합이 100이 아님")
        codes = [a.code for a in self.assets]
        if len(codes) != len(set(codes)):
            raise ValueError("PORTFOLIO_ASSET_DUP: 같은 자산 코드가 두 번")
        unknown = [c for c in codes if c not in CATALOG]
        if unknown:
            raise ValueError(f"ASSET_NOT_IN_CATALOG: {unknown} — Kan-9 카탈로그 {list(CATALOG)} 밖")
        return self


class Rebalancing(Strict):
    focus: Period = Field(description="강조 주기 M/Q/H. 필수 — FOCUS_INVALID. 계산은 세 주기 전부, 화면·AI에서 강조")


class PlanInputs(Strict):
    """POST /plans 요청 본문 = Kan-9 §2 입력 dict 그대로. Spring은 변환 없이 ai-service POST /calculate 에 넘긴다."""

    goal: Goal
    funds: Funds
    alloc: Alloc
    portfolio: Portfolio
    rebalancing: Rebalancing


# ─────────────────────────────────────────── plan + 계산 결과


class Plan(BaseModel):
    """저장된 입력 + 식별자. 백엔드가 채운다. 결과는 저장하지 않으므로 재조회 = 재계산."""

    model_config = ConfigDict(extra="forbid")

    public_id: str = Field(description="UUID. 조건 수정은 새 plan 생성(9/7 PUT 없음)")
    data_snapshot_id: int = Field(description="생성 시점 data_snapshot(is_current)")
    created_at: str = Field(description="ISO 8601")
    inputs: PlanInputs


class Calculation(BaseModel):
    """ai-service POST /calculate 응답 = KAN-11 analyze() 출력 그대로 (Kan-9 §5). 백엔드는 가공·저장하지 않는다.

    `schema.SimulationInput` 과 같은 모양에서 `focus`·`goal_amount` 만 없다 — 그 둘은 Spring이
    POST /rag/answer 를 부를 때 덧붙이는 값이라 공개 응답에는 나오지 않는다.
    """

    model_config = ConfigDict(extra="ignore")   # v0.3 필드(cashflow 등)가 붙어도 통과. 프론트는 무시

    status: str = "OK"
    meta: Meta
    derived: Derived = Field(default_factory=Derived)
    per_period: dict[Period, PeriodResult]


class PlanResponse(BaseModel):
    """POST /plans 201 · GET /plans/{public_id} 200 공통 본문."""

    model_config = ConfigDict(extra="forbid")

    plan: Plan
    calculation: Calculation


# ─────────────────────────────────────────── AI 설명


class ExplanationStatus(str, Enum):
    OK = "OK"
    EXPLANATION_REJECTED = "EXPLANATION_REJECTED"       # 가드레일 2회 실패. 결과 화면 유지, 설명 영역만 message
    EXPLANATION_UNAVAILABLE = "EXPLANATION_UNAVAILABLE" # 모델 호출 실패. 재시도 버튼


class ExplanationResponse(BaseModel):
    """POST /plans/{public_id}/explanation 200 본문. 항상 200이고 성패는 `status` (KAN-17 규약 그대로).

    ai-service 응답에서 `explanation`·`status`·`message` 만 노출한다. `attempts`·`violations`·`retrieved_refs` 는
    디버깅·저장용(agent_message)이라 브라우저에 주지 않는다.
    """

    model_config = ConfigDict(extra="forbid")

    status: ExplanationStatus
    explanation: Explanation | None = Field(description="status=OK 일 때만 non-null")
    message: str | None = Field(default=None, description="status≠OK 일 때 설명 영역에 보여줄 문구")


# ─────────────────────────────────────────── 질문 답변 (KAN-24 — 9/5 도윤 구두 확인)


class QuestionRequest(Strict):
    """POST /plans/{public_id}/questions 요청 본문. 이력 없음 — 매 호출이 독립(대화 저장 안 함)."""

    question: str = Field(min_length=1, max_length=500, description="자유 질문 텍스트")


class QuestionStatus(str, Enum):
    OK = "OK"
    ANSWER_REJECTED = "ANSWER_REJECTED"           # 가드레일 2회 실패. message만
    ANSWER_UNAVAILABLE = "ANSWER_UNAVAILABLE"     # 모델 호출 실패. 재시도 버튼


class QuestionResponse(BaseModel):
    """POST /plans/{public_id}/questions 200 본문. ExplanationResponse와 같은 모양(status로 분기).

    ai-service POST /rag/ask 의 answer(schema.Claim) 그대로 노출. attempts·retrieved_refs·violations는
    디버깅·저장용(agent_message, KAN-24)이라 브라우저에 주지 않는다 — ExplanationResponse와 동일한 원칙.
    """

    model_config = ConfigDict(extra="forbid")

    status: QuestionStatus
    answer: Claim | None = Field(description="status=OK 일 때만 non-null")
    message: str | None = Field(default=None, description="status≠OK 일 때 답변 영역에 보여줄 문구")


# ─────────────────────────────────────────── 유니버스 · 샘플


class UniverseSnapshot(BaseModel):
    """입력 폼에 노출할 데이터 기준. 필드명은 Kan-9 §5 `meta` 와 같게 — 프론트가 어휘 하나만 알면 되게."""

    model_config = ConfigDict(extra="forbid")

    data_version: str
    data_hash: str
    window: dict = Field(description="{start, end, months} — Kan-9 §5 meta.window 과 동일")
    safe_rate_annual_pct: float = Field(description="안전 버킷 예금금리 (연 %)")


class UniverseAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(description="Kan-9 카탈로그 코드. portfolio.assets[].code 에 그대로 넣는다")
    display_name: str
    instrument: str = Field(description="종목코드 (069500 등)")
    group: str = Field(description="base (KR_EQ·US_EQ·KR_BOND) | optional (US_EQ_KR)")
    tax_class: str = ""


class UniverseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot: UniverseSnapshot
    assets: list[UniverseAsset]


class Sample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="P0 … (KAN-14 페르소나)")
    label: str
    inputs: PlanInputs


class SamplesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    samples: list[Sample] = Field(min_length=1)


# ─────────────────────────────────────────── 오류 봉투


class ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(description="Kan-9 정적 검증 코드 (GOAL_AMOUNT_RANGE, WEIGHTS_SUM, …)")
    field: str | None = Field(default=None, description="점 표기 경로 (portfolio.assets)")
    message: str


class ErrorEnvelope(BaseModel):
    """모든 4xx/5xx 의 본문. 프론트는 `code` 와 `retryable` 로만 분기한다.

    입력 검증은 Kan-9 정적 오류 여러 개를 한 번에 돌려준다 — 봉투 `code=VALIDATION_ERROR`, 낱개는 `errors[]`
    (엔진 `ValidationError.errors` 와 같은 모양). 봉투를 유지하는 이유: 오류 응답의 최상위 모양이 항상 같아야
    프론트가 배열/객체를 분기하지 않는다.
    """

    model_config = ConfigDict(extra="forbid")

    code: str = Field(
        description="HTTP 층: VALIDATION_ERROR · UNSUPPORTED_FIELD · PLAN_NOT_FOUND · CALCULATION_FAILED · "
        "EXPLANATION_UNAVAILABLE · ANSWER_UNAVAILABLE · SNAPSHOT_MISMATCH. 데이터 의존 오류는 엔진 코드 그대로: INSUFFICIENT_HISTORY · ASSET_NOT_IN_CATALOG"
    )
    message: str = Field(description="사용자에게 보여도 되는 문구")
    retryable: bool = Field(description="true = 재시도 버튼, false = 입력 수정 요구")
    field: str | None = Field(default=None, description="단일 필드 오류일 때 (UNSUPPORTED_FIELD 등)")
    errors: list[ErrorDetail] = Field(default_factory=list, description="VALIDATION_ERROR 일 때 낱개 목록")
    public_id: str | None = Field(default=None, description="CALCULATION_FAILED: plan은 저장됐으므로 재시도 = GET /plans/{public_id}")
    max_months: int | None = Field(default=None, description="INSUFFICIENT_HISTORY 일 때 가능한 최대 기간")


HTTP_ERROR_CODES = {
    "VALIDATION_ERROR": (400, False),
    "UNSUPPORTED_FIELD": (400, False),
    "INSUFFICIENT_HISTORY": (400, False),
    "ASSET_NOT_IN_CATALOG": (400, False),
    "PLAN_NOT_FOUND": (404, False),
    "SNAPSHOT_MISMATCH": (500, False),
    "CALCULATION_FAILED": (502, True),
    "EXPLANATION_UNAVAILABLE": (502, True),
    "ANSWER_UNAVAILABLE": (502, True),
}
"""코드 → (HTTP, retryable). 노션 §4 오류 봉투 절의 표. 예시 JSON 과 OpenAPI 가 이 표를 따른다."""
