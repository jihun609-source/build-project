"""전역 설정 조회·저장·검증·버전, 에셋 업로드, 예약 미리보기."""
from __future__ import annotations

from typing import Any

import yaml
from fastapi import APIRouter, File, HTTPException, UploadFile

from .. import config as C
from .. import events, scheduler, storage

router = APIRouter()


def public_config() -> dict[str, Any]:
    return C.get_config()


@router.get("/config")
def get_config():
    return {"config": public_config(), "defaults": C.DEFAULT_CONFIG}


@router.put("/config")
def put_config(body: dict[str, Any]):
    cfg = body.get("config", body)
    version = storage.save_config(cfg)
    events.emit("config", {"kind": "config", "version": version})
    return {"config": public_config(), "backup": version}


@router.post("/config/validate")
def validate(body: dict[str, Any]):
    cfg = C.deep_merge(C.DEFAULT_CONFIG, body.get("config", body))
    return {"errors": C.validate_config(cfg)}


@router.get("/config/history")
def history():
    return {"versions": storage.config_history.list("config")}


@router.get("/config/history/{version}")
def history_version(version: str):
    return {"version": version, "text": storage.config_history.read(version),
            "current": C.CONFIG_PATH.read_text(encoding="utf-8") if C.CONFIG_PATH.exists() else ""}


@router.post("/config/restore/{version}")
def restore(version: str):
    storage.restore_config(version)
    events.emit("config", {"kind": "config", "restored": version})
    return {"config": public_config()}


@router.post("/config/assets")
async def upload_asset(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > 500 * 1024 * 1024:
        raise HTTPException(413, "500MB 이하 파일만 업로드할 수 있습니다")
    rel = storage.save_asset(file.filename or "asset", data)
    return {"path": rel}


@router.get("/config/assets")
def list_assets():
    d = C.DIRS["assets"]
    return {"assets": sorted(f"assets/{p.name}" for p in d.iterdir() if p.is_file())}


@router.get("/config/schedule/preview")
def schedule_preview(days: int = 7):
    return {"slots": scheduler.preview(max(1, min(days, 31))), "timezone": C.get_config().get("timezone")}


@router.get("/config/raw")
def raw_yaml():
    return {"text": C.CONFIG_PATH.read_text(encoding="utf-8") if C.CONFIG_PATH.exists() else ""}


@router.put("/config/raw")
def put_raw(body: dict[str, str]):
    try:
        data = yaml.safe_load(body.get("text", "")) or {}
    except yaml.YAMLError as e:
        raise HTTPException(400, f"YAML 문법 오류: {e}")
    return put_config(data)
