"""KAN-12 입력·출력 스키마 — KAN-9 §5(시뮬레이터 출력) · §7(AI 설명 계약) 정렬본.

2026-09-02 밤 재작성. 이전 스키마는 KAN-9 확정 전 추정 모양이었다.
입력 = KAN-11 analyze() 출력 JSON 그대로 (+ focus). 출력 = KAN-9 §7 필드 + evidence.
docs/KAN-12-AI-응답규격.md 와 어긋나면 문서를 먼저 고치고 여기 반영한다.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class Period(str, Enum):
    """리밸런싱 주기. KAN-9 표기 M / Q / H."""

    M = "M"
    Q = "Q"
    H = "H"


PERIOD_LABEL = {"M": "월", "Q": "분기", "H": "반기"}


class AdjustableInput(str, Enum):
    """사용자가 입력으로 조정할 수 있는 것 (KAN-9 §2 입력 필드). 이것만 다음 행동이 될 수 있다.

    포트폴리오 비중 조정은 뺀다 — 권유하는 순간 투자자문이 된다.
    """

    MONTHLY_CONTRIBUTION = "MONTHLY_CONTRIBUTION"   # funds.monthly
    GOAL_HORIZON = "GOAL_HORIZON"                   # goal.horizon_months
    GOAL_AMOUNT = "GOAL_AMOUNT"                     # goal.amount
    ALLOC_MONTHLY = "ALLOC_MONTHLY"                 # alloc.monthly (투자/안전/기타)
    ALLOC_INITIAL = "ALLOC_INITIAL"                 # alloc.initial
    REBALANCING_FOCUS = "REBALANCING_FOCUS"         # rebalancing.focus


# ─────────────────────────────────────────── 입력 (KAN-11 출력 = KAN-9 §5)


class Lenient(BaseModel):
    """모르는 필드는 무시한다. v0.3 옵션 필드(cashflow·tax 등)가 붙어도 파싱이 죽지 않게."""

    model_config = ConfigDict(extra="ignore")


class Window(Lenient):
    start: str            # "2021-08"
    end: str              # "2026-07"
    months: int


class AssetUsed(Lenient):
    code: str             # KR_EQ / US_EQ / KR_BOND …
    display_name: str = ""    # AI는 자산군("국내 주식")이 아니라 이 상품명으로 부른다 (승준 KAN-12 변경점 3)
    instrument: str = ""
    tax_class: str = ""       # domestic_equity | domestic_listed_other | foreign_listed


class Meta(Lenient):
    assumptions_version: str = ""
    data_version: str = ""
    data_hash: str = ""
    window: Window
    assets_used: list[AssetUsed] = Field(default_factory=list)
    data_basis: str = ""                  # 가정 요약 문장. 화면·AI assumptions_note 원천
    generated_at: str = ""
    safe_rate_annual_pct: float | None = None
    warnings: list[str] = Field(default_factory=list)
    # v0.3 추가 (승준 골든 실측). assumptions_note 분기 근거
    start_month: str = ""
    target_month: str = ""
    cashflow_source: str = ""             # none | summary | profile
    series_used: list[str] = Field(default_factory=list)
    options: dict | None = None           # growth_mode · safe_rate_mode · lot_rounding · account


class Derived(Lenient):
    propensity_label: str = ""            # 안정형 / 중립형 / 공격형 (배분율에서 파생, KAN-9 확정 ⑨)
    invest_share_overall_pct: float | None = None
    plan_excluded_amount: int | None = None


class Risk(Lenient):
    mdd_pct: float                        # 양수 %. 7.63 = 7.63%
    vol_annual_pct: float
    worst_month_pct: float | None = None
    max_drift_pct: float | None = None


class Gap(Lenient):
    fv_total: int                         # 만기 총자산
    shortfall: int                        # goal − FV. 양수 부족, 음수 잉여
    extra_monthly_required: int | None = None   # ΔM. null = 산출 불가
    months_extension: int | None = None         # n′. 재제출 가능한 값일 때만, 아니면 null
    # ── 2026-09-03 승준 엔진 변경 A (도윤 노션 계약 정리 §0 "반영 ①"). null 하나로 뭉개지던 연장 사유를 분기한다
    months_extension_raw: int | None = None     # 자르기 전 n′−n. 참고 정보 — 이 값으로 재제출하면 GOAL_HORIZON_RANGE
    extension_status: str | None = None         # OK | BEYOND_INPUT_LIMIT | BEYOND_DATA_WINDOW | SERIES_NOT_AVAILABLE
    extra_monthly_ratio: float | None = None    # ΔM ÷ cashflow.surplus_headroom. 상수 경로(v0.2)는 null
    status: str | None = None                   # already_met | exact | short | unreachable
    basis: str | None = None                    # pre_tax | after_tax
    delta_m_model: str | None = None            # lot_rounding 시 "continuous"


class TrajectoryPoint(Lenient):
    month: int
    invest: int = 0
    safe: int = 0
    total: int


class Tax(Lenient):
    realized_cum: int | None = None
    fv_after_tax: int | None = None


class PeriodResult(Lenient):
    trajectory: list[TrajectoryPoint] = Field(default_factory=list)
    cum_cost: int
    risk: Risk
    gap: Gap
    tax: Tax | None = None                # options.account 켜졌을 때만


class Cashflow(Lenient):
    """§5 cashflow 블록 (v0.3). `profile`(12개월 소득·지출 원자료)은 **일부러 받지 않는다** —
    승준 KAN-12 변경점 2 "원자료 인용 금지". 스키마에 없으면 프롬프트에도 안 들어가고 C3가 인용을 반려한다."""

    monthly_contribution: list[int] = Field(default_factory=list)   # 월별 실제 납입 M_t
    surplus_rate_pct: float | None = None
    bonus_share_pct: float | None = None          # 30% 이상이면 "상여월 시장 상황에 민감" 서술 허용
    months_zero: int | None = None                # 납입 0인 달 수 (비상자금 충당기)
    emergency_filled_month: int | None = None
    growth_effect_pct: float | None = None
    surplus_headroom: int | None = None           # ΔM 실행 가능성의 분모. 상수 경로는 null


class SimulationInput(Lenient):
    """POST /rag/answer 요청 본문 = KAN-11 analyze() 출력 + focus.

    focus는 KAN-9 입력(rebalancing.focus)이지만 §5 출력에는 에코되지 않는다.
    PRD 수용기준 4("선택된 주기 언급")를 위해 backend가 함께 넘긴다 — KAN-9 반영 요청 항목.
    """

    status: str = "OK"
    meta: Meta
    derived: Derived = Field(default_factory=Derived)
    per_period: dict[Period, PeriodResult]
    cashflow: Cashflow | None = None       # v0.2 경로(9/7)는 profile 없는 상수 경로 → 파생값 대부분 null
    # 아래 둘은 KAN-9 §5 출력에 없다. backend가 요청 시 함께 넘긴다 (KAN-9 반영 요청).
    focus: Period | None = None            # rebalancing.focus — PRD 수용기준 4
    goal_amount: int | None = None         # goal.amount — "목표 5,000만원"을 말할 근거. 없으면 AI가 목표액을 언급 못 함


# ─────────────────────────────────────────── 출력 (AI → 화면 = KAN-9 §7 + evidence)
#
# 구조화 출력용이라 모든 필드를 필수로 둔다. 기본값을 주면 JSON Schema에서
# required가 빠져 모델이 필드를 통째로 생략할 수 있다.


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(description="사용자에게 보여줄 문장")
    evidence: list[str] = Field(
        description="이 문장에 등장한 모든 수치의 출처. 입력 JSON의 JSON Pointer "
        "(예: /per_period/Q/gap/shortfall) 또는 지식 청크 참조(chunk:<source_id>#<idx>). "
        "근거를 댈 수 없는 숫자는 문장에서 뺄 것"
    )


class ProsCons(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pros: list[Claim] = Field(description="이 주기의 장점. 비용·이탈·MDD 축으로 수치 인용", min_length=1)
    cons: list[Claim] = Field(description="이 주기의 단점. 우열 판정 금지", min_length=1)


class RiskClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(description="위험 요인 이름")
    detail: str = Field(
        description="이 위험이 뜻하는 바. 입력에 있는 값만 인용하고 직접 환산·계산하지 말 것"
    )
    evidence: list[str]


class NextAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adjustable_input: AdjustableInput = Field(description="조정 대상. 상품·종목·비중은 선택지에 없다")
    text: str
    evidence: list[str]


class Explanation(BaseModel):
    """AI 설명 응답. KAN-9 §7 필드 + evidence(KAN-12 추가) + highlighted_period(PRD 수용기준 4)."""

    model_config = ConfigDict(extra="forbid")

    summary: Claim = Field(description="2~3문장. 목표 간극과 전체 상황. 기준 구간(meta.window)을 반드시 언급")
    per_period_pros_cons: dict[Period, ProsCons] = Field(
        description="M·Q·H 세 주기 전부. 각 장점≥1·단점≥1"
    )
    risks: list[RiskClaim] = Field(description="≥1. risk 지표 인용, MDD 1순위", min_length=1)
    next_actions: list[NextAction] = Field(description="≥1. 입력으로 조정 가능한 행동만", min_length=1)
    assumptions_note: Claim = Field(description="기준 구간 + 환노출 + meta.data_basis 명시")
    highlighted_period: Period | None = Field(
        description="입력 focus를 그대로 에코. focus가 없으면 null"
    )
    retrieved_refs: list[str] = Field(
        description="인용한 지식 청크 참조의 합집합. evidence의 chunk: 접두어를 뺀 <source_id>#<idx> 형식 (예: concept/max_drawdown#0). RAG 미사용 시 빈 배열"
    )
