"""공통 예외 — 검증 오류 구조 {code, field, message} (계약 v0.2 §2.2 유지)."""

from __future__ import annotations


class ValidationError(Exception):
    """검증 실패. .errors 에 [{code, field, message}] 목록."""

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("; ".join(e["code"] for e in self.errors))

    @property
    def codes(self):
        return {e["code"] for e in self.errors}


def err(code: str, field: str, message: str, **extra) -> dict:
    d = {"code": code, "field": field, "message": message}
    if extra:
        d.update(extra)
    return d
