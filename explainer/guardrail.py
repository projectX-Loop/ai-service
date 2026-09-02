"""가드레일 검증기 — KAN-12 산출물 ④ (C1~C10)의 구현.

LLM 응답을 신뢰하지 않고 심문하는 계층. 통과하지 못한 응답은 반환하지 않는다.

C1·C7·C9는 Pydantic이 파싱 단계에서 이미 강제하므로 여기서는 C2~C6, C8, C10을 다룬다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .schema import Explanation, SimulationInput

# ─────────────────────────────────────────── 결과 타입


@dataclass
class Violation:
    check: str      # C2 ~ C10
    severity: str   # ERROR | WARN
    detail: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.check}: {self.detail}"


@dataclass
class Report:
    violations: list[Violation]

    @property
    def passed(self) -> bool:
        return not any(v.severity == "ERROR" for v in self.violations)

    @property
    def errors(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == "ERROR"]

    @property
    def warnings(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == "WARN"]


# ─────────────────────────────────────────── JSON Pointer


def resolve_pointer(data: Any, pointer: str) -> Any:
    """RFC 6901 JSON Pointer. 경로가 없으면 KeyError."""
    if pointer in ("", "/"):
        return data
    if not pointer.startswith("/"):
        raise KeyError(f"'/'로 시작하지 않음: {pointer}")

    node = data
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            if not token.lstrip("-").isdigit():
                raise KeyError(f"배열 인덱스가 아님: {token} ({pointer})")
            idx = int(token)
            if not -len(node) <= idx < len(node):
                raise KeyError(f"인덱스 범위 초과: {token} ({pointer})")
            node = node[idx]
        elif isinstance(node, dict):
            if token not in node:
                raise KeyError(f"키 없음: {token} ({pointer})")
            node = node[token]
        else:
            raise KeyError(f"더 내려갈 수 없음: {token} ({pointer})")
    return node


# ─────────────────────────────────────────── 숫자 추출·대조

DATE_RE = re.compile(r"\d{4}-\d{2}(-\d{2})?")

# "4만 5천원" 같은 복합 표기
COMPOUND_RE = re.compile(r"(\d[\d,]*)\s*만\s*(\d[\d,]*)\s*천")
# 일반 숫자 + 단위
NUMBER_RE = re.compile(r"(-?\d[\d,]*\.?\d*)\s*(억원|억|만원|만|원|%|개월|회|배|p)?")

UNIT_SCALES = {
    "억": 100_000_000,
    "억원": 100_000_000,
    "만": 10_000,
    "만원": 10_000,
}


def _flatten_numbers(node: Any, out: set[float]) -> None:
    if isinstance(node, bool):
        return
    if isinstance(node, (int, float)):
        out.add(float(node))
    elif isinstance(node, dict):
        for v in node.values():
            _flatten_numbers(v, out)
    elif isinstance(node, list):
        out.add(float(len(node)))          # "세 주기", "3개" 같은 구조적 수치
        for v in node:
            _flatten_numbers(v, out)


def collect_input_numbers(data: Any) -> set[float]:
    """입력 JSON에 등장하는 모든 수치 + 표기 변형(절대값·백분율)."""
    raw: set[float] = set()
    _flatten_numbers(data, raw)

    candidates: set[float] = set()
    for v in raw:
        candidates.add(abs(v))
        candidates.add(abs(v) * 100)       # 0.057 → 5.7 (%)
        candidates.add(abs(v) / 10_000)    # 47150000 → 4715 (만원 표기)
    return candidates


def extract_numbers(text: str) -> list[tuple[str, float, bool]]:
    """텍스트에서 (원문, 정규화된 값, 단위있음) 목록.

    날짜는 제외한다. 단위가 붙은 수치는 엄격 검사, 맨숫자는 경고 수준으로 다룬다.
    """
    cleaned = DATE_RE.sub(" ", text)
    found: list[tuple[str, float, bool]] = []

    for m in COMPOUND_RE.finditer(cleaned):
        man = float(m.group(1).replace(",", ""))
        chun = float(m.group(2).replace(",", ""))
        found.append((m.group(0), man * 10_000 + chun * 1_000, True))
    cleaned = COMPOUND_RE.sub(" ", cleaned)

    for m in NUMBER_RE.finditer(cleaned):
        token = m.group(1)
        if token in ("-", "", "."):
            continue
        unit = m.group(2)
        value = abs(float(token.replace(",", "")))
        if unit in UNIT_SCALES:
            value *= UNIT_SCALES[unit]
        found.append((m.group(0).strip(), value, unit is not None))
    return found


def matches_any(value: float, candidates: set[float]) -> bool:
    """반올림 표기를 허용하는 대조. 0.5% 상대오차 + 작은 수용 절대오차."""
    for c in candidates:
        if abs(value - c) <= max(0.05, abs(c) * 0.005):
            return True
    return False


# ─────────────────────────────────────────── 금지 표현 (C5)

PROBABILITY_RE = re.compile(r"확률|가능성이\s*(높|낮)|반드시|확실")
FUTURE_RE = re.compile(r"(것입니다|겁니다|될\s*것|예상됩니다|전망)")
RETURN_WORD_RE = re.compile(r"수익|수익률|이익|오를|상승")
PRODUCT_RE = re.compile(
    r"(KODEX|TIGER|KBSTAR|ARIRANG|SOL|ACE|S&P\s?500|나스닥|코스피|채권형\s*펀드)"
    r"[^.!?\n]{0,25}?(매수|매도|편입|갈아타|비중을?\s*(늘|줄))"
)
LURE_RE = re.compile(r"레버리지|신용거래|대출|자동매매|가입하|청약")
FORECAST_RE = re.compile(r"(내년|향후|앞으로|다음\s*해).{0,20}(금리|물가|경기|시장|증시)")

SENTENCE_SPLIT = re.compile(r"[.!?\n]")


def check_forbidden(text: str) -> list[tuple[str, str]]:
    """(사유, 걸린 문장) 목록."""
    hits: list[tuple[str, str]] = []
    for sentence in SENTENCE_SPLIT.split(text):
        s = sentence.strip()
        if not s:
            continue
        if PROBABILITY_RE.search(s):
            hits.append(("목표 달성 확률·단정 표현", s))
        if FUTURE_RE.search(s) and RETURN_WORD_RE.search(s):
            hits.append(("확정적 미래 수익 표현", s))
        if PRODUCT_RE.search(s):
            hits.append(("특정 상품 매수·매도 권유", s))
        if LURE_RE.search(s):
            hits.append(("자동매매·대출·상품 가입 유도", s))
        if FORECAST_RE.search(s):
            hits.append(("시장 전망 제시", s))
    return hits


# ─────────────────────────────────────────── 본 검사


def _claims(exp: Explanation) -> list[tuple[str, str, list[str]]]:
    """(위치, 텍스트, evidence) 평탄화."""
    items: list[tuple[str, str, list[str]]] = [
        ("summary", exp.summary.text, exp.summary.evidence),
        ("goal_gap", exp.goal_gap.text, exp.goal_gap.evidence),
    ]
    for i, fc in enumerate(exp.frequency_comparison):
        items.append((f"frequency_comparison[{i}].observation", fc.observation, fc.evidence))
        items.append((f"frequency_comparison[{i}].tradeoff", fc.tradeoff, fc.evidence))
    for i, rf in enumerate(exp.risk_factors):
        items.append((f"risk_factors[{i}]", f"{rf.title} {rf.detail}", rf.evidence))
    for i, na in enumerate(exp.next_actions):
        items.append((f"next_actions[{i}]", na.text, na.evidence))
    return items


def validate(exp: Explanation, source: SimulationInput | dict) -> Report:
    data = source.model_dump(mode="json") if isinstance(source, SimulationInput) else source
    candidates = collect_input_numbers(data)
    v: list[Violation] = []
    claims = _claims(exp)

    # C2 — 모든 텍스트 필드에 evidence가 있는가
    for where, text, ev in claims:
        if not ev:
            v.append(Violation("C2", "ERROR", f"{where}: evidence가 비어 있음"))

    # C3 — evidence의 JSON Pointer가 입력에 실제로 존재하는가
    for where, _text, ev in claims:
        for pointer in ev:
            try:
                resolve_pointer(data, pointer)
            except KeyError as e:
                v.append(Violation("C3", "ERROR", f"{where}: 존재하지 않는 경로 {e}"))

    # C4 — 텍스트의 숫자가 입력에서 재현 가능한가 (환각 탐지)
    for where, text, _ev in claims:
        for raw, value, has_unit in extract_numbers(text):
            if matches_any(value, candidates):
                continue
            # 단위 없는 작은 정수(1~12)만 경고로 낮춘다. 순번·개수일 가능성이 있어서다.
            # 0.85 같은 소수는 지표 환각일 확률이 높으므로 단위가 없어도 ERROR.
            is_small_ordinal = (not has_unit) and value.is_integer() and 1 <= value <= 12
            sev = "WARN" if is_small_ordinal else "ERROR"
            v.append(
                Violation("C4", sev, f"{where}: 입력에 없는 수치 '{raw}' (해석값 {value:,.4g})")
            )

    # C5 — 금지 표현
    for where, text, _ev in claims:
        for reason, sentence in check_forbidden(text):
            v.append(Violation("C5", "ERROR", f"{where}: {reason} — \"{sentence}\""))

    # C6 — frequency_comparison이 세 주기를 중복 없이 다루는가
    freqs = [fc.frequency for fc in exp.frequency_comparison]
    if len(set(freqs)) != 3:
        v.append(Violation("C6", "ERROR", f"주기 중복 또는 누락: {[f.value for f in freqs]}"))

    # C8 — data_basis가 입력 meta와 일치하는가
    meta = data["meta"]
    expected_period = f"{meta['data_period']['start']} ~ {meta['data_period']['end']}"
    if exp.data_basis.period.replace(" ", "") != expected_period.replace(" ", ""):
        v.append(
            Violation("C8", "ERROR",
                      f"data_basis.period 불일치: '{exp.data_basis.period}' != '{expected_period}'")
        )
    if list(exp.data_basis.assumptions) != list(meta["assumptions"]):
        v.append(Violation("C8", "ERROR", "data_basis.assumptions가 meta와 다름"))
    if not exp.data_basis.disclaimer.strip():
        v.append(Violation("C8", "ERROR", "투자 유의 문구가 비어 있음"))

    # C10 — 선택된 주기 반영 (PRD 수용기준 4)
    # KAN-9의 rebalancing.focus는 선택 필드다. 없으면 강조할 주기가 없다는 뜻이므로
    # 검증을 건너뛰되, PRD 수용기준 4를 못 지킨 상태임을 경고로 남긴다.
    selected = data.get("selected_frequency")
    if selected is None:
        v.append(
            Violation("C10", "WARN",
                      "selected_frequency(=KAN-9 rebalancing.focus)가 없어 강조 주기 검증을 건너뜀")
        )
    else:
        if exp.highlighted_frequency.value != selected:
            v.append(
                Violation("C10", "ERROR",
                          f"highlighted_frequency={exp.highlighted_frequency.value} != selected_frequency={selected}")
            )
        label = {"MONTHLY": "월", "QUARTERLY": "분기", "SEMIANNUAL": "반기"}[selected]
        head = exp.summary.text + " " + exp.goal_gap.text
        if label not in head:
            v.append(Violation("C10", "ERROR", f"선택된 주기('{label}별')가 요약·간극 설명에 언급되지 않음"))

    return Report(v)
