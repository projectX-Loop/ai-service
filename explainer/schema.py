"""KAN-12 입력·출력 스키마.

내작업/KAN-12-AI-응답규격.md 의 JSON 스키마를 Pydantic으로 옮긴 것.
문서와 이 파일이 어긋나면 문서를 먼저 고치고 여기 반영한다.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class Frequency(str, Enum):
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    SEMIANNUAL = "SEMIANNUAL"


class RiskProfile(str, Enum):
    CONSERVATIVE = "CONSERVATIVE"
    MODERATE = "MODERATE"
    AGGRESSIVE = "AGGRESSIVE"


class AdjustableInput(str, Enum):
    """사용자가 조정할 수 있는 입력값. 이 4개가 전부다.

    상품 매수·매도 권유를 프롬프트가 아니라 타입으로 막는 장치다.
    """

    MONTHLY_CONTRIBUTION = "MONTHLY_CONTRIBUTION"
    GOAL_PERIOD = "GOAL_PERIOD"
    GOAL_AMOUNT = "GOAL_AMOUNT"
    REBALANCING_FREQUENCY = "REBALANCING_FREQUENCY"


# ─────────────────────────────────────────── 입력 (시뮬레이터 → AI)


class Lenient(BaseModel):
    """입력 모델 공통 설정.

    모르는 필드는 무시한다. KAN-9 계약이 아직 초안(v0.3)이라 필드가 늘거나
    이름이 바뀔 수 있는데, 그때마다 파싱이 죽으면 아무것도 못 돌린다.
    """

    model_config = ConfigDict(extra="ignore")


class DataPeriod(Lenient):
    start: str
    end: str


class Meta(Lenient):
    data_period: DataPeriod
    assumptions: list[str] = Field(default_factory=list)
    asset_universe: str = ""
    generated_at: str = ""


class UserProfile(Lenient):
    goal_amount: int
    goal_period_months: int
    initial_investment: int
    monthly_contribution: int
    # KAN-9 v0.3은 투자 성향을 입력받지 않고 배분율에서 파생한다. 없어도 돌아가야 한다.
    risk_profile: RiskProfile | None = None


class AssetWeight(Lenient):
    asset: str
    weight: float


class Portfolio(Lenient):
    target_weights: list[AssetWeight] = Field(default_factory=list)


class RiskMetrics(Lenient):
    """KAN-9·KAN-11이 반환하는 위험 지표는 이 둘뿐이다 (샤프비율 없음)."""

    annual_volatility: float
    max_drawdown: float


class SeriesPoint(Lenient):
    date: str
    value: int


class SimulationResult(Lenient):
    frequency: Frequency
    final_value: int
    goal_gap: int
    cumulative_cost: int
    risk_metrics: RiskMetrics
    # KAN-9 결과 목록에 있는 필드
    additional_monthly_required: int | None = None
    # KAN-9·KAN-11 어디에도 없다. 반영 요청 중이라 없어도 돌아가게 둔다.
    expected_months_to_goal: int | None = None
    goal_gap_rate: float | None = None
    trade_count: int | None = None
    value_series: list[SeriesPoint] = Field(default_factory=list)


class SimulationInput(Lenient):
    meta: Meta
    user_profile: UserProfile
    portfolio: Portfolio = Field(default_factory=Portfolio)
    # KAN-9의 rebalancing.focus와 같은 개념. 없으면 C10 검증만 건너뛴다.
    selected_frequency: Frequency | None = None
    results: list[SimulationResult]


# ─────────────────────────────────────────── 출력 (AI → 화면)
#
# 구조화 출력용이라 모든 필드를 필수로 둔다. 기본값을 주면 JSON Schema에서
# required가 빠져 모델이 필드를 통째로 생략할 수 있다.


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(description="사용자에게 보여줄 문장")
    evidence: list[str] = Field(
        description="이 문장에 등장한 모든 수치의 출처. 입력 JSON의 JSON Pointer 경로 "
        "(예: /results/1/goal_gap). 근거를 댈 수 없는 숫자는 문장에서 뺄 것"
    )


class FrequencyComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frequency: Frequency
    observation: str = Field(description="이 주기에서 관찰된 사실. 수치를 인용할 것")
    tradeoff: str = Field(description="그 사실에 따르는 대가. 우열을 판정하지 말 것")
    evidence: list[str] = Field(description="인용한 수치의 JSON Pointer 경로")


class RiskFactor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(description="위험 요인의 이름")
    detail: str = Field(
        description="이 위험이 사용자에게 뜻하는 바. 입력에 있는 값만 인용하고 "
        "직접 환산·계산하지 말 것 (계산하면 C4 환각 탐지에 걸린다)"
    )
    evidence: list[str] = Field(description="인용한 수치의 JSON Pointer 경로")


class NextAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adjustable_input: AdjustableInput = Field(
        description="조정 대상. 상품·종목은 선택지에 없다"
    )
    text: str = Field(description="사용자가 다음에 해볼 수 있는 조정")
    evidence: list[str] = Field(description="인용한 수치의 JSON Pointer 경로")


class DataBasis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period: str = Field(description="사용한 데이터 기준 기간")
    assumptions: list[str] = Field(description="입력 meta.assumptions를 그대로 복사")
    disclaimer: str = Field(description="투자 유의 문구")


class Explanation(BaseModel):
    """AI 설명 응답. KAN-12 출력 스키마."""

    model_config = ConfigDict(extra="forbid")

    highlighted_frequency: Frequency = Field(
        description="입력 selected_frequency를 그대로 되돌려줄 것"
    )
    summary: Claim = Field(description="결과 한 줄 요약")
    goal_gap: Claim = Field(description="목표와의 간극 설명. 부족액과 추가 필요 월 납입액을 함께")
    frequency_comparison: list[FrequencyComparison] = Field(
        description="월·분기·반기 세 주기 전부. 정확히 3개",
        min_length=3,
        max_length=3,
    )
    risk_factors: list[RiskFactor] = Field(description="핵심 위험 요인", min_length=1)
    next_actions: list[NextAction] = Field(description="조정 가능한 다음 행동", min_length=1)
    data_basis: DataBasis
