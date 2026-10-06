"""C안: 크롬 확장프로그램 업로드 보조 API."""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from .. import config as C
from .. import models as M
from ..timeutil import local, now_iso
from ..workers.base import log
from ..workers.uploader import READY_WHERE, UPLOAD_ORDER, finish_uploaded, resolve_publish_now, upload_metadata

router = APIRouter()


@router.get("/upload/selenium/status")
def selenium_status():
    from .. import selenium_accounts as SA
    browser = C.get_config()["upload"]["selenium"]
    return {"accounts": [{**entry, "login": SA.account_status(entry)} for entry in SA.accounts(browser)],
            "selected_account": browser.get("selected_account", "default"),
            "session": SA.LOGIN.status(), "browser_busy": SA.BROWSER_LOCK.locked()}


class SeleniumLoginIn(BaseModel):
    account_id: str = "default"


@router.post("/upload/selenium/login")
def selenium_login(body: SeleniumLoginIn):
    from .. import selenium_accounts as SA
    cfg = C.get_config()["upload"]
    if cfg["mode"] != "selenium":
        raise HTTPException(409, "D안으로 변경하고 설정을 저장하세요.")
    try:
        return SA.LOGIN.start(cfg["selenium"], body.account_id)
    except SA.BrowserBusy as e:
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/upload/selenium/login/finish")
def selenium_login_finish():
    from .. import selenium_accounts as SA
    try:
        return SA.LOGIN.finish()
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/upload/selenium/login/cancel")
def selenium_login_cancel():
    from .. import selenium_accounts as SA
    return SA.LOGIN.cancel()


def _fmt_date(dt, fmt: str) -> str:
    return (fmt.replace("YYYY", f"{dt.year:04d}").replace("MM", f"{dt.month:02d}").replace("DD", f"{dt.day:02d}")
            .replace("M", str(dt.month)).replace("D", str(dt.day)))


def _fmt_time(dt, fmt: str, ampm: list[str]) -> str:
    h12 = dt.hour % 12 or 12
    s = fmt.replace("HH", f"{dt.hour:02d}").replace("hh", f"{h12:02d}").replace("mm", f"{dt.minute:02d}")
    s = re.sub(r"(?<![A-Za-z])h(?![A-Za-z])", str(h12), s)
    s = s.replace("A", ampm[0] if dt.hour < 12 else ampm[1])
    return s


def pending_count() -> int:
    return M.db().execute(f"SELECT COUNT(*) n FROM items WHERE status='captioned' {READY_WHERE}",
                          (now_iso(),)).fetchone()["n"]


@router.get("/upload/status")
def status():
    return {"mode": C.get_config()["upload"]["mode"], "pending": pending_count(),
            "last_result": M.kv_get("extension_last_result")}


@router.get("/upload/next")
def next_item():
    cfg = C.get_config()["upload"]
    if cfg.get("mode") != "extension":
        raise HTTPException(409, "업로드 모드가 extension이 아닙니다 (업로드 설정에서 전환)")
    item = M.claim_item("captioned", "uploading", "upload", READY_WHERE, (now_iso(),), UPLOAD_ORDER)
    if not item:
        return Response(status_code=204)
    M.update_item(item["id"], upload_mode="extension")
    pub = resolve_publish_now(item, "uploader")
    meta = upload_metadata(item)
    ext = cfg.get("extension", {})
    out = {"id": item["id"], "file_url": f"/files/ready/{item['id']}", **meta,
           "publish_mode": pub["mode"], "privacy": cfg.get("privacy", "public"),
           "made_for_kids": bool(cfg.get("made_for_kids", False))}
    if pub["mode"] == "scheduled":
        lt = local(pub["at"])
        out.update(publish_at=item["publish_at"], publish_date=lt.date().isoformat(),
                   publish_time=lt.strftime("%H:%M"),
                   publish_date_text=_fmt_date(lt, ext.get("date_format", "YYYY. M. D.")),
                   publish_time_text=_fmt_time(lt, ext.get("time_format", "A h:mm"), ext.get("ampm", ["오전", "오후"])))
    log("uploader", "info", "확장프로그램이 업로드 시작", item["id"], "upload")
    return out


class ProgressIn(BaseModel):
    pct: float | None = None
    text: str | None = None


@router.post("/upload/{item_id}/progress")
def progress(item_id: int, body: ProgressIn):
    item = M.get_item(item_id)
    if not item or item["status"] != "uploading":
        raise HTTPException(409, "업로드 중인 항목이 아닙니다")
    M.set_progress(item_id, body.pct if body.pct is not None else item["progress_pct"], body.text)
    return {"ok": True}


class ResultIn(BaseModel):
    video_id: str | None = None
    error: str | None = None
    switched_to_immediate: bool = False


@router.post("/upload/{item_id}/result")
def result(item_id: int, body: ResultIn):
    item = M.get_item(item_id)
    if not item:
        raise HTTPException(404, "항목이 없습니다")
    if body.switched_to_immediate:
        log("uploader", "warning", "스튜디오가 예약 시각을 거부해 즉시 게시로 전환", item_id, "upload")
        M.update_item(item_id, publish_mode="immediate", publish_at=None, slot_assigned=0)
    if body.video_id:
        finish_uploaded(item_id, body.video_id, "extension")
        M.kv_set("extension_last_result", {"item_id": item_id, "ok": True, "video_id": body.video_id,
                                           "at": now_iso()})
    else:
        err = body.error or "알 수 없는 오류"
        M.update_item(item_id, status="failed_upload", error=f"확장프로그램: {err}")
        log("uploader", "error", f"확장프로그램 업로드 실패: {err}", item_id, "upload")
        M.kv_set("extension_last_result", {"item_id": item_id, "ok": False, "error": err, "at": now_iso()})
    return {"ok": True}


@router.get("/upload/selectors")
def get_selectors():
    p = C.DIRS["extension"] / "upload" / "selectors.json"
    return {"text": p.read_text(encoding="utf-8") if p.exists() else "{}"}


@router.put("/upload/selectors")
def put_selectors(body: dict):
    import json

    from .. import storage
    text = body.get("text", "")
    try:
        json.loads(text)
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"JSON 문법 오류: {e.msg} (줄 {e.lineno})")
    p = C.DIRS["extension"] / "upload" / "selectors.json"
    storage.save_with_backup(p, text, storage.History(C.DIRS["config_history"]), "selectors")
    return {"ok": True}


@router.get("/upload/selectors.json")
def selectors_raw():
    """확장프로그램이 최신 셀렉터를 받아가는 경로."""
    import json
    p = C.DIRS["extension"] / "upload" / "selectors.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
