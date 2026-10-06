"""시간 규칙: DB에는 UTC ISO(Z), 입력·표시는 config.timezone 로컬."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config as C

UTC = timezone.utc
PACIFIC = ZoneInfo("America/Los_Angeles")


def now() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_iso() -> str:
    return iso(now())


def parse(value: str | None) -> datetime | None:
    """ISO 문자열 → aware datetime. 시간대가 없으면 서버 로컬(config.timezone)로 해석."""
    if not value:
        return None
    s = value.strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=C.tz())
    return dt.astimezone(UTC)


def local(dt: datetime) -> datetime:
    return dt.astimezone(C.tz())


def local_today() -> str:
    return local(now()).date().isoformat()


def local_day_bounds(day_offset: int = 0) -> tuple[datetime, datetime]:
    """로컬 기준 하루의 [시작, 끝) (UTC aware)."""
    tz = C.tz()
    d = local(now()).date() + timedelta(days=day_offset)
    start = datetime(d.year, d.month, d.day, tzinfo=tz)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def pacific_day_bounds() -> tuple[datetime, datetime]:
    """YouTube·Gemini 할당량이 리셋되는 태평양 시간 기준 오늘."""
    d = now().astimezone(PACIFIC).date()
    start = datetime(d.year, d.month, d.day, tzinfo=PACIFIC)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def next_pacific_midnight() -> datetime:
    return pacific_day_bounds()[1]
