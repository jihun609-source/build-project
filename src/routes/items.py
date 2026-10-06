"""파이프라인 항목: 조회·재시도·건너뛰기·삭제·메타데이터·예약·재편집·재생성·승인."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import config as C
from .. import events
from .. import models as M
from .. import scheduler, storage

router = APIRouter()

BUSY = {"downloading", "editing", "captioning", "uploading"}
LOCKED_SCHEDULE = BUSY | {"uploaded", "skipped"}


def item_row_public(item: dict[str, Any] | None) -> dict[str, Any] | None:
    d = M.item_public(item)
    if d is None:
        return None
    for k in ("transcript",):
        d.pop(k, None)
    return d


def _get(item_id: int) -> dict[str, Any]:
    item = M.get_item(item_id)
    if not item:
        raise HTTPException(404, "항목이 없습니다")
    return item


def _files_url(item: dict[str, Any]) -> dict[str, str | None]:
    out: dict[str, str | None] = {"source": None, "edited": None, "final": None}
    if item.get("source_path") and Path(item["source_path"]).exists():
        out["source"] = f"/files/inbox/{item['id']}"
    if item.get("edited_path") and Path(item["edited_path"]).exists():
        out["edited"] = f"/files/work/{item['id']}"
    if item.get("final_path") and Path(item["final_path"]).exists():
        out["final"] = f"/files/final/{item['id']}"
    return out


@router.get("/items")
def list_items(status: str | None = None, stage: str | None = None, failed: bool = False, scheduled: bool = False,
               review: bool = False, preset: str | None = None, q: str | None = None, limit: int = 100,
               offset: int = 0):
    where, params = [], []
    if status:
        sts = status.split(",")
        where.append(f"i.status IN ({','.join('?' for _ in sts)})")
        params += sts
    if stage:
        where.append("i.stage=?")
        params.append(stage)
    if failed:
        where.append("i.status LIKE 'failed_%'")
    if scheduled:
        where.append("i.publish_mode='scheduled' AND i.status NOT IN ('uploaded','skipped')")
    if review:
        where.append("i.status='captioned_review'")
    if preset:
        where.append("i.edit_preset=?")
        params.append(preset)
    if q:
        where.append("(i.title LIKE ? OR i.caption LIKE ? OR f.author LIKE ?)")
        params += [f"%{q}%"] * 3
    sql = ("SELECT i.*, f.url AS feed_url, f.author AS feed_author, f.thumbnail_url AS feed_thumbnail, "
           "f.caption AS feed_caption FROM items i LEFT JOIN feed_items f ON f.id=i.feed_item_id "
           + ("WHERE " + " AND ".join(where) if where else "") + " ORDER BY i.id DESC LIMIT ? OFFSET ?")
    from .. import thumbs
    rows = [item_row_public(M.row_to_dict(r)) for r in M.db().execute(sql, (*params, min(limit, 500), offset))]
    for r in rows:
        r["thumb"] = thumbs.url(r["feed_item_id"]) if r.get("feed_item_id") else None
    counts = {r["status"]: r["n"] for r in M.db().execute("SELECT status, COUNT(*) n FROM items GROUP BY status")}
    return {"items": rows, "counts": counts}


@router.get("/items/{item_id}")
def get_item(item_id: int):
    item = _get(item_id)
    d = M.item_public(item)
    d["feed"] = M.get_feed(item["feed_item_id"]) if item.get("feed_item_id") else None
    d["events"] = M.item_events(item_id)
    d["files"] = _files_url(item)
    wd = C.DIRS["work"] / str(item_id)

    def rd(name):
        p = wd / name
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return None
        return None

    frames = rd("frames/frames.json") or []
    d["frames"] = [{**f, "url": f"/files/frames/{item_id}/{f['file']}"} for f in frames]
    stt_raw = rd("stt_raw.json")
    if stt_raw and item.get("edit_params"):
        from ..ai.stt import remap_segments
        d["segments"] = remap_segments(stt_raw.get("segments") or [], item["edit_params"])
        d["stt"] = {k: stt_raw.get(k) for k in ("language", "has_speech", "reason", "avg_logprob", "device")}
    else:
        d["segments"], d["stt"] = [], None
    d["ai_response"] = rd("ai_response.json")
    d["render"] = rd("render.json")
    d["audio"] = rd("audio.json")
    from ..workers.uploader import upload_metadata
    d["upload_preview"] = upload_metadata(item)
    return d


class ItemPatch(BaseModel):
    caption: str | None = None
    summary: str | None = None
    title: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    publish_mode: str | None = None
    publish_at: str | None = None
    use_next_slot: bool = False


@router.patch("/items/{item_id}")
def patch_item(item_id: int, body: ItemPatch):
    item = _get(item_id)
    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items()
              if k in ("caption", "summary", "title", "description", "tags")}
    if fields:
        if item["status"] in BUSY:
            raise HTTPException(409, "처리 중에는 수정할 수 없습니다")
        if "tags" in fields:
            fields["tags"] = [t.strip().lstrip("#") for t in fields["tags"] if t.strip()]
        caption_changed = "caption" in fields and fields["caption"] != item.get("caption")
        M.update_item(item_id, **fields)
        M.add_event(item_id, "api", "caption", "info", "메타데이터 수정: " + ", ".join(fields))
        # 캡션을 직접 고치면 AI 호출 없이 합성만 다시
        if caption_changed and item["status"] in ("captioned", "captioned_review", "failed_caption", "failed_upload") \
                and (C.DIRS["work"] / str(item_id) / "ai_result.json").exists():
            M.update_item(item_id, status="edited", error=None)
            M.add_event(item_id, "api", "caption", "info", "캡션 수정으로 자막 합성 다시 실행")
    if "publish_mode" in body.model_fields_set or "publish_at" in body.model_fields_set or body.use_next_slot:
        if body.publish_mode == "immediate":
            change_schedule(item_id, None, False)
        else:
            change_schedule(item_id, body.publish_at, body.use_next_slot)
    return M.item_public(M.get_item(item_id))


def change_schedule(item_id: int, publish_at: str | None, use_next_slot: bool) -> dict[str, Any]:
    item = _get(item_id)
    if item["status"] in LOCKED_SCHEDULE:
        raise HTTPException(409, "업로드가 시작된 뒤에는 게시 시각을 바꿀 수 없습니다")
    with scheduler._lock:
        # 자기 슬롯은 먼저 풀어준 뒤 다시 배정
        M.update_item(item_id, _emit=False, slot_assigned=0)
        try:
            publish = scheduler.resolve_publish(publish_at, use_next_slot)
        except ValueError as e:
            M.update_item(item_id, _emit=False, slot_assigned=int(item["slot_assigned"]))
            raise HTTPException(400, str(e))
        M.update_item(item_id, **publish, next_attempt_at=None)
    M.add_event(item_id, "api", "upload", "info",
                f"게시 시각 변경 → {publish['publish_at']}" if publish["publish_at"] else "즉시 게시로 전환")
    return M.get_item(item_id)


@router.post("/items/{item_id}/retry")
def retry(item_id: int):
    item = _get(item_id)
    back = M.RETRY_TO.get(item["status"])
    if not back:
        raise HTTPException(409, f"재시도할 수 없는 상태입니다 ({item['status']})")
    M.update_item(item_id, status=back, error=None, progress_pct=0, stage_detail=None, next_attempt_at=None)
    M.add_event(item_id, "api", item.get("stage"), "info", "재시도")
    return M.item_public(M.get_item(item_id))


@router.post("/items/retry-failed")
def retry_failed():
    n = 0
    for r in M.db().execute("SELECT id, status FROM items WHERE status LIKE 'failed_%'").fetchall():
        M.update_item(r["id"], status=M.RETRY_TO[r["status"]], error=None, progress_pct=0, next_attempt_at=None)
        n += 1
    return {"retried": n}


@router.post("/items/{item_id}/skip")
def skip(item_id: int):
    item = _get(item_id)
    if item["status"] in BUSY or item["status"] == "uploaded":
        raise HTTPException(409, "처리 중이거나 업로드된 항목은 건너뛸 수 없습니다")
    M.update_item(item_id, status="skipped", slot_assigned=0)
    M.add_event(item_id, "api", item.get("stage"), "info", "건너뛰기")
    return M.item_public(M.get_item(item_id))


@router.post("/items/{item_id}/delete")
@router.delete("/items/{item_id}")
def delete(item_id: int):
    item = _get(item_id)
    if item["status"] in BUSY:
        raise HTTPException(409, "처리 중인 항목은 삭제할 수 없습니다 (워커 정지 후 삭제)")
    for p in (item.get("source_path"), item.get("edited_path"),
              str(C.DIRS["ready"] / f"{item_id}_final.mp4")):
        if p:
            Path(p).unlink(missing_ok=True)
    shutil.rmtree(C.DIRS["work"] / str(item_id), ignore_errors=True)
    M.db().execute("DELETE FROM events WHERE item_id=?", (item_id,))
    M.db().execute("DELETE FROM items WHERE id=?", (item_id,))
    feed = M.get_feed(item["feed_item_id"]) if item.get("feed_item_id") else None
    if feed and str(feed["url"]).startswith("manual://"):
        # 직접 올린 영상의 내부용 피드 행은 함께 지운다
        from .. import thumbs
        thumbs.path(feed["id"]).unlink(missing_ok=True)
        M.db().execute("DELETE FROM feed_items WHERE id=?", (feed["id"],))
        events.emit("item_deleted", {"id": item_id, "feed_item_id": feed["id"]})
        return {"deleted": item_id}
    if item.get("feed_item_id"):
        M.db().execute("UPDATE feed_items SET status='seen', fetched_at=NULL WHERE id=?", (item["feed_item_id"],))
        from .feed import feed_row
        events.emit("feed", feed_row(item["feed_item_id"]))
    events.emit("item_deleted", {"id": item_id, "feed_item_id": item.get("feed_item_id")})
    return {"deleted": item_id}


class ReeditIn(BaseModel):
    edit_preset: str | None = None


@router.post("/items/{item_id}/reedit")
def reedit(item_id: int, body: ReeditIn):
    from ..workers.captioner import invalidate
    item = _get(item_id)
    if item["status"] in BUSY | {"queued", "uploaded"}:
        raise HTTPException(409, f"지금은 다시 편집할 수 없습니다 ({item['status']})")
    if not item.get("source_path") or not Path(item["source_path"]).exists():
        raise HTTPException(409, "원본 파일이 없습니다 (재시도로 다시 다운로드)")
    preset = body.edit_preset or item.get("edit_preset") or C.get_config()["edit"]["default_preset"]
    if preset not in storage.list_presets():
        raise HTTPException(400, f"프리셋 '{preset}' 이 없습니다")
    invalidate(item_id, frames=True, ai=True)
    for p in (item.get("edited_path"), item.get("final_path")):
        if p:
            Path(p).unlink(missing_ok=True)
    M.update_item(item_id, status="downloaded", edit_preset=preset, edited_path=None, final_path=None,
                  needs_review=0, error=None, progress_pct=0)
    M.add_event(item_id, "api", "edit", "info", f"편집 다시 실행 (프리셋 {preset}) - 자막 단계까지 이어서 실행")
    return M.item_public(M.get_item(item_id))


class RecaptionIn(BaseModel):
    instruction: str | None = None
    profile: str | None = None


@router.post("/items/{item_id}/recaption")
def recaption(item_id: int, body: RecaptionIn):
    from ..workers.captioner import invalidate
    item = _get(item_id)
    if item["status"] in BUSY | {"queued", "downloaded", "uploaded"}:
        raise HTTPException(409, f"지금은 다시 생성할 수 없습니다 ({item['status']})")
    if body.profile and body.profile not in storage.list_profiles():
        raise HTTPException(400, "프롬프트 프로필이 없습니다")
    invalidate(item_id, ai=True)
    M.update_item(item_id, status="edited", extra_instruction=body.instruction or None,
                  prompt_profile_override=body.profile or None, needs_review=0, error=None, progress_pct=0)
    M.add_event(item_id, "api", "caption", "info",
                "AI 다시 생성" + (f" (추가 지시: {body.instruction})" if body.instruction else ""))
    return M.item_public(M.get_item(item_id))


@router.post("/items/{item_id}/rerender")
def rerender(item_id: int):
    item = _get(item_id)
    if item["status"] in BUSY | {"queued", "downloaded", "uploaded"}:
        raise HTTPException(409, f"지금은 다시 합성할 수 없습니다 ({item['status']})")
    if not (C.DIRS["work"] / str(item_id) / "ai_result.json").exists():
        raise HTTPException(409, "AI 결과가 없어 합성만 할 수 없습니다 (다시 생성 사용)")
    M.update_item(item_id, status="edited", error=None, progress_pct=0)
    M.add_event(item_id, "api", "caption", "info", "자막만 다시 합성")
    return M.item_public(M.get_item(item_id))


@router.post("/items/{item_id}/approve")
def approve(item_id: int):
    item = _get(item_id)
    if item["status"] != "captioned_review":
        raise HTTPException(409, "검토 대기 상태가 아닙니다")
    M.update_item(item_id, status="captioned", needs_review=0)
    M.add_event(item_id, "api", "caption", "info", "검토 승인 - 업로드 대기열 진입")
    return M.item_public(M.get_item(item_id))


@router.post("/items/{item_id}/upload-now")
def upload_now(item_id: int):
    item = _get(item_id)
    if item["status"] in ("uploading", "uploaded", "skipped"):
        raise HTTPException(409, f"지금은 할 수 없습니다 ({item['status']})")
    change_schedule(item_id, None, False)
    M.update_item(item_id, next_attempt_at=None)
    M.add_event(item_id, "api", "upload", "info", "즉시 업로드 요청")
    return M.item_public(M.get_item(item_id))


@router.get("/items/{item_id}/events")
def item_events(item_id: int):
    return {"events": M.item_events(item_id)}

