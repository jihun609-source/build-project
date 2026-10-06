"""AI 프로바이더 공통 인터페이스."""
from __future__ import annotations

from typing import Any


class ProviderError(Exception):
    """이 프로바이더로는 실패. fatal=True면 재요청하지 않고 바로 다음 프로바이더로."""

    def __init__(self, message: str, fatal: bool = False):
        super().__init__(message)
        self.fatal = fatal


class Provider:
    name = "base"

    def available(self) -> tuple[bool, str | None]:
        """(사용 가능 여부, 불가 사유)."""
        return True, None

    def generate(self, system: str, user: str, images: list[str], schema: dict[str, Any],
                 followups: list[tuple[str, str]] | None = None) -> str:
        """images: base64 JPEG 목록. followups: (직전 assistant 출력, 추가 user 지시) 목록.
        반환: 모델이 출력한 원문 텍스트 (JSON 기대)."""
        raise NotImplementedError

    @property
    def model(self) -> str:
        return ""
