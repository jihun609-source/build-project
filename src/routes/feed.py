"""피드: 확장프로그램 수신, 목록, 가져오기(프리셋·예약)/제외."""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import config as C
from .. import events
from .. import models as M
from .. import scheduler, storage, thumbs
from ..timeutil import iso, local_day_bounds, now_iso, parse
from .items import change_schedule, item_row_public

router = APIRouter()

CODE_RE = re.compile(r"instagram\.com/(?:[^/]+/)?(?:reel|reels|p)/([A-Za-z0-9_\-]+)")


def canonical_url(url: str) -> str:
    m = CODE_RE.search(url or "")
    if not m:
        raise HTTPException(400, "인스타그램 릴스 URL이 아닙니다")
    return f"https://www.instagram.com/reel/{m.group(1)}/"


class FeedIn(BaseModel):
    url: str
    author: str | None = None
    caption: str | None = None
    thumbnail_url: str | None = None
    thumbnail_data: str | None = None     # 확장프로그램 화면 캡처 (data:image/jpeg;base64,...)
    seen_at: str | None = None


class FeedBatch(BaseModel):
    items: list[FeedIn]


class FetchIn(BaseModel):
    edit_preset: str | None = None
    publish_at: str | None = None
    use_next_slot: bool = False
    auto_upload: bool = True            # False면 다운로드만 하고 보류 (자막 탭에서 이어서)


class BulkIn(BaseModel):
    ids: list[int] = Field(min_length=1)
    action: str = "fetch"              # fetch | exclude
    edit_preset: str | None = None
    publish_at: str | None = None
    use_next_slot: bool = False
    sequential_slots: bool = False
    auto_upload: bool = True


class ScheduleIn(BaseModel):
    publish_at: str | None = None
    use_next_slot: bool = False


def _upsert(f: FeedIn) -> tuple[int, bool]:
    url = canonical_url(f.url)
    author = (f.author or "").strip().lstrip("@") or None
    seen = iso(parse(f.seen_at)) if f.seen_at else now_iso()
    row = M.db().execute("SELECT id, author, caption, thumbnail_url FROM feed_items WHERE url=?", (url,)).fetchone()
    if row:
        # 비어 있던 정보만 보강
        M.db().execute(
            "UPDATE feed_items SET author=COALESCE(author, ?), caption=COALESCE(caption, ?), "
            "thumbnail_url=COALESCE(?, thumbnail_url) WHERE id=?",
            (author, f.caption, f.thumbnail_url, row["id"]))
        return row["id"], False
    cur = M.db().execute(
        "INSERT INTO feed_items (url, author, caption, thumbnail_url, seen_at, status) VALUES (?,?,?,?,?, 'seen')",
        (url, author, f.caption, f.thumbnail_url, seen))
    return cur.lastrowid, True


@router.post("/feed")
def receive(body: FeedIn | FeedBatch):
    items = body.items if isinstance(body, FeedBatch) else [body]
    out = []
    for f in items:
        try:
            fid, created = _upsert(f)
        except HTTPException as e:
            out.append({"url": f.url, "error": e.detail})
            continue
        out.append({"id": fid, "created": created})
        got_thumb = False
        if f.thumbnail_data and not thumbs.path(fid).exists():
            got_thumb = thumbs.save_data_url(fid, f.thumbnail_data)
            if got_thumb:
                M.db().execute("UPDATE feed_items SET thumb_attempts=? WHERE id=?", (thumbs.MAX_ATTEMPTS, fid))
        if created or got_thumb:
            events.emit("feed", feed_row(fid))
    thumbs.wake()
    M.kv_set("extension_last_seen", now_iso())
    return {"results": out}


def feed_row(fid: int) -> dict[str, Any] | None:
    rows = list_feed(ids=[fid])
    return rows[0] if rows else None


def list_feed(status: str | None = None, author: str | None = None, date: str | None = None, q: str | None = None,
              ids: list[int] | None = None, limit: int = 60, offset: int = 0) -> list[dict[str, Any]]:
    where, params = [], []
    if status:
        where.append("f.status=?")
        params.append(status)
    if author:
        where.append("f.author LIKE ?")
        params.append(f"%{author.lstrip('@')}%")
    if q:
        where.append("f.caption LIKE ?")
        params.append(f"%{q}%")
    if date:
        from datetime import date as _d
        from datetime import datetime
        try:
            d = _d.fromisoformat(date)
        except ValueError:
            raise HTTPException(400, "date는 YYYY-MM-DD")
        tz = C.tz()
        start = datetime(d.year, d.month, d.day, tzinfo=tz)
        from datetime import timedelta
        where.append("f.seen_at >= ? AND f.seen_at < ?")
        params += [iso(start), iso(start + timedelta(days=1))]
    if ids:
        where.append(f"f.id IN ({','.join('?' for _ in ids)})")
        params += ids
    else:
        # 자막 탭에서 직접 올린 영상(manual://)은 피드에 보이지 않게
        where.append("f.url NOT LIKE 'manual://%'")
    sql = ("SELECT f.*, (SELECT MAX(i.id) FROM items i WHERE i.feed_item_id=f.id) AS item_id FROM feed_items f "
           + (("WHERE " + " AND ".join(where)) if where else "") + " ORDER BY f.seen_at DESC, f.id DESC LIMIT ? OFFSET ?")
    rows = [dict(r) for r in M.db().execute(sql, (*params, limit, offset))]
    item_ids = [r["item_id"] for r in rows if r["item_id"]]
    items = {}
    if item_ids:
        for r in M.db().execute(f"SELECT * FROM items WHERE id IN ({','.join('?' for _ in item_ids)})", item_ids):
            items[r["id"]] = item_row_public(M.row_to_dict(r))
    for r in rows:
        r["item"] = items.get(r.pop("item_id"))
        r["thumb"] = thumbs.url(r["id"])
    return rows


@router.get("/feed/items")
def get_feed(status: str | None = None, author: str | None = None, date: str | None = None, q: str | None = None,
             limit: int = 60, offset: int = 0):
    counts = {r["status"]: r["n"] for r in M.db().execute(
        "SELECT status, COUNT(*) n FROM feed_items WHERE url NOT LIKE 'manual://%' GROUP BY status")}
    return {"items": list_feed(status, author, date, q, limit=min(limit, 300), offset=offset), "counts": counts}


def _new_item(feed_id: int, preset: str | None, publish: dict[str, Any], auto_upload: bool = True) -> int:
    feed = M.get_feed(feed_id)
    if not feed:
        raise HTTPException(404, "피드 항목이 없습니다")
    active = M.db().execute("SELECT id FROM items WHERE feed_item_id=? AND status NOT IN ('skipped')",
                            (feed_id,)).fetchone()
    if active:
        raise HTTPException(409, f"이미 가져온 항목입니다 (#{active['id']})")
    preset = preset or C.get_config()["edit"]["default_preset"]
    if preset not in storage.list_presets():
        raise HTTPException(400, f"프리셋 '{preset}' 이 없습니다")
    item_id = M.insert_item({"feed_item_id": feed_id, "edit_preset": preset, "status": "queued", "stage": "download",
                             "auto_upload": int(auto_upload), **publish})
    M.db().execute("UPDATE feed_items SET status='fetched', fetched_at=? WHERE id=?", (now_iso(), feed_id))
    if auto_upload:
        msg = f"가져오기 (프리셋 {preset}, " + (f"예약 {publish['publish_at']}" if publish.get("publish_at") else "즉시") + ")"
    else:
        msg = "가져오기 - 다운로드만 (자막 탭에서 이어서 처리)"
    M.add_event(item_id, "api", "download", "info", msg)
    events.emit("feed", feed_row(feed_id))
    return item_id


@router.post("/feed/{feed_id}/fetch")
def fetch(feed_id: int, body: FetchIn):
    with scheduler._lock:
        try:
            # 다운로드만 할 때는 게시 시각을 정하지 않는다 (슬롯도 쓰지 않음)
            publish = (scheduler.resolve_publish(body.publish_at, body.use_next_slot) if body.auto_upload
                       else scheduler.resolve_publish(None, False))
        except ValueError as e:
            raise HTTPException(400, str(e))
        item_id = _new_item(feed_id, body.edit_preset, publish, body.auto_upload)
    return {"item_id": item_id, "feed": feed_row(feed_id)}


@router.post("/feed/bulk")
def bulk(body: BulkIn):
    results = []
    if body.action == "exclude":
        for fid in body.ids:
            M.db().execute("UPDATE feed_items SET status='excluded' WHERE id=? AND status='seen'", (fid,))
            events.emit("feed", feed_row(fid))
            results.append({"id": fid, "ok": True})
        return {"results": results}
    with scheduler._lock:
        slots: list[str] = []
        if body.auto_upload and (body.sequential_slots or body.use_next_slot):
            try:
                slots = scheduler.next_free_slots(len(body.ids))
            except ValueError as e:
                raise HTTPException(400, str(e))
        for i, fid in enumerate(body.ids):
            try:
                if slots:
                    publish = {"publish_mode": "scheduled", "publish_at": slots[i], "slot_assigned": 1}
                else:
                    publish = scheduler.resolve_publish(body.publish_at if body.auto_upload else None, False)
                item_id = _new_item(fid, body.edit_preset, publish, body.auto_upload)
                results.append({"id": fid, "ok": True, "item_id": item_id, "publish_at": publish.get("publish_at")})
            except HTTPException as e:
                results.append({"id": fid, "ok": False, "error": e.detail})
            except ValueError as e:
                results.append({"id": fid, "ok": False, "error": str(e)})
    return {"results": results}


@router.post("/feed/{feed_id}/exclude")
def exclude(feed_id: int):
    if not M.get_feed(feed_id):
        raise HTTPException(404, "피드 항목이 없습니다")
    M.db().execute("UPDATE feed_items SET status='excluded' WHERE id=?", (feed_id,))
    events.emit("feed", feed_row(feed_id))
    return feed_row(feed_id)


@router.post("/feed/{feed_id}/restore")
def restore(feed_id: int):
    M.db().execute("UPDATE feed_items SET status='seen' WHERE id=? AND status='excluded'", (feed_id,))
    events.emit("feed", feed_row(feed_id))
    return feed_row(feed_id)


@router.patch("/feed/{feed_id}/schedule")
def schedule(feed_id: int, body: ScheduleIn):
    row = M.db().execute("SELECT MAX(id) AS id FROM items WHERE feed_item_id=?", (feed_id,)).fetchone()
    if not row or not row["id"]:
        raise HTTPException(404, "가져온 항목이 없습니다")
    change_schedule(row["id"], body.publish_at, body.use_next_slot)
    return feed_row(feed_id)


@router.get("/schedule/presets")
def schedule_presets():
    cfg = C.get_config()
    start, _ = local_day_bounds(0)
    return {"presets": cfg["schedule"].get("presets") or [], "timezone": cfg.get("timezone"),
            "now": now_iso(), "today_start": iso(start)}
