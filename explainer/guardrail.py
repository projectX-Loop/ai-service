"""가드레일 검증기 — KAN-12 산출물 ④ 의 구현. KAN-9 §5·§7 정렬본 (2026-09-02).

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

# KAN-9 §7 규칙 4 — 파생 성향 라벨을 인격 단정으로 확장
PERSONA_RE = re.compile(r"(안정형|중립형|공격형|보수적|공격적|안정적)[^.!?\n]{0,12}(사람|성격|성향의 분|투자자이|타입이|분이시|이시네요|이시군요)")
# KAN-9 §7 규칙 7 — 지출·소비 습관 평가·훈계
LECTURE_RE = re.compile(r"낭비|과소비|씀씀이|헤프|절약하세요|아끼세요|습관을\s*고치|줄이셔야\s*합니다")
# KAN-9 §7 규칙 5 — 과거 재현을 미래 예측으로 서술. 조건절이 있으면 허용
FUTURE_CLAIM_RE = re.compile(r"(\d+\s*년\s*(뒤|후)|만기\s*(에|시)|미래에|앞으로)[^.!?\n]{0,30}(됩니다|될\s*것|도달합니다|도달할|모입니다|만들어집니다)")
CONDITIONAL_RE = re.compile(r"반복된다면|그대로라면|가정하면|재현|기준 구간|같은 흐름|과거 구간|반복될 경우|재생")

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
        if PERSONA_RE.search(s):
            hits.append(("성향 라벨의 인격 단정 (KAN-9 규칙 4)", s))
        if LECTURE_RE.search(s):
            hits.append(("지출·소비 습관 평가·훈계 (KAN-9 규칙 7)", s))
    return hits


def check_future_claims(text: str) -> list[str]:
    """KAN-9 규칙 5 — 조건절 없는 미래 단정 문장 목록."""
    bad = []
    for sentence in SENTENCE_SPLIT.split(text):
        s = sentence.strip()
        if s and FUTURE_CLAIM_RE.search(s) and not CONDITIONAL_RE.search(s):
            bad.append(s)
    return bad


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


# ─────────────────────────────────────────── 본 검사


def _claims(exp: Explanation) -> list[tuple[str, str, list[str]]]:
    """(위치, 텍스트, evidence) 평탄화."""
    items: list[tuple[str, str, list[str]]] = [
        ("summary", exp.summary.text, exp.summary.evidence),
        ("assumptions_note", exp.assumptions_note.text, exp.assumptions_note.evidence),
    ]
    for p, pc in exp.per_period_pros_cons.items():
        for i, c in enumerate(pc.pros):
            items.append((f"per_period_pros_cons[{p.value}].pros[{i}]", c.text, c.evidence))
        for i, c in enumerate(pc.cons):
            items.append((f"per_period_pros_cons[{p.value}].cons[{i}]", c.text, c.evidence))
    for i, r in enumerate(exp.risks):
        items.append((f"risks[{i}]", f"{r.title} {r.detail}", r.evidence))
    for i, a in enumerate(exp.next_actions):
        items.append((f"next_actions[{i}]", a.text, a.evidence))
    return items


def _window_mentioned(text: str, window: dict) -> bool:
    """기준 구간이 언급됐는가 — 시작/끝 연월, 연도, 또는 '기준 구간'이라는 말."""
    start, end = str(window.get("start", "")), str(window.get("end", ""))
    keys = {start, end, start[:4], end[:4], "기준 구간"} - {""}
    return any(k in text for k in keys)


def validate(exp: Explanation, source: SimulationInput | dict,
             chunk_exists=None) -> Report:
    """chunk_exists: Callable[[str], bool] — KAN-17에서 DB 조회를 주입. None이면 형식만 검사."""
    data = source.model_dump(mode="json") if isinstance(source, SimulationInput) else source
    candidates = collect_input_numbers(data)
    v: list[Violation] = []
    claims = _claims(exp)
    window = data.get("meta", {}).get("window", {})

    # C2 — 모든 텍스트 필드에 evidence가 있는가
    for where, text, ev in claims:
        if not ev:
            v.append(Violation("C2", "ERROR", f"{where}: evidence가 비어 있음"))

    # C3 — evidence가 실제로 존재하는가 (JSON Pointer 또는 chunk 참조)
    chunk_refs: set[str] = set()
    for where, _text, ev in claims:
        for e in ev:
            if e.startswith("chunk:"):
                ref = e[len("chunk:"):]
                chunk_refs.add(ref)
                if "#" not in ref:
                    v.append(Violation("C3", "ERROR", f"{where}: 청크 참조 형식 오류 '{e}' (source_id#idx)"))
                elif chunk_exists is not None and not chunk_exists(ref):
                    v.append(Violation("C3", "ERROR", f"{where}: 존재하지 않는 청크 {e}"))
                continue
            try:
                resolve_pointer(data, e)
            except (KeyError, IndexError, ValueError) as err:
                v.append(Violation("C3", "ERROR", f"{where}: 존재하지 않는 경로 {err}"))

    # C4 — 텍스트의 숫자가 입력에서 재현 가능한가 (환각 탐지)
    for where, text, ev in claims:
        for raw, value, has_unit in extract_numbers(text):
            if matches_any(value, candidates):
                continue
            is_small_ordinal = (not has_unit) and value.is_integer() and 1 <= value <= 12
            sev = "WARN" if is_small_ordinal else "ERROR"
            v.append(Violation("C4", sev, f"{where}: 입력에 없는 수치 '{raw}' (해석값 {value:,.4g})"))

    # C13 — 청크를 근거로 든 문장에는 수치를 쓸 수 없다 (청크는 개념 설명 전용)
    for where, text, ev in claims:
        if any(e.startswith("chunk:") for e in ev) and not any(not e.startswith("chunk:") for e in ev):
            nums = [raw for raw, _v, _u in extract_numbers(text)]
            if nums:
                v.append(Violation("C13", "ERROR", f"{where}: 청크만 근거인 문장에 수치 {nums} — 수치는 계산 결과에서만"))

    # C5 — 금지 표현 (KAN-9 규칙 1·2·3·4·7)
    for where, text, _ev in claims:
        for reason, sentence in check_forbidden(text):
            v.append(Violation("C5", "ERROR", f"{where}: {reason} — \"{sentence}\""))

    # C12 — 과거 재현을 미래 예측으로 서술 (KAN-9 규칙 5). 조건절 없는 미래 단정
    for where, text, _ev in claims:
        for s in check_future_claims(text):
            v.append(Violation("C12", "ERROR", f"{where}: 조건절 없는 미래 단정 (규칙 5) — \"{s}\""))

    # C6 — 세 주기 장단점이 전부 있는가
    got = {p.value for p in exp.per_period_pros_cons}
    if got != {"M", "Q", "H"}:
        v.append(Violation("C6", "ERROR", f"per_period_pros_cons 주기 누락/초과: {sorted(got)}"))

    # C8 — assumptions_note가 기준 구간·환노출·data_basis를 담는가
    an = exp.assumptions_note.text
    if not _window_mentioned(an, window):
        v.append(Violation("C8", "ERROR", "assumptions_note에 기준 구간(meta.window)이 없음"))
    if "환" not in an:
        v.append(Violation("C8", "WARN", "assumptions_note에 환노출 언급 없음 (KAN-9 §7)"))
    if not an.strip():
        v.append(Violation("C8", "ERROR", "assumptions_note가 비어 있음"))

    # C11 — summary에 기준 구간 언급 (KAN-9 규칙 6: 조건절 없는 금액 서술 금지)
    if not _window_mentioned(exp.summary.text, window):
        v.append(Violation("C11", "ERROR", "summary에 기준 구간 언급 없음 (규칙 6)"))

    # C10 — 선택된 주기 반영 (PRD 수용기준 4). focus는 KAN-9 §5에 없어 backend가 함께 넘긴다
    focus = data.get("focus")
    if focus is None:
        v.append(Violation("C10", "WARN", "focus 없음 — 강조 주기 검증 건너뜀"))
    else:
        hp = exp.highlighted_period.value if exp.highlighted_period else None
        if hp != focus:
            v.append(Violation("C10", "ERROR", f"highlighted_period={hp} != focus={focus}"))
        label = {"M": "월", "Q": "분기", "H": "반기"}[focus]
        if label not in exp.summary.text:
            v.append(Violation("C10", "ERROR", f"선택된 주기('{label}별')가 summary에 언급되지 않음"))

    # retrieved_refs — 문장별 chunk evidence의 합집합과 일치하는가
    if set(exp.retrieved_refs) != chunk_refs:
        v.append(Violation("C3", "WARN", f"retrieved_refs {sorted(exp.retrieved_refs)} != evidence 청크 합집합 {sorted(chunk_refs)}"))

    return Report(v)
