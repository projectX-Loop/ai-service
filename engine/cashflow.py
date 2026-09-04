"""
cashflow.py — 현금흐름 계층 (계약 v0.3 §3). 엔진 앞단의 **순수 함수**.

입력(cashflow 블록 §2.2 C, 묶인 자금 해제 이벤트) + 스냅샷(2층 replay 시 임금지수·CPI) → 월별 납입 경로 M_t (t=1..n)

    Y_m = regular + Σ bonus[m]           C_m = fixed + variable × mult[m]         D_m = debt.monthly_payment
    F_m = Y_m − C_m − D_m  ≥ 0 (∀m, 위반 시 CASHFLOW_DEFICIT_MONTHS)
    E_gap_0 = max(0, target_months × C_avg − current),  E_t = min(F_t, E_gap_{t−1})
    C_avg = (1/12) Σ C_m + min(months_remaining, 12)/12 × D    (§3.3 개정 ⑥)
    M_t = F_t − E_t + L_t

정수화: Y·C·D·E·M 은 각 단계에서 원 미만 절사(floor). 난수·네트워크 없음.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .dataset import add_months, month_of
from .errors import ValidationError, err


def _obj(v):
    """블록을 dict 로 정규화 — 실험 X18x: truthy 비-dict 가 .get() 에서 AttributeError 를 낸다."""
    return v if isinstance(v, dict) else {}

INCOME_TYPES = ("employee", "self_employed", "other")
DEFAULT_EMERGENCY_MONTHS = {"employee": 3, "self_employed": 6, "other": 6}
GROWTH_MODES = ("flat", "replay")
MODES = ("summary", "profile")
MAX_BONUS = 4
MAX_VARIABLE_MULT = 3.0
MAX_EMERGENCY_MONTHS = 12


def _nonneg_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool) and x >= 0


def _is_num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


@dataclass
class Profile:
    """달력 월 1..12 프로파일 (인덱스 0 = 1월)."""
    income: list                 # Y_m (세후)
    expense: list                # C_m
    debt_monthly: int            # D
    months_remaining: int | None
    bonus_by_month: dict         # {m: amount} (summary 모드만, profile 모드는 {})
    emergency_current: int
    emergency_target_months: int
    growth_mode: str
    mode: str

    @property
    def living_cost_avg(self) -> float:
        """C_avg = (1/12) Σ C_m + min(months_remaining, 12)/12 × D — 월 평균 생활비(지출 + 부채 상환).

        계약 §3.3 개정 ⑥ B안(2026-09-04). 이전 문면은 `D_m = debt.monthly_payment`(§3.1)에
        m 의존이 없어 12번 더한 뒤 12로 나누면 D 가 통째로 남았다 — 잔여 1개월과 12개월이
        같은 C_avg 를 받았다. 부채 항만 잔여 개월에 비례시켜 문턱을 계단으로 편다.
        months_remaining 이 null(무기한)이거나 12 이상이면 종전과 동일(k = 12).
        """
        mr = self.months_remaining
        k = 12 if mr is None else min(max(int(mr), 0), 12)
        return sum(self.expense) / 12.0 + (k / 12.0) * self.debt_monthly


@dataclass
class CashflowResult:
    mode: str                                   # "summary" | "profile" | "none"
    contributions: list                         # M_t (t=1..n)
    income: list = field(default_factory=list)  # Y_t
    expense: list = field(default_factory=list)  # C_t
    debt: list = field(default_factory=list)    # D_t
    surplus: list = field(default_factory=list)  # F_t
    emergency: list = field(default_factory=list)  # E_t
    locked: list = field(default_factory=list)  # L_t
    profile_income: list | None = None
    profile_expense: list | None = None
    debt_monthly: int | None = None
    bonus_by_month: dict = field(default_factory=dict)
    surplus_rate_pct: float | None = None
    bonus_share_pct: float | None = None
    months_zero: int = 0
    emergency_filled_month: int | None = 0
    growth_effect_pct: float | None = None

    @property
    def total(self) -> int:
        return sum(self.contributions)

    def to_output(self) -> dict:
        return {
            "profile": (None if self.profile_income is None else
                        {"income": list(self.profile_income), "expense": list(self.profile_expense), "debt": self.debt_monthly}),
            "monthly_contribution": list(self.contributions),
            "surplus_rate_pct": self.surplus_rate_pct,
            "bonus_share_pct": self.bonus_share_pct,
            "months_zero": self.months_zero,
            "emergency_filled_month": self.emergency_filled_month,
            "growth_effect_pct": self.growth_effect_pct,
        }


# ---------------------------------------------------------------------------
# 정적 검증 (계약 §2.2 C · §2.3)
# ---------------------------------------------------------------------------

def validate_cashflow(cf) -> list:
    """구조·범위 검증 + 12개월 프로파일 적자월 검증. 오류 목록 반환(비어 있으면 통과)."""
    errs = []
    if not isinstance(cf, dict):
        return [err("CASHFLOW_FIELD_RANGE", "cashflow", "cashflow는 객체여야 함")]
    mode = cf.get("mode", "summary")
    if mode not in MODES:
        errs.append(err("CASHFLOW_FIELD_RANGE", "cashflow.mode", "mode는 summary | profile"))
        return errs

    income = _obj(cf.get("income"))
    expense = _obj(cf.get("expense"))
    debt = _obj(cf.get("debt"))
    ef = _obj(cf.get("emergency_fund"))
    for k in ("income", "expense", "debt", "emergency_fund", "profile"):   # 실험 X18x: dict 아닌 블록을 조용히 무시하지 않는다
        if cf.get(k) is not None and not isinstance(cf.get(k), dict):
            errs.append(err("CASHFLOW_FIELD_RANGE", f"cashflow.{k}", f"{k}는 객체"))

    basis = income.get("basis", "net")
    if basis != "net":
        errs.append(err("INCOME_BASIS_INVALID", "cashflow.income.basis",
                        "엔진은 세후 실수령(net)만 받음 — 세전 입력은 UI에서 요율표로 환산해 제출"))
    itype = income.get("type", "employee")
    if itype not in INCOME_TYPES:
        errs.append(err("CASHFLOW_FIELD_RANGE", "cashflow.income.type", "type은 employee | self_employed | other"))

    if mode == "summary":
        if not _nonneg_int(income.get("regular_monthly")):
            errs.append(err("CASHFLOW_FIELD_RANGE", "cashflow.income.regular_monthly", "세후 정액 실수령은 0 이상의 정수"))
        bonus = income.get("bonus") or []
        if not isinstance(bonus, list) or len(bonus) > MAX_BONUS:
            errs.append(err("BONUS_MONTH_INVALID", "cashflow.income.bonus", f"상여는 0~{MAX_BONUS}개"))
        else:
            seen = set()
            for b in bonus:
                m = b.get("month") if isinstance(b, dict) else None
                a = b.get("amount") if isinstance(b, dict) else None
                valid_m = isinstance(m, int) and not isinstance(m, bool) and 1 <= m <= 12
                if not valid_m or m in seen:
                    errs.append(err("BONUS_MONTH_INVALID", "cashflow.income.bonus", "상여 월은 1~12, 중복 불가"))
                if valid_m:                             # 실험 H15: 비해시 month 를 seen 에 넣으면 TypeError
                    seen.add(m)
                if not _nonneg_int(a):
                    errs.append(err("CASHFLOW_FIELD_RANGE", "cashflow.income.bonus", "상여 금액은 0 이상의 정수"))
        for k in ("fixed_monthly", "variable_monthly"):
            if not _nonneg_int(expense.get(k)):
                errs.append(err("CASHFLOW_FIELD_RANGE", f"cashflow.expense.{k}", "지출은 0 이상의 정수"))
        vbm = expense.get("variable_by_month") or {}     # 검증은 원값으로 — _obj 로 정규화하면 아래 isinstance 가 죽은 코드가 되어
        if not isinstance(vbm, dict):                    # 비-dict 배수가 조용히 무시되고 적자월 불허(§3.2)를 우회한다(리뷰 R-2)
            errs.append(err("CASHFLOW_FIELD_RANGE", "cashflow.expense.variable_by_month", "달력 월별 배수 객체"))
        else:
            for k, v in vbm.items():
                try:
                    km = int(k)
                except (TypeError, ValueError):
                    km = 0
                if _is_num(v) and not math.isfinite(v):      # 실험 X18y — 범위가 아니라 타입 문제로 알린다
                    errs.append(err("NUMBER_NOT_FINITE", "cashflow.expense.variable_by_month",
                                    f"배수가 유한한 수가 아님 (NaN·무한대): {k}"))
                    break
                if not (1 <= km <= 12) or not _is_num(v) or v < 0 or v > MAX_VARIABLE_MULT:
                    errs.append(err("CASHFLOW_FIELD_RANGE", "cashflow.expense.variable_by_month",
                                    f"키는 1~12, 배수는 0~{MAX_VARIABLE_MULT:g}"))
                    break
    else:  # profile
        prof = _obj(cf.get("profile"))
        for k in ("income", "expense"):
            arr = prof.get(k)
            if not (isinstance(arr, list) and len(arr) == 12 and all(_nonneg_int(x) for x in arr)):
                errs.append(err("PROFILE_LENGTH", f"cashflow.profile.{k}", "달력 1~12월 12개의 0 이상 정수(결측 불가)"))

    mp = debt.get("monthly_payment", 0)
    if not _nonneg_int(mp):
        errs.append(err("CASHFLOW_FIELD_RANGE", "cashflow.debt.monthly_payment", "월 원리금은 0 이상의 정수"))
    mr = debt.get("months_remaining")
    if mr is not None and not _nonneg_int(mr):
        errs.append(err("CASHFLOW_FIELD_RANGE", "cashflow.debt.months_remaining", "잔여 개월은 0 이상의 정수 또는 null"))

    cur = ef.get("current", 0)
    if not _nonneg_int(cur):
        errs.append(err("CASHFLOW_FIELD_RANGE", "cashflow.emergency_fund.current", "비상자금 보유액은 0 이상의 정수"))
    tm = ef.get("target_months")
    if tm is not None and not (_nonneg_int(tm) and tm <= MAX_EMERGENCY_MONTHS):
        errs.append(err("CASHFLOW_FIELD_RANGE", "cashflow.emergency_fund.target_months", f"목표 배수는 0~{MAX_EMERGENCY_MONTHS}"))

    gm = cf.get("growth_mode", "flat")
    if gm not in GROWTH_MODES:
        errs.append(err("OPTION_INVALID", "cashflow.growth_mode", "growth_mode는 flat | replay"))

    if errs:
        return errs

    # 12개월 프로파일 적자월 검증 (정적, §3.2 — 개정 ⑥ B-2, 2026-09-04)
    #
    # months_remaining 은 시작월 기준 경과 개월(t)이고 이 프로파일은 달력 월(1~12)이라,
    # 시작월을 모르는 정적 단계에서는 "몇 월에 부채가 끝나는지"를 알 수 없다.
    # 1 ≤ mr ≤ 11 이면 부채 항을 세지 않고, 정확한 판정은 contribution_path 의
    # 경과 경로 재검사(t=1..n, D_t 가 mr 을 반영)에 맡긴다. n ≥ 12 이므로 그 검사가
    # mr 구간을 빠짐없이 덮는다 — 적자월 불허 원칙은 유지되고 사유만 정확해진다.
    prof = build_profile(cf)
    mr = prof.months_remaining
    d_static = prof.debt_monthly if (mr is None or int(mr) >= 12) else 0
    deficits = [{"m": m + 1, "F_m": prof.income[m] - prof.expense[m] - d_static}
                for m in range(12) if prof.income[m] - prof.expense[m] - d_static < 0]
    if deficits:
        errs.append(err("CASHFLOW_DEFICIT_MONTHS", "cashflow",
                        "여유자금이 음수인 달이 있음 — 지출·상여·부채값을 조정해 재제출: "
                        + ", ".join(f"{d['m']}월 {d['F_m']:,}원" for d in deficits),
                        months=deficits))
    return errs


def build_profile(cf) -> Profile:
    """검증 통과한 cashflow → 12개월 프로파일 (§3.1). 값은 정수(원)."""
    mode = cf.get("mode", "summary")
    income = _obj(cf.get("income"))
    expense = _obj(cf.get("expense"))
    debt = _obj(cf.get("debt"))
    ef = _obj(cf.get("emergency_fund"))
    itype = income.get("type", "employee")
    bonus_by_month = {}
    if mode == "summary":
        for b in income.get("bonus") or []:
            bonus_by_month[int(b["month"])] = int(b["amount"])
        vbm = {int(k): float(v) for k, v in (_obj(expense.get("variable_by_month"))).items()}
        Y = [int(income["regular_monthly"]) + bonus_by_month.get(m, 0) for m in range(1, 13)]
        C = [int(expense["fixed_monthly"]) + math.floor(int(expense["variable_monthly"]) * vbm.get(m, 1.0))
             for m in range(1, 13)]
    else:
        prof = cf["profile"]
        Y = [int(x) for x in prof["income"]]
        C = [int(x) for x in prof["expense"]]
    D = int(debt.get("monthly_payment", 0) or 0)
    if debt.get("months_remaining") == 0:              # 실험 H18: 잔여 0 = 이미 끝난 부채 — 정적 검증·C_avg 에 유령으로 남지 않게
        D = 0
    tm = ef.get("target_months")
    if tm is None:
        tm = DEFAULT_EMERGENCY_MONTHS[itype]
    return Profile(income=Y, expense=C, debt_monthly=D, months_remaining=debt.get("months_remaining"),
                   bonus_by_month=bonus_by_month, emergency_current=int(ef.get("current", 0) or 0),
                   emergency_target_months=int(tm), growth_mode=cf.get("growth_mode", "flat"), mode=mode)


# ---------------------------------------------------------------------------
# 경로 생성
# ---------------------------------------------------------------------------

def _cal(start_month: str, t: int) -> int:
    """cal(t) = month_of(start_month + t − 1)"""
    return month_of(add_months(start_month, t - 1))


def _locked_vector(n: int, locked_events) -> list:
    L = [0] * (n + 1)
    for t, amount in locked_events or []:
        if 1 <= t <= n:
            L[t] += int(amount)
    return L


def _expand(prof: Profile, n: int, start_month: str, w_factor=None, p_factor=None):
    """12개월 프로파일 → n개월 (§3.4). w_factor/p_factor: t → 배수 (None = flat)."""
    Y, C, D = [0] * (n + 1), [0] * (n + 1), [0] * (n + 1)
    for t in range(1, n + 1):
        m = _cal(start_month, t)
        y, c = prof.income[m - 1], prof.expense[m - 1]
        if w_factor is not None:
            y = math.floor(y * w_factor(t))
        if p_factor is not None:
            c = math.floor(c * p_factor(t))
        d = prof.debt_monthly
        if prof.months_remaining is not None and t > prof.months_remaining:
            d = 0
        Y[t], C[t], D[t] = y, c, d
    return Y, C, D


def _apply(prof: Profile, Y, C, D, L, n: int):
    """F·E·M 계산 (§3.2·§3.3·§3.6). 반환 (F, E, M, filled_month)."""
    F, E, M = [0] * (n + 1), [0] * (n + 1), [0] * (n + 1)
    gap = max(0, math.floor(prof.emergency_target_months * prof.living_cost_avg) - prof.emergency_current)
    filled = 0 if gap == 0 else None
    for t in range(1, n + 1):
        F[t] = Y[t] - C[t] - D[t]
        e = min(F[t], gap) if F[t] > 0 else 0
        E[t] = e
        gap -= e
        if filled is None and gap == 0:
            filled = t
        M[t] = F[t] - E[t] + L[t]
    return F, E, M, filled


def constant_path(monthly: int, n: int, locked_events=None) -> CashflowResult:
    """v0.2 경로: funds.monthly 상수 (+ 묶인 자금 해제). mode = none."""
    L = _locked_vector(n, locked_events)
    M = [int(monthly) + L[t] for t in range(1, n + 1)]
    return CashflowResult(mode="none", contributions=M, locked=L[1:],
                          months_zero=sum(1 for x in M if x == 0), emergency_filled_month=0)


def contribution_path(cf: dict, *, n: int, start_month: str, window_start: str, dataset=None,
                      locked_events=None) -> CashflowResult:
    """
    cashflow 입력 → M_t (t = 1..n).  검증(validate_cashflow)을 통과한 입력을 전제한다.
    - growth_mode = replay: dataset의 임금지수·CPI 후행 MA12 비율을 곱한다(§3.4). 시계열 부족 시 SERIES_NOT_AVAILABLE.
    - 확장 경로에서도 F_t ≥ 0 을 재검사한다 (위반 시 CASHFLOW_DEFICIT_MONTHS, 데이터 단계).
    """
    prof = build_profile(cf)
    L = _locked_vector(n, locked_events)

    w_factor = p_factor = None
    if prof.growth_mode == "replay":
        if dataset is None or not dataset.wage or not dataset.cpi:
            raise ValidationError([err("SERIES_NOT_AVAILABLE", "cashflow.growth_mode",
                                       "replay에는 임금지수(wage_index_monthly)·CPI(cpi_monthly) 시계열이 필요함")])
        base = add_months(window_start, -1)
        need_start, need_end = add_months(window_start, -12), add_months(window_start, n - 1)
        for name, label in (("wage", "임금지수"), ("cpi", "CPI")):
            if not dataset.series_covers(name, need_start, need_end):
                span = dataset.series_span(name)
                raise ValidationError([err("SERIES_NOT_AVAILABLE", "cashflow.growth_mode",
                                           f"replay에 필요한 {label} 구간 {need_start}~{need_end}이 스냅샷에 없음"
                                           f"(보유 {span[0]}~{span[1]}). 없는 데이터를 만들지 않음 — 스냅샷 갱신 또는 flat 사용",
                                           required=[need_start, need_end], available=list(span))])
        W0, P0 = dataset.ma12("wage", base), dataset.ma12("cpi", base)
        Wt = [None] + [dataset.ma12("wage", add_months(window_start, t - 1)) for t in range(1, n + 1)]
        Pt = [None] + [dataset.ma12("cpi", add_months(window_start, t - 1)) for t in range(1, n + 1)]
        w_factor = lambda t: Wt[t] / W0  # noqa: E731
        p_factor = lambda t: Pt[t] / P0  # noqa: E731

    Y, C, D = _expand(prof, n, start_month, w_factor, p_factor)
    deficits = [{"t": t, "cal": _cal(start_month, t), "F_t": Y[t] - C[t] - D[t]}
                for t in range(1, n + 1) if Y[t] - C[t] - D[t] < 0]
    if deficits:
        why = "임금·물가 재생 구간" if prof.growth_mode == "replay" else "부채 상환 구간 포함"
        raise ValidationError([err("CASHFLOW_DEFICIT_MONTHS", "cashflow",
                                   f"확장 경로에서 여유자금이 음수인 달이 있음({why}): "
                                   + ", ".join(f"t={d['t']}({d['cal']}월) {d['F_t']:,}원" for d in deficits[:6]),
                                   months=deficits)])
    F, E, M, filled = _apply(prof, Y, C, D, L, n)

    total_m = sum(M[1:])
    total_y = sum(Y[1:])
    surplus_rate = round(100.0 * sum(F[1:]) / total_y, 2) if total_y > 0 else None
    bonus_share = None
    if prof.mode == "summary" and total_m > 0:
        bonus_share = round(100.0 * sum(min(M[t], prof.bonus_by_month.get(_cal(start_month, t), 0))
                                        for t in range(1, n + 1)) / total_m, 2)
    growth_effect = None
    if prof.growth_mode == "replay":
        Yf, Cf, Df = _expand(prof, n, start_month)
        _, _, Mf, _ = _apply(prof, Yf, Cf, Df, L, n)
        tf = sum(Mf[1:])
        growth_effect = round(100.0 * (total_m / tf - 1.0), 2) if tf > 0 else None

    return CashflowResult(
        mode=prof.mode, contributions=M[1:], income=Y[1:], expense=C[1:], debt=D[1:], surplus=F[1:],
        emergency=E[1:], locked=L[1:], profile_income=list(prof.income), profile_expense=list(prof.expense),
        debt_monthly=prof.debt_monthly, bonus_by_month=dict(prof.bonus_by_month),
        surplus_rate_pct=surplus_rate, bonus_share_pct=bonus_share,
        months_zero=sum(1 for x in M[1:] if x == 0), emergency_filled_month=filled, growth_effect_pct=growth_effect,
    )
