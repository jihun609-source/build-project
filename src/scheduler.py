"""예약 슬롯 계산·배정.

- 슬롯: config.schedule.slots (요일별 로컬 HH:MM 목록)
- 배정된 슬롯: slot_assigned=1 인 예약 항목의 publish_at (건너뛰기/삭제되면 풀림)
- 직접 시각을 준 항목은 slot_assigned=0 이라 슬롯을 소비하지 않는다
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Any

from . import config as C
from . import models as M
from .timeutil import UTC, iso, local, now, parse

_lock = threading.RLock()
ACTIVE_EXCLUDE = ("skipped",)


def upcoming_slots(days: int = 14, start: datetime | None = None) -> list[datetime]:
    cfg = C.get_config()
    tz = C.tz()
    start = start or now()
    lead = timedelta(minutes=float(cfg["schedule"].get("min_lead_minutes", 20)))
    earliest = start + lead
    slots = cfg["schedule"].get("slots") or {}
    base = local(start).date()
    out = []
    for i in range(days + 1):
        d = base + timedelta(days=i)
        for t in sorted(slots.get(C.WEEKDAYS[d.weekday()]) or []):
            h, m = (int(x) for x in t.split(":"))
            dt = datetime(d.year, d.month, d.day, h, m, tzinfo=tz).astimezone(UTC)
            if dt >= earliest:
                out.append(dt)
    return sorted(out)


def assigned_slots() -> dict[str, int]:
    rows = M.db().execute(
        "SELECT id, publish_at FROM items WHERE slot_assigned=1 AND publish_mode='scheduled' "
        "AND publish_at IS NOT NULL AND status NOT IN ('skipped')"
    )
    return {r["publish_at"]: r["id"] for r in rows}


def next_free_slots(n: int, exclude: set[str] | None = None) -> list[str]:
    """아직 배정되지 않은 가장 가까운 슬롯 n개 (UTC ISO)."""
    with _lock:
        taken = set(assigned_slots()) | (exclude or set())
        out: list[str] = []
        horizon = 14
        while len(out) < n and horizon <= 370:
            out = [s for s in (iso(x) for x in upcoming_slots(horizon)) if s not in taken][:n]
            horizon *= 2
        if len(out) < n:
            raise ValueError("배정할 수 있는 예약 슬롯이 없습니다 (업로드 설정에서 슬롯을 추가하세요)")
        return out


def resolve_publish(publish_at: str | None, use_next_slot: bool) -> dict[str, Any]:
    """fetch/PATCH 입력 → items 필드."""
    if use_next_slot:
        with _lock:
            slot = next_free_slots(1)[0]
            return {"publish_mode": "scheduled", "publish_at": slot, "slot_assigned": 1}
    if publish_at:
        dt = parse(publish_at)
        if dt <= now():
            raise ValueError("과거 시각은 예약할 수 없습니다")
        return {"publish_mode": "scheduled", "publish_at": iso(dt), "slot_assigned": 0}
    return {"publish_mode": "immediate", "publish_at": None, "slot_assigned": 0}


def preview(days: int = 7) -> list[dict[str, Any]]:
    """향후 days일의 슬롯과 배정 현황 + 직접 지정 예약."""
    assigned = assigned_slots()
    titles = {}
    ids = list(assigned.values())
    extra = M.db().execute(
        "SELECT id, publish_at, title, status FROM items WHERE publish_mode='scheduled' AND slot_assigned=0 "
        "AND publish_at IS NOT NULL AND status NOT IN ('skipped','uploaded')"
    ).fetchall()
    if ids:
        q = ",".join("?" for _ in ids)
        for r in M.db().execute(f"SELECT id, title, status FROM items WHERE id IN ({q})", ids):
            titles[r["id"]] = (r["title"], r["status"])
    end = now() + timedelta(days=days)
    out = []
    for dt in upcoming_slots(days, start=now() - timedelta(minutes=float(
            C.get_config()["schedule"].get("min_lead_minutes", 20)))):
        if dt > end:
            break
        key = iso(dt)
        item_id = assigned.get(key)
        t = titles.get(item_id, (None, None))
        out.append({"at": key, "kind": "slot", "item_id": item_id, "title": t[0], "status": t[1]})
    for r in extra:
        dt = parse(r["publish_at"])
        if now() <= dt <= end:
            out.append({"at": r["publish_at"], "kind": "manual", "item_id": r["id"], "title": r["title"],
                        "status": r["status"]})
    out.sort(key=lambda x: x["at"])
    return out


def free_slot_count(days: int = 7) -> int:
    return sum(1 for s in preview(days) if s["kind"] == "slot" and s["item_id"] is None)
