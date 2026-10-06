"""편집 프리셋 CRUD·버전, 편집 미리보기."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from .. import config as C
from .. import events
from .. import models as M
from .. import storage

router = APIRouter()


def _in_use(name: str) -> int:
    row = M.db().execute("SELECT COUNT(*) n FROM items WHERE edit_preset=? AND status IN "
                         "('queued','downloading','downloaded')", (name,)).fetchone()
    return row["n"]


@router.get("/presets/edit")
def list_presets():
    default = C.get_config()["edit"]["default_preset"]
    return {"presets": [{"name": n, "default": n == default, "pending_items": _in_use(n)}
                        for n in storage.list_presets()], "default": default}


@router.get("/presets/edit/{name}")
def get_preset(name: str):
    return {"preset": storage.read_preset(name), "rules": storage.PRESET_RULES}


class NewPreset(BaseModel):
    name: str
    data: dict[str, Any] | None = None
    from_preset: str | None = None


@router.post("/presets/edit")
def create(body: NewPreset):
    base = storage.read_preset(body.from_preset) if body.from_preset else storage.read_preset("default")
    data = C.deep_merge(base, body.data or {})
    storage.save_preset(body.name, data, create=True)
    events.emit("config", {"kind": "preset", "name": body.name})
    return {"preset": storage.read_preset(body.name)}


@router.put("/presets/edit/{name}")
def save(name: str, body: dict[str, Any]):
    data = body.get("preset", body)
    version = storage.save_preset(name, data)
    events.emit("config", {"kind": "preset", "name": name, "version": version})
    return {"preset": storage.read_preset(name), "backup": version}


@router.post("/presets/edit/validate")
def validate(body: dict[str, Any]):
    p = storage.normalize_preset(body.get("preset", body), body.get("name", "tmp"))
    return {"errors": storage.validate_preset(p)}


class NameIn(BaseModel):
    name: str


@router.post("/presets/edit/{name}/clone")
def clone(name: str, body: NameIn):
    storage.clone_preset(name, body.name)
    events.emit("config", {"kind": "preset", "name": body.name})
    return {"preset": storage.read_preset(body.name)}


@router.post("/presets/edit/{name}/rename")
def rename(name: str, body: NameIn):
    storage.rename_preset(name, body.name)
    M.db().execute("UPDATE items SET edit_preset=? WHERE edit_preset=? AND status IN "
                   "('queued','downloading','downloaded')", (body.name, name))
    events.emit("config", {"kind": "preset", "name": body.name, "renamed_from": name})
    return {"preset": storage.read_preset(body.name)}


@router.delete("/presets/edit/{name}")
def delete(name: str, force: bool = False):
    if name == "default":
        raise HTTPException(400, "default 프리셋은 삭제할 수 없습니다")
    n = _in_use(name)
    if n and not force:
        raise HTTPException(409, f"이 프리셋을 쓰는 대기 항목이 {n}개 있습니다. 삭제하면 default로 편집됩니다 "
                                 f"(force=true로 다시 요청)")
    storage.delete_preset(name)
    events.emit("config", {"kind": "preset", "deleted": name})
    return {"deleted": name, "pending_items": n}


@router.post("/presets/edit/{name}/default")
def set_default(name: str):
    storage.read_preset(name)
    storage.patch_config({"edit": {"default_preset": name}})
    events.emit("config", {"kind": "config"})
    return {"default": name}


@router.get("/presets/edit/{name}/history")
def history(name: str):
    return {"versions": storage.preset_history.list(name)}


@router.get("/presets/edit/{name}/history/{version}")
def history_version(name: str, version: str):
    path = C.DIRS["presets"] / f"{name}.yaml"
    return {"version": version, "text": storage.preset_history.read(version),
            "current": path.read_text(encoding="utf-8") if path.exists() else ""}


@router.post("/presets/edit/{name}/restore/{version}")
def restore(name: str, version: str):
    storage.restore_preset(name, version)
    events.emit("config", {"kind": "preset", "name": name, "restored": version})
    return {"preset": storage.read_preset(name)}


# ---------------------------------------------------------------- 미리보기
class PreviewIn(BaseModel):
    item_id: int
    preset: str | None = None
    overrides: dict[str, Any] | None = None
    seconds: float = 5.0


def _preview(body: PreviewIn) -> dict[str, Any]:
    from ..ai import render
    from ..util import proc
    from ..workers.editor import build_command, plan

    item = M.get_item(body.item_id)
    if not item:
        raise HTTPException(404, "항목이 없습니다")
    src = Path(item.get("source_path") or "")
    if not src.exists():
        raise HTTPException(409, "원본이 아직 없습니다 (다운로드된 항목을 고르세요)")
    base = storage.read_preset(body.preset or item.get("edit_preset") or "default")
    preset = storage.normalize_preset(C.deep_merge(base, body.overrides or {}), base["name"])
    errors = storage.validate_preset(preset)
    if errors:
        raise storage.StorageError("프리셋 값이 올바르지 않습니다", errors)
    meta = item.get("source_meta") or proc.probe(src)
    info = plan(meta, preset["video"])
    seconds = max(1.0, min(15.0, body.seconds))
    key = hashlib.sha1(json.dumps([item["id"], preset, seconds], sort_keys=True, default=str).encode()).hexdigest()[:12]
    pdir = C.DIRS["work"] / "preview"
    pdir.mkdir(parents=True, exist_ok=True)
    out = pdir / f"{item['id']}_{key}.mp4"
    if not out.exists():
        # 자막: 항목의 캡션(없으면 샘플), 미리보기 구간 0~seconds 에 표시
        params = {"caption": {**preset["caption"], "start_sec": 0, "end_sec": seconds}, "intro_duration": 0,
                  "main_duration": min(seconds, info["main_duration"]), "outro_duration": 0,
                  "edited_duration": min(seconds, info["main_duration"])}
        caption = item.get("caption") or "자막 미리보기\n샘플 문장입니다"
        segs = [{"start": 0.3, "end": seconds - 0.3, "text": "대사 자막은 이렇게 보입니다"}]
        ass, _ = render.build_ass(params, caption, segs, True)
        (pdir / f"{key}.ass").write_text(ass, encoding="utf-8")
        fontsdir = render.prepare_fonts(pdir, preset["caption"]["font_path"])
        tmp = pdir / f"{item['id']}_{key}.tmp.mp4"
        args, total = build_command(src, meta, preset["video"], tmp, preview_sec=seconds,
                                    subtitles=f"{key}.ass", fontsdir=fontsdir)
        proc.ffmpeg_with_progress(args, total, lambda p: None, timeout=300, cwd=pdir)
        tmp.replace(out)
        _prune_previews(pdir)
    return {"url": f"/files/preview/{out.name}", "path": str(out), "plan": info}


def _prune_previews(pdir: Path, keep: int = 30) -> None:
    files = sorted(pdir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files[keep:]:
        p.unlink(missing_ok=True)
    for p in pdir.glob("*.ass"):
        if time.time() - p.stat().st_mtime > 3600:
            p.unlink(missing_ok=True)


@router.post("/edit/preview")
async def preview(body: PreviewIn):
    return await run_in_threadpool(_preview, body)


@router.post("/edit/estimate")
def estimate(body: PreviewIn):
    """결과 길이 예상치: 원본 45초 → 편집본 32.3초."""
    from ..util import proc
    from ..workers.editor import plan
    item = M.get_item(body.item_id)
    if not item:
        raise HTTPException(404, "항목이 없습니다")
    meta = item.get("source_meta")
    if not meta:
        src = Path(item.get("source_path") or "")
        if not src.exists():
            raise HTTPException(409, "원본 정보가 없습니다")
        meta = proc.probe(src)
    base = storage.read_preset(body.preset or item.get("edit_preset") or "default")
    preset = storage.normalize_preset(C.deep_merge(base, body.overrides or {}), base["name"])
    try:
        return {"plan": plan(meta, preset["video"])}
    except ValueError as e:
        raise HTTPException(400, str(e))
