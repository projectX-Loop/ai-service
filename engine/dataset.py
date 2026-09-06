"""
dataset.py — 스냅샷 로더 (KAN-11 엔진 v0.3 · 계약 v0.2 §1 / v0.3 §1.2)

- 표준 라이브러리만 사용(의존성 0). 엔진은 이 객체를 **주입**받는다 — 네트워크·파일 직접 접근 없음.
- 스냅샷 디렉터리 구성 (엔진용 파일 → data_hash 통합 대상):
    monthly_returns.csv        month, <code>...            자산 월간 총수익률(TR, 소수)    ← 필수
    asset_catalog.json         [ {code, display_name, instrument, cost, tax_class, lot_size, price_anchor, ...} ]  ← 필수
    deposit_rate_monthly.csv   month, deposit_rate_pct     정기예금 가중평균금리(연율 %)   ← 필수 (§1.4: 항상 사용)
    cpi_monthly.csv            month, cpi                  소비자물가지수                  ← 선택 (2층 replay)
    wage_index_monthly.csv     month, wage_total_krw       상용근로자 월평균 임금총액(원)  ← 선택 (2층 replay)
    SNAPSHOT.json              data_version, data_hash, latest_month, files{...}, sources...  (해시 대상 아님)
- 결측 규칙: 각 시계열은 첫 관측월~마지막 관측월 사이에 빈 달이 없어야 한다(보간 금지, 발견 시 로드 실패).
- latest_month(최신 확정월): 스냅샷이 선언하고 로더가 검증한다 — 정기예금 시계열이 그 달을 덮어야 함.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import statistics

ENGINE_FILES = [
    "monthly_returns.csv",
    "asset_catalog.json",
    "deposit_rate_monthly.csv",
    "cpi_monthly.csv",
    "wage_index_monthly.csv",
]
REQUIRED_FILES = ["monthly_returns.csv", "asset_catalog.json", "deposit_rate_monthly.csv"]
SNAPSHOT_META = "SNAPSHOT.json"

_YM = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class DatasetError(Exception):
    """스냅샷 로드·정합성 실패 (해시 불일치, 결측, 형식 오류)."""


# ---------------------------------------------------------------------------
# 월(YYYY-MM) 산술
# ---------------------------------------------------------------------------

def is_month(s) -> bool:
    return isinstance(s, str) and bool(_YM.match(s))


def month_index(ym: str) -> int:
    y, m = ym.split("-")
    return int(y) * 12 + int(m) - 1


def index_month(i: int) -> str:
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def add_months(ym: str, k: int) -> str:
    return index_month(month_index(ym) + k)


def months_between(a: str, b: str) -> int:
    """b − a (개월). a == b 이면 0."""
    return month_index(b) - month_index(a)


def month_of(ym: str) -> int:
    """달력 월 1..12"""
    return int(ym[5:7])


# ---------------------------------------------------------------------------
# 해시
# ---------------------------------------------------------------------------

def sha256_bytes(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


def file_sha256(path: str) -> str:
    with open(path, "rb") as f:
        return sha256_bytes(f.read())


def combined_hash(dir_path: str, files=ENGINE_FILES) -> str:
    """엔진용 파일을 고정 순서로 이어 붙인 바이트의 SHA-256 (계약 v0.3 §1.2). 없는 선택 파일은 건너뜀."""
    h = hashlib.sha256()
    for name in files:
        p = os.path.join(dir_path, name)
        if os.path.exists(p):
            with open(p, "rb") as f:
                h.update(f.read())
    return "sha256:" + h.hexdigest()


# ---------------------------------------------------------------------------
# 시계열 유틸
# ---------------------------------------------------------------------------

def _check_contiguous(name: str, series: dict) -> None:
    """첫 관측~마지막 관측 사이 빈 달 금지."""
    if not series:
        return
    months = sorted(series)
    first, last = months[0], months[-1]
    expected = months_between(first, last) + 1
    if len(months) != expected:
        have = set(months)
        missing = [index_month(i) for i in range(month_index(first), month_index(last) + 1)
                   if index_month(i) not in have]
        raise DatasetError(f"{name}: 구간 내 결측 {len(missing)}개월 (예: {missing[:5]}) — 보간 금지, 수집 단계에서 확인")


def _read_csv_series(path: str, value_col: str) -> dict:
    out = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        if rd.fieldnames is None or "month" not in rd.fieldnames or value_col not in rd.fieldnames:
            raise DatasetError(f"{os.path.basename(path)}: 열 구성이 'month,{value_col}'이 아님 ({rd.fieldnames})")
        for row in rd:
            m = (row.get("month") or "").strip()
            v = (row.get(value_col) or "").strip()
            if not m:
                continue
            if not is_month(m):
                raise DatasetError(f"{os.path.basename(path)}: 월 형식 오류 '{m}'")
            if v == "":
                continue
            try:
                out[m] = float(v)
            except ValueError as e:
                raise DatasetError(f"{os.path.basename(path)}: 숫자 아님 {m}={v!r}") from e
    return out


def _read_returns_csv(path: str) -> dict:
    out = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        if rd.fieldnames is None or rd.fieldnames[0] != "month":
            raise DatasetError("monthly_returns.csv: 첫 열은 month 여야 함")
        codes = [c for c in rd.fieldnames[1:] if c]
        for c in codes:
            out[c] = {}
        for row in rd:
            m = (row.get("month") or "").strip()
            if not m:
                continue
            if not is_month(m):
                raise DatasetError(f"monthly_returns.csv: 월 형식 오류 '{m}'")
            for c in codes:
                v = (row.get(c) or "").strip()
                if v == "":
                    continue
                try:
                    out[c][m] = float(v)
                except ValueError as e:
                    raise DatasetError(f"monthly_returns.csv: 숫자 아님 {c}@{m}={v!r}") from e
    return out


def _normalize_catalog(raw) -> dict:
    """리스트 또는 dict → {code: meta}. 필수 키 검사."""
    items = list(raw.values()) if isinstance(raw, dict) and raw and all(isinstance(v, dict) for v in raw.values()) else raw
    cat = {}
    for it in items:
        if not isinstance(it, dict) or "code" not in it:
            raise DatasetError("asset_catalog.json: 항목에 code 없음")
        code = it["code"]
        if code in cat:
            raise DatasetError(f"asset_catalog.json: 코드 중복 {code}")
        meta = dict(it)
        meta.setdefault("display_name", code)
        meta.setdefault("instrument", code)
        meta.setdefault("total_return", True)
        cost = meta.get("cost") or {}
        meta["cost"] = {
            "commission_one_way": float(cost.get("commission_one_way", 0.0005)),
            "fx_spread_one_way": float(cost.get("fx_spread_one_way", 0.0)),
        }
        meta.setdefault("tax_class", "domestic_listed_other")
        if meta["tax_class"] not in ("domestic_equity", "domestic_listed_other", "foreign_listed"):
            raise DatasetError(f"asset_catalog.json: {code} tax_class 값 오류 {meta['tax_class']!r}")
        meta["lot_size"] = int(meta.get("lot_size") or 1)
        pa = meta.get("price_anchor")
        if pa is not None:
            if not (isinstance(pa, dict) and is_month(pa.get("month", "")) and pa.get("price_krw") is not None):
                raise DatasetError(f"asset_catalog.json: {code} price_anchor 형식 오류")
        cat[code] = meta
    return cat


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------

class Dataset:
    """스냅샷 한 벌 (불변 취급). 엔진은 이 객체만 본다."""

    def __init__(self, *, data_version: str, data_hash: str, latest_month: str, catalog: dict,
                 returns: dict, deposit_rate: dict, cpi: dict | None = None, wage: dict | None = None,
                 meta: dict | None = None):
        if not is_month(latest_month):
            raise DatasetError(f"latest_month 형식 오류 {latest_month!r}")
        self.data_version = data_version
        self.data_hash = data_hash
        self.latest_month = latest_month
        self.catalog = catalog
        self.returns = {c: dict(s) for c, s in returns.items()}
        self.deposit_rate = dict(deposit_rate)
        self.cpi = dict(cpi) if cpi else None
        self.wage = dict(wage) if wage else None
        self.meta = meta or {}

        for c, s in self.returns.items():
            _check_contiguous(f"monthly_returns[{c}]", s)
        _check_contiguous("deposit_rate_monthly", self.deposit_rate)
        if self.cpi:
            _check_contiguous("cpi_monthly", self.cpi)
        if self.wage:
            _check_contiguous("wage_index_monthly", self.wage)
        if latest_month not in self.deposit_rate:
            raise DatasetError(f"deposit_rate_monthly가 최신 확정월 {latest_month}을 덮지 않음 (§1.4: 항상 사용 시계열)")
        for c in self.catalog:
            if c not in self.returns or not self.returns[c]:
                raise DatasetError(f"카탈로그 자산 {c}의 수익률 열이 monthly_returns.csv에 없음")
        self._ma12_cache = {}
        self._price_cache = {}

    # ------------------------------------------------------------- 생성자

    @classmethod
    def load(cls, dir_path: str, *, verify_hash: bool = True) -> "Dataset":
        for name in REQUIRED_FILES + [SNAPSHOT_META]:
            if not os.path.exists(os.path.join(dir_path, name)):
                raise DatasetError(f"스냅샷 파일 없음: {name}")
        with open(os.path.join(dir_path, SNAPSHOT_META), encoding="utf-8") as f:
            meta = json.load(f)
        for k in ("data_version", "data_hash", "latest_month"):
            if k not in meta:
                raise DatasetError(f"SNAPSHOT.json에 {k} 없음")
        if verify_hash:
            actual = combined_hash(dir_path)
            if actual != meta["data_hash"]:
                raise DatasetError(f"data_hash 불일치: SNAPSHOT.json {meta['data_hash']} ≠ 실제 {actual} (스냅샷 변조·손상)")
            for name, h in (meta.get("files") or {}).items():
                p = os.path.join(dir_path, name)
                if os.path.exists(p) and file_sha256(p) != h:
                    raise DatasetError(f"파일 해시 불일치: {name}")
        with open(os.path.join(dir_path, "asset_catalog.json"), encoding="utf-8") as f:
            catalog = _normalize_catalog(json.load(f))
        returns = _read_returns_csv(os.path.join(dir_path, "monthly_returns.csv"))
        deposit = _read_csv_series(os.path.join(dir_path, "deposit_rate_monthly.csv"), "deposit_rate_pct")
        cpi = wage = None
        p = os.path.join(dir_path, "cpi_monthly.csv")
        if os.path.exists(p):
            cpi = _read_csv_series(p, "cpi")
        p = os.path.join(dir_path, "wage_index_monthly.csv")
        if os.path.exists(p):
            wage = _read_csv_series(p, "wage_total_krw")
        return cls(data_version=meta["data_version"], data_hash=meta["data_hash"], latest_month=meta["latest_month"],
                   catalog=catalog, returns=returns, deposit_rate=deposit, cpi=cpi, wage=wage, meta=meta)

    @classmethod
    def synthetic(cls, returns: dict, *, catalog: dict | None = None, deposit_rate=3.0, cpi=None, wage=None,
                  latest_month: str | None = None, data_version: str = "synthetic", history_pad: int = 12) -> "Dataset":
        """
        테스트용 인메모리 스냅샷.
          returns: {code: {month: r}}  또는 {code: (start_month, [r_1, ...])}
          deposit_rate: 상수(연율 %) 또는 {month: %}. 상수면 (최초 수익률월 − history_pad) ~ latest 전 구간에 채움.
          cpi/wage: None | 상수 | {month: v}. 상수면 같은 구간에 상수로 채움(= 재생 계수 1.0).
          catalog: None이면 자동 생성 (수수료 0.05%, tax_class domestic_equity, price_anchor = 첫 수익률월 직전 월, 10,000원).
        """
        norm = {}
        for code, s in returns.items():
            if isinstance(s, dict):
                norm[code] = {m: float(v) for m, v in s.items()}
            else:
                start, vals = s
                norm[code] = {add_months(start, i): float(v) for i, v in enumerate(vals)}
        first = min(min(s) for s in norm.values())
        last = max(max(s) for s in norm.values())
        latest = latest_month or last
        span = [index_month(i) for i in range(month_index(first) - history_pad, month_index(latest) + 1)]

        def fill(x):
            if x is None:
                return None
            if isinstance(x, dict):
                return {m: float(v) for m, v in x.items()}
            return {m: float(x) for m in span}

        cat = {}
        for code in norm:
            base = (catalog or {}).get(code, {})
            meta = {"code": code, "display_name": base.get("display_name", code), "instrument": base.get("instrument", code),
                    "source": "synthetic", "currency": "KRW", "fx_convert": None, "total_return": True,
                    "history_start": min(norm[code]), "group": "test", "license_note": "synthetic",
                    "cost": base.get("cost", {"commission_one_way": 0.0005, "fx_spread_one_way": 0.0}),
                    "tax_class": base.get("tax_class", "domestic_equity"), "lot_size": base.get("lot_size", 1),
                    "price_anchor": base.get("price_anchor", {"month": add_months(min(norm[code]), -1), "price_krw": 10000.0})}
            for k, v in base.items():
                meta.setdefault(k, v)
            cat[code] = meta
        cat = _normalize_catalog(list(cat.values()))
        return cls(data_version=data_version, data_hash="sha256:synthetic", latest_month=latest, catalog=cat,
                   returns=norm, deposit_rate=fill(deposit_rate), cpi=fill(cpi), wage=fill(wage))

    # ------------------------------------------------------------- 조회

    @property
    def codes(self):
        return list(self.catalog)

    @property
    def series_available(self) -> list:
        s = ["monthly_returns", "deposit_rate_monthly"]
        if self.cpi:
            s.append("cpi_monthly")
        if self.wage:
            s.append("wage_index_monthly")
        return s

    def first_return_month(self, code: str) -> str:
        return min(self.returns[code])

    def last_return_month(self, code: str) -> str:
        return min(max(self.returns[code]), self.latest_month)

    def common_window(self, codes) -> tuple:
        """(first, last, n) — 선택 자산 이력의 교집합 ∩ [.., latest_month]. 비어 있으면 n=0."""
        codes = list(codes)
        if not codes:
            return (self.latest_month, self.latest_month, 0)
        first = max(self.first_return_month(c) for c in codes)
        last = min(self.last_return_month(c) for c in codes)
        n = months_between(first, last) + 1
        return (first, last, max(n, 0))

    def baseline_window(self, codes, n: int):
        """계약 §3.1: [latest − n + 1, latest]. 공통 구간 부족 시 None."""
        first, last, avail = self.common_window(codes)
        if n > avail or n <= 0:
            return None
        return (add_months(last, -(n - 1)), last)

    def returns_series(self, codes, start: str, n: int) -> dict:
        out = {}
        for c in codes:
            s = self.returns[c]
            vals = []
            for t in range(n):
                m = add_months(start, t)
                if m not in s:
                    raise DatasetError(f"{c}: {m} 수익률 없음")
                vals.append(s[m])
            out[c] = vals
        return out

    def series_covers(self, name: str, start: str, end: str) -> bool:
        s = {"deposit": self.deposit_rate, "cpi": self.cpi, "wage": self.wage}[name]
        if not s:
            return False
        return all(add_months(start, t) in s for t in range(months_between(start, end) + 1))

    def series_span(self, name: str):
        s = {"deposit": self.deposit_rate, "cpi": self.cpi, "wage": self.wage}[name]
        if not s:
            return None
        return (min(s), max(s))

    def deposit_series(self, start: str, n: int) -> list:
        """기준 구간 월별 정기예금 금리(연율 %)."""
        out = []
        for t in range(n):
            m = add_months(start, t)
            if m not in self.deposit_rate:
                raise DatasetError(f"deposit_rate_monthly: {m} 없음")
            out.append(self.deposit_rate[m])
        return out

    def deposit_window_mean(self, start: str, n: int) -> float:
        """r̄ = 기준 구간 월별 연율(%)의 산술평균, 소수 6자리 반올림 (계약 v0.3 §1.4)."""
        return round(statistics.fmean(self.deposit_series(start, n)), 6)

    def ma12(self, name: str, month: str):
        """후행 12개월 이동평균 (month−11 … month). 하나라도 없으면 None."""
        key = (name, month)
        if key in self._ma12_cache:
            return self._ma12_cache[key]
        s = {"cpi": self.cpi, "wage": self.wage}[name]
        val = None
        if s:
            vals = []
            for k in range(11, -1, -1):
                m = add_months(month, -k)
                if m not in s:
                    vals = None
                    break
                vals.append(s[m])
            if vals:
                val = statistics.fmean(vals)
        self._ma12_cache[key] = val
        return val

    def synthetic_price(self, code: str, month: str):
        """
        TR 보정 합성가격 P̃ (계약 v0.3 §4.5):  P̃(anchor) = price_anchor.price_krw,  P̃(m) = P̃(m−1) × (1 + r_m).
        anchor 이전이거나 anchor+1..month 사이 수익률이 없으면 None.
        """
        key = (code, month)
        if key in self._price_cache:
            return self._price_cache[key]
        meta = self.catalog.get(code) or {}
        pa = meta.get("price_anchor")
        val = None
        if pa:
            am = pa["month"]
            if month == am:
                val = float(pa["price_krw"])
            elif month_index(month) > month_index(am):
                prev = self.synthetic_price(code, add_months(month, -1))
                r = self.returns[code].get(month)
                if prev is not None and r is not None:
                    val = prev * (1.0 + r)
        self._price_cache[key] = val
        return val

    def asset_info(self, code: str) -> dict:
        m = self.catalog[code]
        return {"code": code, "display_name": m.get("display_name"), "instrument": m.get("instrument"),
                "tax_class": m.get("tax_class"), "history": [self.first_return_month(code), self.last_return_month(code)]}
