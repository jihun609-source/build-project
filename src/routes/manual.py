"""자막 탭: 다운로드만 한(보류) 영상 이어서 처리, 직접 편집한 영상·새 영상 올리기.

올린 영상은 inbox/{id}_manual.{ext} 로 저장하고 원본(source_path)을 교체한 뒤 downloaded 상태로 넣는다.
이후는 기존 흐름과 같다: 편집(기본은 규격만 맞춤) → 자막 → 업로드.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from .. import config as C
from .. import events
from .. import models as M
from .. import scheduler, storage, thumbs
from ..timeutil import now_iso
from ..util import proc
from .items import item_row_public

router = APIRouter()

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
MAX_BYTES = 4 * 1024 ** 3


def _rows(where: str, params: tuple = (), limit: int = 50) -> list[dict[str, Any]]:
    sql = ("SELECT i.*, f.url AS feed_url, f.author AS feed_author, f.caption AS feed_caption FROM items i "
           "LEFT JOIN feed_items f ON f.id=i.feed_item_id WHERE " + where + " ORDER BY i.id DESC LIMIT ?")
    out = []
    for r in M.db().execute(sql, (*params, limit)):
        d = item_row_public(M.row_to_dict(r))
        d["thumb"] = thumbs.url(d["feed_item_id"]) if d.get("feed_item_id") else None
        d["files"] = {"source": f"/files/inbox/{d['id']}" if d.get("source_path") and Path(d["source_path"]).exists()
                      else None}
        d["filename"] = (d.get("source_meta") or {}).get("filename") if isinstance(d.get("source_meta"), dict) else None
        out.append(d)
    return out


@router.get("/manual/items")
def manual_items():
    return {
        # 다운로드만 하고 멈춘 항목
        "held": _rows("i.status='held'", limit=200),
        # 직접 올린 영상의 진행 상황
        "recent": _rows("i.source_kind='manual'", limit=40),
    }


def _publish(publish_at: str | None, use_next_slot: bool) -> dict[str, Any]:
    try:
        return scheduler.resolve_publish(publish_at or None, use_next_slot)
    except ValueError as e:
        raise HTTPException(400, str(e))


def _check_preset(name: str | None) -> str:
    preset = name or C.get_config()["edit"]["default_preset"]
    if preset not in storage.list_presets():
        raise HTTPException(400, f"프리셋 '{preset}' 이 없습니다")
    return preset


class ContinueIn(BaseModel):
    edit_preset: str | None = None
    publish_at: str | None = None
    use_next_slot: bool = False


@router.post("/items/{item_id}/continue")
def continue_auto(item_id: int, body: ContinueIn):
    """보류 항목을 편집본 없이 기존처럼 자동 처리 (프리셋 편집 → 자막 → 업로드)."""
    item = M.get_item(item_id)
    if not item or item["status"] != "held":
        raise HTTPException(409, "다운로드만 하고 보류된 항목이 아닙니다")
    preset = _check_preset(body.edit_preset or item.get("edit_preset"))
    with scheduler._lock:
        publish = _publish(body.publish_at, body.use_next_slot)
        M.update_item(item_id, status="downloaded", auto_upload=1, edit_video=1, edit_preset=preset, **publish)
    M.add_event(item_id, "api", "edit", "info", f"보류 해제 - 자동 처리 (프리셋 {preset})")
    return M.item_public(M.get_item(item_id))


async def _save_upload(file: UploadFile, target_stem: str) -> tuple[Path, dict]:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in VIDEO_EXT:
        raise HTTPException(400, f"영상 파일만 올릴 수 있습니다 ({', '.join(sorted(VIDEO_EXT))})")
    inbox = C.DIRS["inbox"]
    tmp = inbox / f".{target_stem}.{uuid.uuid4().hex[:8]}.upload{ext}"
    size = 0
    try:
        with open(tmp, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, "4GB 이하 영상만 올릴 수 있습니다")
                out.write(chunk)
        meta = proc.probe(tmp)
        if not meta.get("width") or meta["duration"] <= 0:
            raise HTTPException(400, "영상 정보를 읽을 수 없습니다 (손상됐거나 영상이 아닌 파일)")
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    target = inbox / f"{target_stem}{ext}"
    target.unlink(missing_ok=True)
    tmp.replace(target)
    meta["filename"] = Path(file.filename or "").name
    return target, meta


def _start_with_video(item_id: int, target: Path, meta: dict, preset: str, edit_video: bool,
                      publish: dict[str, Any]) -> None:
    from ..workers.captioner import invalidate
    old = M.get_item(item_id) or {}
    # 원본이 바뀌었으므로 이전 편집·음성·프레임·AI 결과는 모두 버린다
    invalidate(item_id, frames=True, ai=True, stt_cache=True)
    for p in (old.get("edited_path"), old.get("final_path")):
        if p:
            Path(p).unlink(missing_ok=True)
    M.update_item(item_id, status="downloaded", source_path=str(target), source_meta=meta,
                  source_duration=meta["duration"], source_kind="manual", edit_video=int(edit_video),
                  edit_preset=preset, auto_upload=1, edited_path=None, final_path=None, needs_review=0,
                  error=None, progress_pct=0, **publish)
    if old.get("feed_item_id") and thumbs.from_video(old["feed_item_id"], target, min(1.0, meta["duration"] / 3)):
        thumbs.mark(old["feed_item_id"])


@router.post("/items/{item_id}/manual-video")
async def upload_for_item(item_id: int, file: UploadFile = File(...), edit_preset: str = Form(""),
                          edit_video: bool = Form(False), publish_at: str = Form(""),
                          use_next_slot: bool = Form(False)):
    """보류 항목에 직접 편집한 영상을 올리면 그 영상으로 자막 → 업로드."""
    item = M.get_item(item_id)
    if not item:
        raise HTTPException(404, "항목이 없습니다")
    if item["status"] not in ("held", "failed_edit", "failed_caption"):
        raise HTTPException(409, f"지금은 영상을 바꿀 수 없습니다 ({item['status']})")
    preset = _check_preset(edit_preset or item.get("edit_preset"))
    target, meta = await _save_upload(file, f"{item_id}_manual")
    with scheduler._lock:
        publish = _publish(publish_at, use_next_slot)
        _start_with_video(item_id, target, meta, preset, edit_video, publish)
    M.add_event(item_id, "api", "edit", "info",
                f"직접 편집한 영상 올림 ({meta['filename']}, {meta['duration']:.1f}초) - 자막·업로드 자동 처리")
    return M.item_public(M.get_item(item_id))


@router.post("/manual/upload")
async def upload_new(file: UploadFile = File(...), edit_preset: str = Form(""), edit_video: bool = Form(False),
                     publish_at: str = Form(""), use_next_slot: bool = Form(False)):
    """피드와 상관없이 새 영상을 올려 자막 → 업로드."""
    preset = _check_preset(edit_preset)
    with scheduler._lock:
        publish = _publish(publish_at, use_next_slot)   # 먼저 검증 (과거 시각이면 파일 받기 전에 거절)
    # 썸네일·이벤트를 피드 구조로 다루기 위한 내부용 피드 행 (피드 목록에는 보이지 않음)
    cur = M.db().execute(
        "INSERT INTO feed_items (url, author, caption, seen_at, status, fetched_at, thumb_attempts) "
        "VALUES (?, NULL, NULL, ?, 'fetched', ?, ?)",
        (f"manual://{uuid.uuid4().hex}", now_iso(), now_iso(), thumbs.MAX_ATTEMPTS))
    feed_id = cur.lastrowid
    item_id = M.insert_item({"feed_item_id": feed_id, "edit_preset": preset, "status": "held", "stage": "download",
                             "source_kind": "manual", "auto_upload": 1})
    try:
        target, meta = await _save_upload(file, f"{item_id}_manual")
    except BaseException:
        M.db().execute("DELETE FROM items WHERE id=?", (item_id,))
        M.db().execute("DELETE FROM feed_items WHERE id=?", (feed_id,))
        events.emit("item_deleted", {"id": item_id, "feed_item_id": feed_id})
        raise
    with scheduler._lock:
        publish = _publish(publish_at, use_next_slot)
        _start_with_video(item_id, target, meta, preset, edit_video, publish)
    M.add_event(item_id, "api", "edit", "info",
                f"새 영상 올림 ({meta['filename']}, {meta['duration']:.1f}초) - 자막·업로드 자동 처리")
    return M.item_public(M.get_item(item_id))

