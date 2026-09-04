"""
engine.py — Loop 고정 주기 리밸런싱 시뮬레이터 v0.3 (KAN-11)

구현 기준 (단일 기준 문서 = KAN-9 분석 계약)
  · v0.2  실제 월간 총수익률 재생 · 데이터셋 주입 · MDD/실현 변동성 · ΔM 이분법 · 카탈로그 확장
  · v0.3  1층: 현금흐름 계층(M_t) · 목표 월 · 비상자금 · 적자월 불허
          2층: 시작 상태 구성(현금/기보유/묶인 자금) · 임금·CPI 재생 · 시변 예금금리 · 1주 단위 매수 · 일반/ISA 세후
  · 모든 신규 필드는 선택 — 미입력 시 v0.2 동작(회귀 테스트 B①로 보장)

원칙: 순수 표준 라이브러리 · 결정론(난수·시간 의존 없음, now 주입) · 네트워크·파일 접근 없음(Dataset 주입) ·
      계약에 없는 수치·가정을 추가하지 않는다.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import cashflow as cfl
from .dataset import Dataset, add_months, is_month, month_index, month_of, months_between
from .errors import ValidationError, err

# ---------------------------------------------------------------------------
# 상수 (계약 v0.2 §3 · v0.3 §4.6)
# ---------------------------------------------------------------------------

ASSUMPTIONS_VERSION = "v0.3"
PERIODS = {"M": 1, "Q": 3, "H": 6}            # 리밸런싱 주기 (개월)
PROPENSITY_BOUNDS = (30.0, 60.0)              # 성향 라벨 경계 (%) — 확정 ⑨
GOAL_AMOUNT_RANGE = (1_000_000, 10_000_000_000)
HORIZON_RANGE = (12, 120)                     # 확정 ②
BISECT_MAX_ITER = 50
BISECT_MAX_EXPAND = 60                        # 상한 2배 확장 횟수 (계약 §4.8 A안 — 금액 가드 대체)
BISECT_TOL = 1.0                              # 원

TAX_WITHHOLDING_OTHER = 0.154                 # 국내상장 기타 ETF 매매차익 원천징수 (일반계좌, 매도 시)
TAX_FOREIGN_CGT = 0.22                        # 해외 상장 양도소득세 (연간 통산)
FOREIGN_ANNUAL_DEDUCTION = 2_500_000          # 연 기본공제
ISA_TAX_RATE = 0.099                          # ISA 만기 정산 (비과세 한도 초과분)
ISA_EXEMPT_DEFAULT = 2_000_000                # 일반형 200만 (서민형 400만은 입력)
ISA_ANNUAL_LIMIT = 20_000_000
ISA_TOTAL_LIMIT = 100_000_000
ISA_MIN_HORIZON = 36                          # 의무가입 3년
ACCOUNT_TYPES = ("general", "isa")
SAFE_RATE_MODES = ("fixed_avg", "replay")
GOAL_TYPES = ("lump_sum", "housing", "wedding", "education", "business")
TAX_CLASSES = ("domestic_equity", "domestic_listed_other", "foreign_listed")


def monthly_rate(annual: float) -> float:
    """연 실효 → 월 복리 (계약 §4.3)."""
    return (1.0 + annual) ** (1.0 / 12.0) - 1.0


def _nonneg_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool) and x >= 0


def _obj(v):
    """입력 블록을 dict 로 정규화 — 실험 X18x: `inputs.get(k) or {}` 는 truthy 비-dict(숫자·리스트)를 그대로 통과시켜
    뒤의 .get() 이 AttributeError 로 죽는다. 블록이 dict 가 아니면 '비어 있음'으로 보고 필드별 오류가 나게 한다."""
    return v if isinstance(v, dict) else {}


def _is_num(v) -> bool:
    return not isinstance(v, bool) and isinstance(v, (int, float))


def _finite_num(v) -> bool:
    """실험 X18y: NaN 은 모든 범위 비교가 False 라 `v <= 0 or v > 100` 류 가드를 통과한다."""
    return _is_num(v) and math.isfinite(v)


def _nonfinite(v) -> bool:
    """수는 맞는데 NaN·±inf — 범위 오류가 아니라 별도 코드(NUMBER_NOT_FINITE)로 알린다 (계약 §2.3 개정)."""
    return _is_num(v) and not math.isfinite(v)


def _alloc_ok(alloc) -> bool:
    keys = ("invest", "safe", "other")
    if not isinstance(alloc, dict) or set(alloc) != set(keys):
        return False
    for k in keys:
        v = alloc[k]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 or v > 100:
            return False
    return abs(sum(alloc[k] for k in keys) - 100.0) < 1e-9


# ---------------------------------------------------------------------------
# 1. 정적 검증 (데이터 불필요 — 즉시 반환)
# ---------------------------------------------------------------------------

def validate_static(inputs: dict) -> list:
    errs = []
    E = errs.append
    goal = _obj(inputs.get("goal"))
    funds = _obj(inputs.get("funds"))
    alloc = _obj(inputs.get("alloc"))
    portfolio = _obj(inputs.get("portfolio"))
    assets = portfolio.get("assets") or []
    options = _obj(inputs.get("options"))
    cf = inputs.get("cashflow")

    # A. 목표
    amount = goal.get("amount")
    if not _nonneg_int(amount) or not (GOAL_AMOUNT_RANGE[0] <= amount <= GOAL_AMOUNT_RANGE[1]):
        E(err("GOAL_AMOUNT_RANGE", "goal.amount", "목표 금액은 100만 ~ 100억 원의 정수"))
    tm, hm = goal.get("target_month"), goal.get("horizon_months")
    if tm is not None and hm is not None:
        E(err("GOAL_SPEC_CONFLICT", "goal", "target_month와 horizon_months는 동시에 줄 수 없음"))
    elif tm is not None:
        if not is_month(tm):
            E(err("TARGET_MONTH_RANGE", "goal.target_month", "목표 월은 YYYY-MM 형식"))
    else:
        if not _nonneg_int(hm) or not (HORIZON_RANGE[0] <= hm <= HORIZON_RANGE[1]):
            E(err("GOAL_HORIZON_RANGE", "goal.horizon_months",
                  f"목표 기간은 {HORIZON_RANGE[0]} ~ {HORIZON_RANGE[1]}개월의 정수 (또는 goal.target_month)"))
    if goal.get("type") is not None and goal.get("type") not in GOAL_TYPES:
        E(err("OPTION_INVALID", "goal.type", f"목표 유형은 {' | '.join(GOAL_TYPES)}"))

    # B. 자금
    initial = funds.get("initial")
    if not _nonneg_int(initial):
        E(err("FUNDS_INITIAL_RANGE", "funds.initial", "초기 자금은 0 이상의 정수"))
    monthly = funds.get("monthly")
    if monthly is not None and cf is not None:
        E(err("MONTHLY_SOURCE_CONFLICT", "funds.monthly", "funds.monthly와 cashflow는 동시에 줄 수 없음"))
    if cf is None:
        if not _nonneg_int(monthly):
            E(err("FUNDS_MONTHLY_RANGE", "funds.monthly", "월 저축액은 0 이상의 정수 (또는 cashflow 입력)"))
        elif _nonneg_int(initial) and initial == 0 and monthly == 0:
            E(err("NO_FUNDS", "funds", "초기 자금과 월 저축액이 모두 0이면 계산할 수 없음"))
    else:
        errs.extend(validate_cashflow_block(cf))

    comp = funds.get("composition")
    holding_codes = []
    if comp is not None:
        if not isinstance(comp, dict):
            E(err("COMPOSITION_SUM", "funds.composition", "composition은 {cash, holdings, locked} 객체"))
        else:
            cash = comp.get("cash", 0)
            holdings = comp.get("holdings") or []
            locked = comp.get("locked") or []
            ok = _nonneg_int(cash)
            if not isinstance(holdings, list) or not isinstance(locked, list):   # 실험 X18: 스칼라면 for 문이 TypeError
                holdings, locked, ok = [], [], False
            for h in holdings:
                if not (isinstance(h, dict) and isinstance(h.get("code"), str) and _nonneg_int(h.get("amount"))):
                    ok = False
                elif h["amount"] > 0:            # 계약 §2.2 B 3′ 개정(2026-09-03): 0원 항목은 없는 것으로 본다
                    holding_codes.append(h["code"])
            for lk in locked:
                if not (isinstance(lk, dict) and _nonneg_int(lk.get("amount")) and is_month(lk.get("release_month", ""))):
                    ok = False
            if not ok:
                E(err("COMPOSITION_SUM", "funds.composition",
                      "cash는 0 이상 정수, holdings[]는 {code, amount}, locked[]는 {amount, release_month(YYYY-MM)}"))
            elif _nonneg_int(initial):
                total = cash + sum(h["amount"] for h in holdings) + sum(lk["amount"] for lk in locked)
                if total != initial:
                    E(err("COMPOSITION_SUM", "funds.composition",
                          f"cash + holdings + locked = {total:,} ≠ funds.initial {initial:,}"))

    # D. 배분
    for name in ("initial", "monthly"):
        a = alloc.get(name)
        # NaN 은 모든 범위 비교가 거짓이라 `_alloc_ok` 의 합계 검사에서 걸린다 — "합계가 100이어야 함"은 사유가 틀렸다.
        nf = [k for k in ("invest", "safe", "other") if isinstance(a, dict) and _nonfinite(a.get(k))]
        if nf:
            E(err("NUMBER_NOT_FINITE", f"alloc.{name}", f"유한한 수가 아님 (NaN·무한대): {', '.join(nf)}"))
        elif not _alloc_ok(a):
            E(err("ALLOC_SUM_" + name.upper(), f"alloc.{name}",
                  "투자/안전저축/기타 비율은 각 0~100이며 합계가 100이어야 함"))

    # 포트폴리오 (투자 배분이 쓰이거나 기보유 자산이 있으면 필수)
    ai, am = alloc.get("initial"), alloc.get("monthly")
    invest_used = bool(holding_codes)
    if _alloc_ok(ai) and _nonneg_int(initial) and initial > 0 and ai["invest"] > 0:
        invest_used = True
    if _alloc_ok(am) and am["invest"] > 0 and (cf is not None or (_nonneg_int(monthly) and monthly > 0)):
        invest_used = True
    # 계약 §2.1 #7 개정 결정(2026-09-03, B안): 투자 배분이 0%면 엔진이 포트폴리오를 **읽지 않는다**(_resolve 에서 비움).
    # 읽지 않으므로 검증도 하지 않는다 — 크래시 위험이 없고, "invest = 0 이면 생략 가능"이라는 계약 취지와 맞는다.
    # 투자 배분이 있을 때는 컨테이너 타입·개수(1~3)·원소 형식을 모두 본다(실험 X18t·리뷰 R-1/R-3).
    if invest_used and not (isinstance(assets, list) and 1 <= len(assets) <= 3):
        E(err("PORTFOLIO_REQUIRED", "portfolio.assets", "투자 배분(또는 기보유 자산)이 있으면 포트폴리오(1~3개 자산)가 필요함"))
    elif invest_used:
        seen, wsum, bad = set(), 0.0, False
        for a in assets:
            code, w = (a.get("code"), a.get("weight")) if isinstance(a, dict) else (None, None)
            if not isinstance(code, str) or not code:
                E(err("PORTFOLIO_REQUIRED", "portfolio.assets", "자산 코드가 비어 있음"))
                bad = True
            else:                                   # 실험 H15: 비문자열 code 를 seen 에 넣으면 TypeError
                if code in seen:
                    E(err("PORTFOLIO_ASSET_DUP", "portfolio.assets", f"자산 중복: {code}"))
                seen.add(code)
            if _nonfinite(w):                                # 실험 X18y — 범위가 아니라 타입 문제로 알린다
                E(err("NUMBER_NOT_FINITE", "portfolio.assets", f"자산 비중이 유한한 수가 아님 (NaN·무한대): {code}"))
                bad = True
            elif not _finite_num(w) or w <= 0 or w > 100:
                E(err("PORTFOLIO_WEIGHT_RANGE", "portfolio.assets", "자산 비중은 0 초과 100 이하"))
                bad = True
            else:
                wsum += float(w)
        if not bad and abs(wsum - 100.0) > 1e-9:
            E(err("WEIGHTS_SUM", "portfolio.assets", "포트폴리오 비중 합계는 100%"))
        if not bad:                                  # 리뷰: 원소가 망가진 경우까지 유령 HOLDING_NOT_IN_PORTFOLIO 를 내지 않는다
            for hc in holding_codes:
                if hc not in seen:
                    E(err("HOLDING_NOT_IN_PORTFOLIO", "funds.composition.holdings",
                          f"기보유 자산 {hc}은 portfolio.assets에 있어야 함"))

    if inputs.get("rebalancing") is not None and not isinstance(inputs.get("rebalancing"), dict):
        E(err("FOCUS_INVALID", "rebalancing", "rebalancing은 객체"))   # 리뷰 R-5: options 와 대칭 — 조용히 무시 금지
    focus = _obj(inputs.get("rebalancing")).get("focus")
    if focus is not None and (not isinstance(focus, str) or focus not in PERIODS):   # 실험 H15: 비해시 타입 방어
        E(err("FOCUS_INVALID", "rebalancing.focus", "focus는 M/Q/H 중 하나"))

    # 실험 X18x: 블록이 dict 가 아니면 _obj 가 {} 로 만들어 크래시는 막지만, 조용히 무시되면 안 된다
    if inputs.get("options") is not None and not isinstance(inputs.get("options"), dict):
        E(err("OPTION_INVALID", "options", "options는 객체"))

    # E. 옵션 (2층)
    srm = options.get("safe_rate_mode", "fixed_avg")
    if srm not in SAFE_RATE_MODES:
        E(err("OPTION_INVALID", "options.safe_rate_mode", "safe_rate_mode는 fixed_avg | replay"))
    if not isinstance(options.get("lot_rounding", False), bool):
        E(err("OPTION_INVALID", "options.lot_rounding", "lot_rounding은 true | false"))
    acc = options.get("account")
    if acc is not None:
        if not isinstance(acc, dict) or acc.get("type") not in ACCOUNT_TYPES:
            E(err("ACCOUNT_TYPE_UNSUPPORTED", "options.account.type",
                  "계좌 유형은 general | isa — 연금계좌(pension)는 55세 이전 인출 시 기타소득세 16.5%로 이 목표 기간에 부적합"))
        else:
            lim = acc.get("isa_exempt_limit", ISA_EXEMPT_DEFAULT)
            if not _nonneg_int(lim):
                E(err("OPTION_INVALID", "options.account.isa_exempt_limit", "비과세 한도는 0 이상의 정수"))
            if acc["type"] == "isa" and _nonneg_int(hm) and hm < ISA_MIN_HORIZON:
                E(err("ISA_HORIZON_TOO_SHORT", "options.account",
                      f"ISA 의무가입기간 3년 — 목표 기간 {hm}개월은 {ISA_MIN_HORIZON}개월 미만"))
    return errs


def validate_cashflow_block(cf) -> list:
    return cfl.validate_cashflow(cf)


# ---------------------------------------------------------------------------
# 2. 계획 해석 (데이터 의존 검증 + 현금흐름 계층) → Plan
# ---------------------------------------------------------------------------

class _NoPlanFunds(Exception):
    pass


@dataclass
class Plan:
    n: int
    start_month: str
    target_month: str
    window_start: str
    window_end: str
    codes: list
    target_w: dict
    inv_i: float
    safe_i: float
    other_i: float
    inv_m: float
    safe_m: float
    other_m: float
    cash: int
    holdings: dict
    locked_in: list
    locked_out: int
    contributions: list
    returns: dict
    safe_rates: list
    safe_rate_avg_pct: float
    cost: dict
    tax_class: dict
    lot: bool
    lot_size: dict
    price0: dict | None
    account: dict | None
    cal: list
    cashflow: cfl.CashflowResult
    growth_mode: str
    safe_rate_mode: str
    warnings: list = field(default_factory=list)
    series_used: list = field(default_factory=list)


def _resolve(inputs: dict, dataset: Dataset, *, horizon_override: int | None = None, strict: bool = True,
             latest_override: str | None = None) -> Plan:
    """
    입력 + 스냅샷 → Plan (데이터 의존 검증 + 현금흐름 계층 실행).
    horizon_override: 기간 연장 탐색용 n′.  strict=False: 한도·NO_FUNDS 등 사용자 검증을 생략(내부 탐색용).
    latest_override: 롤링 검증(rolling.py) 전용 — 최신 확정월을 이 달로 두고 기준 구간·시작월을 잡는다. 사용자 경로에서는 쓰지 않는다.
    """
    goal = inputs["goal"]
    funds = inputs["funds"]
    alloc = inputs["alloc"]
    options = _obj(inputs.get("options"))
    cf = inputs.get("cashflow")

    latest = latest_override or dataset.latest_month
    start_month = add_months(latest, 1)

    # 목표 월 ↔ 개월 수
    if horizon_override is not None:
        n = int(horizon_override)
    elif goal.get("target_month") is not None:
        n = months_between(start_month, goal["target_month"]) + 1
        if not (HORIZON_RANGE[0] <= n <= HORIZON_RANGE[1]):
            lo, hi = add_months(start_month, HORIZON_RANGE[0] - 1), add_months(start_month, HORIZON_RANGE[1] - 1)
            raise ValidationError([err("TARGET_MONTH_RANGE", "goal.target_month",
                                       f"시작월 {start_month} 기준 목표 월은 {lo} ~ {hi} (12~120개월)")])
    else:
        n = int(goal["horizon_months"])
    target_month = add_months(start_month, n - 1)
    cal = [0] + [month_of(add_months(start_month, t - 1)) for t in range(1, n + 1)]

    # 자산 코드
    assets = _obj(inputs.get("portfolio")).get("assets") or []
    comp = funds.get("composition")
    holdings_in = {}
    if comp:
        for h in comp.get("holdings") or []:
            amt = int(h["amount"])
            if amt == 0:                        # 계약 §2.2 B 3′ 개정 — locked 와 대칭 (정적·데이터 두 단계 동일 규칙)
                continue
            holdings_in[h["code"]] = holdings_in.get(h["code"], 0) + amt
    # 계약 §2.1 #7 개정(2026-09-03, B안): 투자 배분이 0%면 포트폴리오를 쓰지 않으므로 아예 읽지 않는다.
    # 쓰지 않는 자산의 이력이 INSUFFICIENT_HISTORY 를 유발하던 문제(리뷰 R-7)도 함께 닫힌다.
    ai_r, am_r = _obj(alloc.get("initial")), _obj(alloc.get("monthly"))
    invest_used = bool(holdings_in) \
        or (int(funds.get("initial") or 0) > 0 and (ai_r.get("invest") or 0) > 0) \
        or ((am_r.get("invest") or 0) > 0 and (cf is not None or int(funds.get("monthly") or 0) > 0))
    if not invest_used:
        assets = []

    codes = [a["code"] for a in assets]
    unknown = [c for c in codes + list(holdings_in) if c not in dataset.catalog]
    if unknown:
        raise ValidationError([err("ASSET_NOT_IN_CATALOG", "portfolio.assets",
                                   f"카탈로그에 없는 자산 코드: {', '.join(sorted(set(unknown)))} (등재: {', '.join(dataset.codes)})")])
    target_w = {a["code"]: float(a["weight"]) / 100.0 for a in assets}

    # 기준 구간 (계약 §3.1: 최신 확정월에서 역산 — 사람이 고를 수 없음)
    window_end = latest
    window_start = add_months(latest, -(n - 1))
    if codes:
        first, last, avail = dataset.common_window(codes)
        covered = (month_index(first) <= month_index(window_start)
                   and all(month_index(max(dataset.returns[c])) >= month_index(window_end) for c in codes))
        if not covered:
            raise ValidationError([err("INSUFFICIENT_HISTORY", "goal",
                                       f"선택 자산의 공통 가용 구간은 {first}~{last} ({avail}개월) — 목표 기간 {n}개월"
                                       f"({window_start}~{window_end})을 재생할 수 없음. 최대 {avail}개월까지 가능",
                                       max_months=avail)])
    if not dataset.series_covers("deposit", window_start, window_end):
        span = dataset.series_span("deposit")
        avail = months_between(span[0], latest) + 1
        raise ValidationError([err("INSUFFICIENT_HISTORY", "goal",
                                   f"정기예금 금리 시계열({span[0]}~{span[1]})이 기준 구간 {window_start}~{window_end}을 덮지 않음. "
                                   f"최대 {avail}개월까지 가능", max_months=avail)])

    # 안전 버킷 금리 (§1.4 · §4.4)
    srm = options.get("safe_rate_mode", "fixed_avg")
    rates_pct = dataset.deposit_series(window_start, n)
    r_avg_pct = dataset.deposit_window_mean(window_start, n)
    if srm == "fixed_avg":
        r_fixed = monthly_rate(r_avg_pct / 100.0)
        safe_rates = [r_fixed] * n
    else:
        safe_rates = [monthly_rate(p / 100.0) for p in rates_pct]

    # 비용·세금 클래스·1주 단위 (§1.1 · §4.5)
    cost = {c: dataset.catalog[c]["cost"]["commission_one_way"] + dataset.catalog[c]["cost"]["fx_spread_one_way"] for c in codes}
    tax_class = {c: dataset.catalog[c]["tax_class"] for c in codes}
    lot_size = {c: dataset.catalog[c]["lot_size"] for c in codes}
    lot = bool(options.get("lot_rounding", False))
    price0 = None
    if lot and codes:
        price0 = {}
        base = add_months(window_start, -1)
        for c in codes:
            p = dataset.synthetic_price(c, base)
            if p is None or p <= 0:
                pa = dataset.catalog[c].get("price_anchor")
                raise ValidationError([err("SERIES_NOT_AVAILABLE", "options.lot_rounding",
                                           f"{c}: 1주 단위 매수에 필요한 합성가격을 만들 수 없음 "
                                           f"(price_anchor={pa}, 필요 시점 {base}) — 카탈로그 price_anchor·수익률 이력 확인")])
            price0[c] = p

    # 계좌·세금 (§4.6)
    acc = options.get("account")
    account = None
    if acc:
        account = {"type": acc["type"], "exempt": int(acc.get("isa_exempt_limit", ISA_EXEMPT_DEFAULT))}
        if account["type"] == "isa":
            bad = [c for c in codes if tax_class[c] == "foreign_listed"]
            if bad:
                raise ValidationError([err("ACCOUNT_ASSET_INELIGIBLE", "options.account",
                                           f"ISA에는 해외 상장 자산을 편입할 수 없음: {', '.join(bad)} "
                                           f"(국내상장 대안 사용, 예: US_EQ → US_EQ_KR)")])
            if n < ISA_MIN_HORIZON and strict:
                raise ValidationError([err("ISA_HORIZON_TOO_SHORT", "options.account",
                                           f"ISA 의무가입기간 3년 — 목표 기간 {n}개월은 {ISA_MIN_HORIZON}개월 미만")])

    # 시작 상태 (§4.3) · 묶인 자금 (§3.5)
    warnings = []
    locked_in, locked_out = [], 0
    if comp:
        cash = int(comp.get("cash", 0))
        for lk in comp.get("locked") or []:
            rm, amt = lk["release_month"], int(lk["amount"])
            # 계약 §2.2 B 3′ 개정(2026-09-03): 금액 0 항목은 없는 것으로 보고 **경고도 내지 않는다**(실험 M-22).
            # UI 빈 행이 흔하고 계산에 영향이 없으므로 정상 입력 흐름을 방해하지 않는다 — 한계는 계약 §9·README 에 명시.
            if amt == 0:
                continue
            if months_between(start_month, rm) < 0:
                raise ValidationError([err("LOCKED_RELEASE_PAST", "funds.composition.locked",
                                           f"해제 월 {rm}이 시작월 {start_month} 이전 — 이미 현금이므로 cash로 옮겨 재제출")])
            t = months_between(start_month, rm) + 1
            if t > n:
                locked_out += amt
                warnings.append(err("LOCKED_RELEASE_OUT_OF_RANGE", "funds.composition.locked",
                                    f"해제 월 {rm}이 목표 월 {target_month} 이후 — 계획에서 제외(계획 외 자금으로 집계)",
                                    amount=amt, release_month=rm))
            else:
                locked_in.append((t, amt))
    else:
        cash = int(funds["initial"])
    holdings = {c: holdings_in.get(c, 0) for c in codes if holdings_in.get(c, 0) > 0}

    ai, am = alloc["initial"], alloc["monthly"]
    inv_i, safe_i, other_i = ai["invest"] / 100.0, ai["safe"] / 100.0, ai["other"] / 100.0
    inv_m, safe_m, other_m = am["invest"] / 100.0, am["safe"] / 100.0, am["other"] / 100.0

    # 납입 경로 (§3)
    growth_mode = "flat"
    if cf is not None:
        growth_mode = cf.get("growth_mode", "flat")
        cfres = cfl.contribution_path(cf, n=n, start_month=start_month, window_start=window_start,
                                      dataset=dataset, locked_events=locked_in)
    else:
        cfres = cfl.constant_path(int(funds["monthly"]), n, locked_in)
    M = cfres.contributions
    total_m = sum(M)

    if strict and cf is not None and int(funds["initial"]) == 0 and total_m == 0:
        raise ValidationError([err("NO_FUNDS", "funds", "초기 자금이 0이고 현금흐름에서 파생된 납입액 합계도 0이면 계산할 수 없음")])

    plan_funds = cash * (inv_i + safe_i) + sum(holdings.values()) + total_m * (inv_m + safe_m)
    if plan_funds <= 0:
        raise _NoPlanFunds()

    # ISA 납입 한도 (§4.6) — 계좌 연차(12개월 블록) 기준
    if strict and account and account["type"] == "isa":
        per_year = {}
        per_year[1] = cash * (inv_i + safe_i) + sum(holdings.values())
        for t in range(1, n + 1):
            y = (t - 1) // 12 + 1
            per_year[y] = per_year.get(y, 0.0) + M[t - 1] * (inv_m + safe_m)
        over = [(y, v) for y, v in sorted(per_year.items()) if v > ISA_ANNUAL_LIMIT]
        total = sum(per_year.values())
        if over or total > ISA_TOTAL_LIMIT:
            detail = ", ".join(f"{y}년차 {v:,.0f}원" for y, v in over) or f"총 {total:,.0f}원"
            raise ValidationError([err("ISA_LIMIT_EXCEEDED", "options.account",
                                       f"ISA 납입 한도(연 2,000만·총 1억 원) 초과: {detail}")])

    returns = dataset.returns_series(codes, window_start, n) if codes else {}
    series_used = (["monthly_returns"] if codes else []) + ["deposit_rate_monthly"]
    if growth_mode == "replay":
        series_used += ["cpi_monthly", "wage_index_monthly"]

    return Plan(n=n, start_month=start_month, target_month=target_month, window_start=window_start, window_end=window_end,
                codes=codes, target_w=target_w, inv_i=inv_i, safe_i=safe_i, other_i=other_i, inv_m=inv_m, safe_m=safe_m,
                other_m=other_m, cash=cash, holdings=holdings, locked_in=locked_in, locked_out=locked_out,
                contributions=list(M), returns=returns, safe_rates=safe_rates, safe_rate_avg_pct=r_avg_pct,
                cost=cost, tax_class=tax_class, lot=lot, lot_size=lot_size, price0=price0, account=account, cal=cal,
                cashflow=cfres, growth_mode=growth_mode, safe_rate_mode=srm, warnings=warnings, series_used=series_used)


# ---------------------------------------------------------------------------
# 3. 투자 버킷 회계 (연속량 / 1주 단위 · 원가 추적 · 세금)
# ---------------------------------------------------------------------------

class _Book:
    """투자 버킷. 연속량(v0.2) 또는 정수 주(§4.5). 자산별 원가 B_i(가치 기준 평균원가법, §4.6)."""

    def __init__(self, plan: Plan, *, lot: bool):
        self.codes = list(plan.codes)
        self.w = plan.target_w
        self.c = plan.cost
        self.tc = plan.tax_class
        self.lot = lot
        self.account = plan.account
        self.A = {c: 0.0 for c in self.codes}
        self.q = {c: 0 for c in self.codes}
        self.P = dict(plan.price0) if lot else None
        self.B = {c: 0.0 for c in self.codes}
        self.realized_cum = {c: 0.0 for c in self.codes}
        self.foreign_annual = 0.0
        self.R = 0.0
        self.cum_cost = 0.0
        self.tax_cum = 0.0

    # -- 조회
    def value(self, c) -> float:
        return self.q[c] * self.P[c] if self.lot else self.A[c]

    def total(self) -> float:
        return sum(self.value(c) for c in self.codes)

    def weights(self) -> dict:
        V = self.total()
        if V <= 0:
            return dict(self.w)
        return {c: self.value(c) / V for c in self.codes}

    def drift(self) -> float:
        V = self.total()
        if V <= 0:
            return 0.0
        return sum(abs(self.value(c) / V - self.w[c]) for c in self.codes)

    # -- 성장
    def grow(self, r: dict):
        for c in self.codes:
            if self.lot:
                self.P[c] *= 1.0 + r[c]
            else:
                self.A[c] *= 1.0 + r[c]

    # -- 매수 (목표 비중대로, 편도 비용)
    def buy_alloc(self, amount: float):
        if self.lot:
            pool = amount + self.R
            self.R = 0.0
            for c in self.codes:
                a = pool * self.w[c]
                if a <= 0:
                    continue
                p = self.P[c]
                q = int(a // (p * (1.0 + self.c[c])))
                ex = q * p
                fee = ex * self.c[c]
                self.q[c] += q
                self.B[c] += ex
                self.cum_cost += fee
                self.R += a - ex - fee
        else:
            for c in self.codes:
                a = amount * self.w[c]
                fee = a * self.c[c]
                net = a - fee
                self.A[c] += net
                self.B[c] += net
                self.cum_cost += fee

    def place_holdings(self, holdings: dict):
        """기보유 자산 — 그대로 배치(매수 비용 없음), 원가 = 시작 시점 평가액 (§4.3)."""
        for c, amt in holdings.items():
            if self.lot:
                q = int(amt // self.P[c])
                self.q[c] += q
                self.B[c] += q * self.P[c]
                self.R += amt - q * self.P[c]
            else:
                self.A[c] += amt
                self.B[c] += amt

    # -- 실현·세금
    def _realize(self, c, sale_value: float) -> float:
        """가치 sale_value 매도의 실현이익(부호 있음) 반환, 원가 B 갱신."""
        A = self.value(c)
        if A <= 0 or sale_value <= 0:
            return 0.0
        gain = sale_value * (1.0 - self.B[c] / A)
        self.B[c] *= (1.0 - sale_value / A)
        return gain

    def _ledger(self, c, gain: float):
        self.realized_cum[c] += gain
        if self.tc[c] == "foreign_listed":
            self.foreign_annual += gain

    def _withholding(self, c, gain: float) -> float:
        if self.account and self.account["type"] == "general" and self.tc[c] == "domestic_listed_other":
            return max(0.0, gain) * TAX_WITHHOLDING_OTHER
        return 0.0

    def rebalance(self):
        if self.lot:
            self._rebalance_lot()
        else:
            self._rebalance_continuous()

    def _rebalance_continuous(self):
        V = self.total()
        if V <= 0:
            return
        tgt = {c: self.w[c] * V for c in self.codes}
        sell = {c: max(0.0, self.A[c] - tgt[c]) for c in self.codes}
        buy = {c: max(0.0, tgt[c] - self.A[c]) for c in self.codes}
        fee = sum((sell[c] + buy[c]) * self.c[c] for c in self.codes)
        tax = 0.0
        for c in self.codes:                      # 1차: 매도 대금 기준 원천징수 (매도 시점 과세)
            if sell[c] > 0 and self.A[c] > 0:
                gain_pre = sell[c] * (1.0 - self.B[c] / self.A[c])
                tax += self._withholding(c, gain_pre)
        V_net = V - fee - tax                     # 2차: 세후 V 기준으로 w* 복원
        for c in self.codes:
            new = self.w[c] * V_net
            delta = new - self.A[c]
            if delta < 0:
                self._ledger(c, self._realize(c, -delta))
            elif delta > 0:
                self.B[c] += delta
            self.A[c] = new
        self.cum_cost += fee
        self.tax_cum += tax

    def _rebalance_lot(self):
        V = self.total()
        if V <= 0:
            return
        tgt = {c: self.w[c] * V for c in self.codes}
        for c in self.codes:                      # 매도: 초과분 floor 주
            excess = self.value(c) - tgt[c]
            s = int(excess // self.P[c]) if excess > 0 else 0
            if s <= 0:
                continue
            proceeds = s * self.P[c]
            gain = self._realize(c, proceeds)
            self._ledger(c, gain)
            tax = self._withholding(c, gain)
            fee = proceeds * self.c[c]
            self.q[c] -= s
            self.R += proceeds - fee - tax
            self.cum_cost += fee
            self.tax_cum += tax
        order = sorted(self.codes, key=lambda c: tgt[c] - self.value(c), reverse=True)
        for c in order:                           # 매수: 부족분 floor 주, 잔여현금 한도 내
            short = tgt[c] - self.value(c)
            if short <= 0 or self.R <= 0:
                continue
            afford = self.R / (1.0 + self.c[c])
            b = int(min(short, afford) // self.P[c])
            if b <= 0:
                continue
            ex = b * self.P[c]
            fee = ex * self.c[c]
            self.q[c] += b
            self.B[c] += ex
            self.R -= ex + fee
            self.cum_cost += fee

    def year_end_tax(self) -> float:
        """해외 상장 양도세 — 연간 통산 실현이익에 250만 공제 후 22% (일반계좌), 투자 버킷에서 차감."""
        if not (self.account and self.account["type"] == "general"):
            self.foreign_annual = 0.0
            return 0.0
        tax = max(0.0, self.foreign_annual - FOREIGN_ANNUAL_DEDUCTION) * TAX_FOREIGN_CGT
        self.foreign_annual = 0.0
        if tax > 0:
            self._deduct(tax)
            self.tax_cum += tax
        return tax

    def _deduct(self, amount: float):
        """세액 납부 — 연속: 전 자산 비례 축소(원가도 비례, 과세 없음) · 1주: 잔여현금 → 부족 시 최대 자산 매도(간소화)."""
        if self.lot:
            take = min(self.R, amount)
            self.R -= take
            rem = amount - take
            for c in sorted(self.codes, key=lambda k: -self.value(k)):     # 가치순 — 한 종목으로 부족하면 다음 종목
                if rem <= 0:
                    break
                if self.P[c] <= 0 or self.q[c] <= 0:
                    continue
                s = min(self.q[c], int(math.ceil(rem / self.P[c])))
                proceeds = s * self.P[c]
                self._ledger(c, self._realize(c, proceeds))
                self.q[c] -= s
                self.R += proceeds
                rem -= proceeds
            self.R = max(0.0, self.R - max(0.0, rem))                      # 전량 매도로도 부족하면 0 (연속 경로와 동일)
        else:
            V = self.total()
            if V <= 0:
                return
            f = max(0.0, 1.0 - amount / V)
            for c in self.codes:
                self.A[c] *= f
                self.B[c] *= f

    def isa_settlement(self) -> float:
        """ISA 만기 정산: 국내상장 기타 자산의 (실현 + 미실현) 순이익 통산 − 비과세 한도, 9.9%. 국내 주식형은 비과세."""
        if not (self.account and self.account["type"] == "isa"):
            return 0.0
        net = sum(self.realized_cum[c] + (self.value(c) - self.B[c])
                  for c in self.codes if self.tc[c] == "domestic_listed_other")
        return max(0.0, net - self.account["exempt"]) * ISA_TAX_RATE


@dataclass
class SimResult:
    trajectory: list
    cum_cost: float
    mdd_pct: float
    vol_annual_pct: float
    worst_month_pct: float
    max_drift_pct: float
    drift_after_rebalance: list
    fv_total: int
    fv_after_tax: int
    tax_realized_cum: float
    tax_terminal: float
    port_returns: list
    book: _Book
    safe_end: float


def _simulate(plan: Plan, period: int, *, contributions=None, lot: bool | None = None) -> SimResult:
    """
    월 루프 (계약 §4.2): 성장 → 납입 매수(M_t) → 드리프트 측정 → 리밸런싱(주기 도래) → (연말 양도세) → 기록.
    period = 0 이면 리밸런싱 없음. contributions/lot 은 ΔM 이분법·연장 탐색용 오버라이드.
    """
    n = plan.n
    M = plan.contributions if contributions is None else contributions
    use_lot = plan.lot if lot is None else lot
    codes = plan.codes
    book = _Book(plan, lot=use_lot and bool(codes))
    has_foreign = any(plan.tax_class.get(c) == "foreign_listed" for c in codes)
    general = bool(plan.account and plan.account["type"] == "general")

    # t = 0 — 시작 상태
    if codes:
        v0 = plan.cash * plan.inv_i
        if v0 > 0:
            book.buy_alloc(v0)
        if plan.holdings:
            book.place_holdings(plan.holdings)
    safe = plan.cash * plan.safe_i
    total_path = [book.total() + safe + book.R]

    trajectory, port_returns, drift_after = [], [], []
    max_drift = 0.0
    for t in range(1, n + 1):
        m_t = M[t - 1]
        if codes:
            w_pre = book.weights()
            r = {c: plan.returns[c][t - 1] for c in codes}
            book.grow(r)                                                    # 1. 성장
            port_returns.append(sum(w_pre[c] * r[c] for c in codes))
            book.buy_alloc(m_t * plan.inv_m)                                 # 2. 납입 매수
        safe = safe * (1.0 + plan.safe_rates[t - 1]) + m_t * plan.safe_m   # 안전 버킷 (§4.4)
        if codes:
            max_drift = max(max_drift, book.drift())                         # 3. 드리프트
            if period > 0 and t % period == 0:                               # 4. 리밸런싱
                book.rebalance()
                drift_after.append(book.drift())
            if general and has_foreign and (plan.cal[t] == 12 or t == n):    # 연말 양도세 (§4.6)
                book.year_end_tax()
        inv_r, safe_r, cash_r = round(book.total()), round(safe), round(book.R)
        trajectory.append({"month": t, "invest": inv_r, "safe": safe_r, "total": inv_r + safe_r + cash_r,
                           "contribution": int(m_t), "cash_residual": cash_r})
        total_path.append(book.total() + safe + book.R)

    # 위험 지표 (§4.4)
    peak, mdd = -math.inf, 0.0
    for v in total_path:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak)
    vol = statistics.stdev(port_returns) * math.sqrt(12.0) if len(port_returns) >= 2 else 0.0
    worst = min(port_returns) if port_returns else 0.0

    tax_terminal = book.isa_settlement() if codes else 0.0
    fv_total = trajectory[-1]["total"]
    fv_after_tax = fv_total - round(tax_terminal)
    return SimResult(trajectory=trajectory, cum_cost=book.cum_cost, mdd_pct=round(mdd * 100.0, 2),
                     vol_annual_pct=round(vol * 100.0, 2), worst_month_pct=round(worst * 100.0, 2),
                     max_drift_pct=round(max_drift * 100.0, 2), drift_after_rebalance=drift_after,
                     fv_total=fv_total, fv_after_tax=fv_after_tax, tax_realized_cum=book.tax_cum,
                     tax_terminal=tax_terminal, port_returns=port_returns, book=book, safe_end=safe)


# ---------------------------------------------------------------------------
# 4. 목표 간극 — ΔM 이분법 · 기간 연장 (§4.8)
# ---------------------------------------------------------------------------

def _fv_measure(sim: SimResult, after_tax: bool) -> int:
    return sim.fv_after_tax if after_tax else sim.fv_total


def extra_monthly_required(plan: Plan, period: int, goal: int, *, after_tax: bool):
    """f(ΔM) = FV(M_t + ΔM·1[t∈T*]) − goal 의 영점 (연속 모델). T* = 비상자금 충당 완료 이후 달."""
    filled = plan.cashflow.emergency_filled_month
    if filled is None:
        return None
    T = [t for t in range(1, plan.n + 1) if t > filled]
    if not T or plan.inv_m + plan.safe_m <= 0:
        return None

    def fv(dm):
        contrib = [m + (dm if t in Tset else 0) for t, m in zip(range(1, plan.n + 1), plan.contributions)]
        return _fv_measure(_simulate(plan, period, contributions=contrib, lot=False), after_tax)

    Tset = set(T)
    base = fv(0)
    if base >= goal:
        return 0
    # 계약 §4.8 개정 결정(2026-09-03, A안): 금액 상한 가드(`hi > goal`)를 삭제한다.
    # "목표보다 큰 ΔM 은 없다"는 잘못된 가정이라 참 해를 잘랐고(실험 H6), 절벽 위치가 2진 격자에 의존했다.
    # 무한 루프 방지는 확장 횟수 상한으로 한다 — 2^60 배까지 늘려도 미달이면 사실상 비수렴.
    lo, hi = 0.0, float(max(plan.contributions) + goal / len(T))
    for _ in range(BISECT_MAX_EXPAND):
        if fv(hi) >= goal:
            break
        hi *= 2.0
    else:
        return None
    for _ in range(BISECT_MAX_ITER):
        mid = (lo + hi) / 2.0
        if fv(mid) < goal:
            lo = mid
        else:
            hi = mid
        if hi - lo <= BISECT_TOL:
            break
    v = int(math.ceil(hi))
    return v - 1 if v > 0 and fv(v - 1) >= goal else v      # ceil 이 1원 넘길 수 있어 최소해로 되돌린다


def _surplus_headroom(plan: Plan):
    """T* 구간의 월 평균 여유자금 F_t (원). ΔM 실행 가능성의 분모.

    현금흐름 경로에서만 정의된다 — v0.2 상수 경로(`funds.monthly`)는 소득·지출을 받지 않아
    '얼마까지 낼 수 있는지'를 알 수 없으므로 None 이고, 실행 가능성을 판정하지 않는다(계약 §4.8 개정).
    """
    cf = plan.cashflow
    if not cf.surplus:
        return None
    filled = cf.emergency_filled_month
    if filled is None:
        return None
    T = [t for t in range(1, plan.n + 1) if t > filled]
    if not T:
        return None
    return round(sum(cf.surplus[t - 1] for t in T) / len(T))


def months_extension(inputs: dict, dataset: Dataset, plan: Plan, period: int, goal: int, *, after_tax: bool, cache: dict):
    """납입 유지 시 목표 도달 최소 개월 n′ — (months_extension, months_extension_raw, extension_status) 반환.

    계약 §4.8 개정(2026-09-03 결정): 공통 가용 구간 안에서 도달하더라도 n′ 이 입력 상한 HORIZON_RANGE[1] 을
    넘으면 사용자가 재제출할 수 없으므로 `months_extension` 은 null 로 둔다. 다만 값을 버리지 않고
    `months_extension_raw` 에 실어 화면이 "데이터상 N개월이면 도달하나 입력 가능 범위를 넘습니다"를 말할 수 있게 한다.
      OK                 도달, 재제출 가능
      BEYOND_INPUT_LIMIT 데이터 구간 안에서 도달하나 n′ > 상한 (raw 에 값 있음)
      BEYOND_DATA_WINDOW 공통 가용 구간 끝까지 미도달
      SERIES_NOT_AVAILABLE 연장 구간에 필요한 시계열 없음(replay 등) — _resolve 실패
    """
    if plan.codes:
        _, _, max_n = dataset.common_window(plan.codes)
    else:
        span = dataset.series_span("deposit")
        max_n = months_between(span[0], dataset.latest_month) + 1
    for n2 in range(plan.n + 1, max_n + 1):
        if n2 not in cache:
            try:
                cache[n2] = _resolve(inputs, dataset, horizon_override=n2, strict=False)
            except (ValidationError, _NoPlanFunds):
                cache[n2] = None
        p2 = cache[n2]
        if p2 is None:
            return None, None, "SERIES_NOT_AVAILABLE"
        if _fv_measure(_simulate(p2, period), after_tax) >= goal:
            ext = n2 - plan.n
            if n2 > HORIZON_RANGE[1]:
                return None, ext, "BEYOND_INPUT_LIMIT"
            return ext, ext, "OK"
    return None, None, "BEYOND_DATA_WINDOW"


# ---------------------------------------------------------------------------
# 5. 메인 진입점 (출력 계약 §5)
# ---------------------------------------------------------------------------

def analyze(inputs: dict, *, dataset: Dataset, now: datetime | None = None) -> dict:
    """
    - 검증 실패: ValidationError (.errors)
    - 계획 자금 0: {"status": "NO_PLAN_FUNDS", ...}
    - 정상: {"status": "OK", "meta", "derived", "cashflow", "per_period"}
    """
    errors = validate_static(inputs)
    if errors:
        raise ValidationError(errors)

    generated_at = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    try:
        plan = _resolve(inputs, dataset)
    except _NoPlanFunds:
        return {"status": "NO_PLAN_FUNDS",
                "message": "목표에 배정된 자금이 없습니다. 투자 또는 안전저축 배분을 확인하세요.",
                "meta": {"assumptions_version": ASSUMPTIONS_VERSION, "data_version": dataset.data_version,
                         "data_hash": dataset.data_hash, "generated_at": generated_at}}

    goal = inputs["goal"]["amount"]
    after_tax = plan.account is not None
    total_m = sum(plan.contributions)

    # 파생 지표 (§5 derived 재정의)
    sum_hold = sum(plan.holdings.values())
    total_in = plan.cash + sum_hold + total_m
    invest_in = sum_hold + plan.cash * plan.inv_i + total_m * plan.inv_m
    w_overall = 100.0 * invest_in / total_in if total_in > 0 else 0.0
    lo, hi = PROPENSITY_BOUNDS
    label = "안정형" if w_overall < lo else ("중립형" if w_overall <= hi else "공격형")
    plan_excluded = round(plan.cash * plan.other_i + total_m * plan.other_m + plan.locked_out)

    # 주기별 시뮬레이션
    per_period = {}
    ext_cache = {}
    headroom = _surplus_headroom(plan)            # 주기와 무관 — cashflow 블록에 싣고 gap 은 비율만 (계약 §4.8 개정)
    safe_only = not plan.codes
    shared = _simulate(plan, 0) if safe_only else None
    for pname, pmonths in PERIODS.items():
        sim = shared if safe_only else _simulate(plan, pmonths)
        fv = _fv_measure(sim, after_tax)
        shortfall = goal - fv
        if shortfall > 0:
            dm = extra_monthly_required(plan, 0 if safe_only else pmonths, goal, after_tax=after_tax)
            ext, ext_raw, ext_status = months_extension(inputs, dataset, plan, 0 if safe_only else pmonths,
                                                        goal, after_tax=after_tax, cache=ext_cache)
        else:
            dm, ext, ext_raw, ext_status = 0, 0, 0, "OK"

        # 계약 §4.8 개정 결정(2026-09-03): ΔM 정의는 "목표에 도달시키는 금액" 그대로 두고,
        # 실행 가능성은 별도 필드로 낸다 — ΔM 은 T* 의 여유자금 F_t 를 **넘어서** 더 넣어야 하는 돈이다.
        # 현금흐름 경로에서 M_t 는 이미 여유자금 전부이므로 ΔM > 0 은 "지금 없는 돈"을 뜻한다(실험 H4).
        ratio = None if (headroom is None or headroom <= 0 or not dm) else round(dm / headroom, 4)
        if shortfall <= 0:
            status = "already_met" if shortfall < 0 else "exact"
        elif ext_status == "BEYOND_DATA_WINDOW" and ratio is not None and ratio > 1.0:
            status = "unreachable"          # 기간으로도 금액으로도 확인 가능한 범위 밖
        else:
            status = "short"
        gap = {"fv_total": sim.fv_total, "shortfall": int(shortfall), "extra_monthly_required": dm,
               "months_extension": ext, "months_extension_raw": ext_raw, "extension_status": ext_status,
               "extra_monthly_ratio": ratio, "status": status,
               "basis": "after_tax" if after_tax else "pre_tax"}
        if plan.lot and plan.codes:                      # 1주 단위가 실제로 적용될 때만 병기 (안전 전용은 무관)
            gap["delta_m_model"] = "continuous"
        block = {
            "trajectory": sim.trajectory,
            "cum_cost": round(sim.cum_cost),
            "risk": {"mdd_pct": sim.mdd_pct, "vol_annual_pct": sim.vol_annual_pct,
                     "worst_month_pct": sim.worst_month_pct, "max_drift_pct": sim.max_drift_pct},
            "gap": gap,
        }
        if after_tax:
            block["tax"] = {"realized_cum": round(sim.tax_realized_cum), "fv_after_tax": sim.fv_after_tax}
        per_period[pname] = block

    # meta
    src = plan.cashflow.mode
    src_desc = {"none": "funds.monthly 상수", "summary": "사용자 요약값 기반 12개월 프로파일",
                "profile": "사용자 실적(가계부 CSV 집계) 12개월 프로파일"}[src]
    src_desc += " (기준 구간 임금지수·CPI 경로 재생)" if plan.growth_mode == "replay" else " (상수 반복)"
    safe_desc = ("기준 구간 정기예금 가중평균금리 평균 고정" if plan.safe_rate_mode == "fixed_avg"
                 else "기준 구간 정기예금 가중평균금리 월별 재생")
    data_basis = (f"실제 월간 총수익률 재생 · 기준 구간 {plan.window_start}~{plan.window_end} · 환노출(무헤지) · "
                  f"안전저축 연 {plan.safe_rate_avg_pct:.2f}%({safe_desc}) · 납입 경로: {src_desc}")
    if plan.lot:
        data_basis += " · 1주 단위 매수(TR 보정 합성가격)"
    if plan.account:
        data_basis += " · 세후 산출(" + ("일반계좌" if plan.account["type"] == "general" else "ISA") + ")"
    else:
        data_basis += " · 세금 제외"
    meta = {
        "assumptions_version": ASSUMPTIONS_VERSION,
        "data_version": dataset.data_version,
        "data_hash": dataset.data_hash,
        "window": {"start": plan.window_start, "end": plan.window_end, "months": plan.n},
        "start_month": plan.start_month,
        "target_month": plan.target_month,
        "assets_used": [dataset.asset_info(c) for c in plan.codes],
        "cashflow_source": src,
        "series_used": plan.series_used,
        "options": {"growth_mode": plan.growth_mode, "safe_rate_mode": plan.safe_rate_mode,
                    "lot_rounding": plan.lot,
                    "account": (None if plan.account is None else
                                {"type": plan.account["type"], "isa_exempt_limit": plan.account["exempt"]})},
        "safe_rate_annual_pct": plan.safe_rate_avg_pct,
        "data_basis": data_basis,
        "warnings": plan.warnings,
        "generated_at": generated_at,
    }
    return {
        "status": "OK",
        "meta": meta,
        "derived": {"propensity_label": label, "invest_share_overall_pct": round(w_overall, 1),
                    "plan_excluded_amount": plan_excluded},
        "cashflow": {**plan.cashflow.to_output(), "surplus_headroom": headroom},
        "per_period": per_period,
    }
