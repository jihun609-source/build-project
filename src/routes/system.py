"""시스템: 상태·대시보드·워커 제어·로그·저장 공간·백업/복원·파일 서빙·인증."""
from __future__ import annotations

import io
import os
import socket
import sqlite3
import tempfile
import threading
import time
import zipfile
from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel

from .. import auth
from .. import config as C
from .. import events
from .. import models as M
from .. import scheduler, youtube
from ..ai.providers import anthropic as anth
from ..ai.providers import gemini, ollama
from ..timeutil import iso, local_day_bounds, local_today, now, now_iso, pacific_day_bounds

router = APIRouter()
public = APIRouter()   # 토큰 없이 접근 (로그인, OAuth 콜백, 헬스체크)

_cache: dict[str, tuple[float, Any]] = {}


def cached(key: str, ttl: float, fn):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (time.monotonic(), val)
    return val


def lan_addresses() -> list[str]:
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    ips.discard("127.0.0.1")
    return sorted(ips)


def _workers() -> dict:
    from ..main import workers
    return workers()


def uploads_between(start, end) -> int:
    return M.db().execute("SELECT COUNT(*) n FROM items WHERE status='uploaded' AND uploaded_at >= ? AND uploaded_at < ?",
                          (iso(start), iso(end))).fetchone()["n"]


@router.get("/system/status")
def status():
    cfg = C.get_config()
    port = cfg["server"].get("port", 8000)
    today = local_today()
    usage = M.usage_on(today)
    ps, pe = pacific_day_bounds()
    from ..ai import stt
    return {
        "server": {"time": now_iso(), "timezone": cfg.get("timezone"), "port": port,
                   "addresses": [f"http://{ip}:{port}/ui" for ip in lan_addresses()], "ws_clients": events.client_count()},
        "workers": [w.status() for w in _workers().values()],
        "ollama": cached("ollama", 10, ollama.status),
        "youtube": cached("youtube", 60, youtube.status),
        "upload_mode": cfg["upload"]["mode"],
        "today_uploads": uploads_between(*local_day_bounds(0)),
        "api_uploads_quota_day": M.db().execute(
            "SELECT COUNT(*) n FROM items WHERE upload_mode='api' AND uploaded_at >= ? AND uploaded_at < ?",
            (iso(ps), iso(pe))).fetchone()["n"],
        "upload_daily_limit": cfg["upload"]["daily_limit"],
        "extension_last_seen": M.kv_get("extension_last_seen"),
        "gemini": {"has_key": gemini.has_key(), "model": cfg["ai"]["gemini"]["model"],
                   "used": usage.get("gemini", {}).get("calls", 0), "limit": cfg["ai"]["gemini"]["daily_limit"],
                   "exhausted_until": M.kv_get(gemini.EXHAUSTED_KEY)},
        "anthropic": {"has_key": anth.has_key(), "model": cfg["ai"]["anthropic"]["model"],
                      "used": usage.get("anthropic", {}).get("calls", 0), "limit": cfg["ai"]["anthropic"]["daily_limit"]},
        "stt_device": stt.device_used,
        "ollama_pull": M.kv_get("ollama_pull"),
    }


@router.get("/system/dashboard")
def dashboard():
    counts = {r["status"]: r["n"] for r in M.db().execute("SELECT status, COUNT(*) n FROM items GROUP BY status")}
    feed = {r["status"]: r["n"] for r in M.db().execute("SELECT status, COUNT(*) n FROM feed_items GROUP BY status")}

    def c(*names):
        return sum(counts.get(n, 0) for n in names)

    cards = {
        "seen": feed.get("seen", 0),
        "fetched": c("queued", "downloading", "downloaded", "held"),
        "editing": c("editing", "edited"),
        "captioning": c("captioning"),
        "review": c("captioned_review"),
        "upload_wait": c("captioned", "uploading"),
        "uploaded": c("uploaded"),
        "failed": sum(v for k, v in counts.items() if k.startswith("failed_")),
    }
    ws, we = local_day_bounds(-((now().astimezone(C.tz()).weekday())))
    upcoming = [dict(r) for r in M.db().execute(
        "SELECT i.id, i.title, i.publish_at, i.status, f.thumbnail_url FROM items i LEFT JOIN feed_items f "
        "ON f.id=i.feed_item_id WHERE i.publish_mode='scheduled' AND i.status NOT IN ('uploaded','skipped') "
        "ORDER BY i.publish_at LIMIT 10")]
    failures = [dict(r) for r in M.db().execute(
        "SELECT i.id, i.title, i.status, i.error, i.updated_at, f.author FROM items i LEFT JOIN feed_items f "
        "ON f.id=i.feed_item_id WHERE i.status LIKE 'failed_%' ORDER BY i.updated_at DESC LIMIT 5")]
    usage = M.usage_on(local_today())
    return {
        "cards": cards, "counts": counts,
        "uploads_today": uploads_between(*local_day_bounds(0)),
        "uploads_week": uploads_between(ws, ws + timedelta(days=7)),
        "free_slots_7d": scheduler.free_slot_count(7),
        "upcoming": upcoming, "failures": failures,
        "ai_usage": usage,
        "ai_fallbacks": sum(v.get("fallbacks", 0) for v in usage.values()),
        "workers": [w.status() for w in _workers().values()],
    }


@router.post("/system/workers/{name}/{action}")
def worker_control(name: str, action: str):
    ws = _workers()
    if name == "all":
        targets = list(ws.values())
    elif name in ws:
        targets = [ws[name]]
    else:
        raise HTTPException(404, "워커가 없습니다")
    for w in targets:
        if action == "start":
            w.start()
        elif action == "stop":
            w.stop()
        else:
            raise HTTPException(400, "start 또는 stop")
    return {"workers": [w.status() for w in ws.values()]}


# ---------------------------------------------------------------- Ollama
@router.get("/system/ollama/models")
def ollama_models():
    try:
        models = ollama.list_models()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Ollama 연결 실패: {e}")
    _cache.pop("ollama", None)
    return {"models": [{"name": m.get("name"), "size": m.get("size"),
                        "parameter_size": (m.get("details") or {}).get("parameter_size"),
                        "modified_at": m.get("modified_at")} for m in models],
            "current": C.get_config()["ai"]["ollama"]["model"]}


class PullIn(BaseModel):
    model: str


_pull_lock = threading.Lock()


@router.post("/system/ollama/pull")
def ollama_pull(body: PullIn):
    if not _pull_lock.acquire(blocking=False):
        raise HTTPException(409, "이미 모델을 받는 중입니다")

    def run():
        try:
            def on(p):
                M.kv_set("ollama_pull", {**p, "running": True})
                events.emit("ollama_pull", {**p, "running": True})
            ollama.pull(body.model, on)
            done = {"model": body.model, "status": "success", "pct": 100, "running": False}
        except Exception as e:  # noqa: BLE001
            done = {"model": body.model, "status": f"실패: {e}", "pct": None, "running": False, "error": True}
        finally:
            _pull_lock.release()
            _cache.pop("ollama", None)
        M.kv_set("ollama_pull", done)
        events.emit("ollama_pull", done)

    threading.Thread(target=run, daemon=True).start()
    return {"started": body.model}


# ---------------------------------------------------------------- 로그
@router.get("/system/logs")
def logs(worker: str | None = None, level: str | None = None, tail: int = 200, item_id: int | None = None,
         before_id: int | None = None):
    where, params = [], []
    if worker:
        where.append("worker=?")
        params.append(worker)
    if level:
        order = ["debug", "info", "warning", "error"]
        if level in order:
            allowed = order[order.index(level):]
            where.append(f"level IN ({','.join('?' for _ in allowed)})")
            params += allowed
    if item_id:
        where.append("item_id=?")
        params.append(item_id)
    if before_id:
        where.append("id < ?")
        params.append(before_id)
    sql = "SELECT * FROM events " + ("WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?"
    rows = [dict(r) for r in M.db().execute(sql, (*params, min(tail, 2000)))]
    return {"logs": rows[::-1]}


@router.get("/system/logs/download")
def logs_download(worker: str = "captioner"):
    p = C.DIRS["logs"] / f"{Path(worker).name}.log"
    if not p.exists():
        raise HTTPException(404, "로그 파일이 없습니다")
    return FileResponse(p, filename=p.name, media_type="text/plain; charset=utf-8")


# ---------------------------------------------------------------- 저장 공간
def dir_size(d: Path) -> tuple[int, int]:
    total = count = 0
    for root, _, files in os.walk(d):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
                count += 1
            except OSError:
                pass
    return total, count


@router.get("/system/storage")
def storage_usage():
    out = {}
    for k in ("inbox", "work", "ready", "done"):
        size, count = dir_size(C.DIRS[k])
        out[k] = {"bytes": size, "files": count}
    import shutil
    du = shutil.disk_usage(C.ROOT)
    return {"dirs": out, "disk": {"total": du.total, "free": du.free},
            "done_keep_days": C.get_config()["storage"]["done_keep_days"]}


class CleanupIn(BaseModel):
    days: int | None = None


@router.post("/system/cleanup")
def cleanup(body: CleanupIn):
    import shutil
    days = body.days or int(C.get_config()["storage"]["done_keep_days"])
    cutoff = now() - timedelta(days=days)
    removed, freed = 0, 0
    for p in C.DIRS["done"].glob("*"):
        if p.is_file() and p.stat().st_mtime < cutoff.timestamp():
            freed += p.stat().st_size
            p.unlink()
            removed += 1
    # 업로드가 끝난 지 오래된 항목의 원본·작업 파일
    for r in M.db().execute("SELECT id, source_path, edited_path FROM items WHERE status='uploaded' AND uploaded_at < ?",
                            (iso(cutoff),)).fetchall():
        for p in (r["source_path"], r["edited_path"]):
            if p and Path(p).exists():
                freed += Path(p).stat().st_size
                Path(p).unlink()
                removed += 1
        wd = C.DIRS["work"] / str(r["id"])
        if wd.exists():
            freed += dir_size(wd)[0]
            shutil.rmtree(wd, ignore_errors=True)
    return {"removed": removed, "freed_bytes": freed, "days": days}


# ---------------------------------------------------------------- 백업·복원
BACKUP_DIRS = ["presets", "prompts", ".history", "assets"]


def backup_bytes() -> bytes:
    buf = io.BytesIO()
    with tempfile.TemporaryDirectory() as td:
        snap = Path(td) / "pipeline.sqlite"
        dst = sqlite3.connect(snap)
        M.db().backup(dst)
        dst.close()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(snap, "db/pipeline.sqlite")
            if C.CONFIG_PATH.exists():
                z.write(C.CONFIG_PATH, "config.yaml")
            sel = C.DIRS["extension"] / "upload" / "selectors.json"
            if sel.exists():
                z.write(sel, "chrome-extension/upload/selectors.json")
            for d in BACKUP_DIRS:
                for root, _, files in os.walk(C.ROOT / d):
                    for f in files:
                        full = Path(root) / f
                        if full.name.startswith("pre-restore-"):
                            continue
                        z.write(full, full.relative_to(C.ROOT).as_posix())
    return buf.getvalue()


@router.get("/system/backup")
def backup():
    name = f"autoset-backup-{local_today()}.zip"
    return Response(backup_bytes(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/system/restore")
async def restore(file: UploadFile = File(...)):
    from .. import storage
    data = await file.read()
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise HTTPException(400, "zip 파일이 아닙니다")
    names = z.namelist()
    allowed_prefix = tuple(d + "/" for d in BACKUP_DIRS)
    for n in names:
        if n.startswith("/") or ".." in Path(n).parts:
            raise HTTPException(400, f"잘못된 경로: {n}")
    ws = _workers()
    was_running = [w.name for w in ws.values() if w.running]
    for w in ws.values():
        w.stop()
    # 복원 직전 상태를 .history/pre-restore-*.zip 으로 남겨 둔다
    safety = C.DIRS["config_history"] / f"pre-restore-{now().strftime('%Y%m%d-%H%M%S')}.zip"
    safety.write_bytes(backup_bytes())
    restored = []
    for n in names:
        if n.endswith("/"):
            continue
        if n == "config.yaml" or n.startswith(allowed_prefix) or n == "chrome-extension/upload/selectors.json":
            target = C.ROOT / n
            storage.atomic_write_bytes(target, z.read(n))
            restored.append(n)
    if "db/pipeline.sqlite" in names:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "restore.sqlite"
            p.write_bytes(z.read("db/pipeline.sqlite"))
            src = sqlite3.connect(p)
            src.backup(M.db())
            src.close()
        restored.append("db/pipeline.sqlite")
    C.invalidate()
    M.init_db()
    for name in was_running:
        ws[name].start()
    events.emit("config", {"kind": "restore"})
    return {"restored": restored}


# ---------------------------------------------------------------- QR
@router.get("/system/qr")
def qr(url: str):
    import qrcode
    import qrcode.image.svg
    img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return Response(buf.getvalue(), media_type="image/svg+xml")


@router.get("/system/fonts")
def fonts():
    import sys
    if sys.platform == "win32":
        dirs = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"]
    elif sys.platform == "darwin":
        dirs = [Path("/System/Library/Fonts"), Path("/System/Library/Fonts/Supplemental"), Path("/Library/Fonts"),
                Path.home() / "Library" / "Fonts"]
    else:
        dirs = [Path("/usr/share/fonts"), Path.home() / ".local" / "share" / "fonts"]
    out = []
    seen = set()
    for d in dirs:
        if not d.is_dir():
            continue
        pattern = "**/*" if sys.platform not in ("win32", "darwin") else "*"
        for p in sorted(d.glob(pattern)):
            if p.suffix.lower() in (".ttf", ".otf", ".ttc") and p.name not in seen:
                seen.add(p.name)
                out.append({"path": p.as_posix(), "name": p.name})
    for p in sorted(C.DIRS["assets"].glob("*")):
        if p.suffix.lower() in (".ttf", ".otf", ".ttc"):
            out.append({"path": p.as_posix(), "name": f"(업로드) {p.name}"})
    return {"fonts": out}


# ---------------------------------------------------------------- 파일 서빙
def _file(path: Path | None, media: str = "video/mp4"):
    if not path or not path.exists():
        raise HTTPException(404, "파일이 없습니다")
    return FileResponse(path, media_type=media)


@router.get("/files/frames/{item_id}/{name}")
def frame(item_id: int, name: str):
    return _file(C.DIRS["work"] / str(item_id) / "frames" / Path(name).name, "image/jpeg")


@router.get("/files/thumb/{feed_id}")
def thumb_file(feed_id: int):
    from .. import thumbs
    return _file(thumbs.path(feed_id), "image/jpeg")


@router.get("/files/preview/{name}")
def preview_file(name: str):
    return _file(C.DIRS["work"] / "preview" / Path(name).name)


@router.get("/files/assets/{name}")
def asset_file(name: str):
    p = C.DIRS["assets"] / Path(name).name
    import mimetypes
    return _file(p, mimetypes.guess_type(p.name)[0] or "application/octet-stream")


# 구체적인 /files/... 경로보다 뒤에 둔다 (앞에 두면 thumb·preview·assets 요청을 가로챈다)
@router.get("/files/{area}/{item_id}")
def files(area: str, item_id: int):
    item = M.get_item(item_id)
    if not item:
        raise HTTPException(404, "항목이 없습니다")
    if area == "inbox":
        return _file(Path(item["source_path"]) if item.get("source_path") else None)
    if area == "work":
        return _file(Path(item["edited_path"]) if item.get("edited_path") else None)
    if area in ("ready", "final"):
        return _file(Path(item["final_path"]) if item.get("final_path") else None)
    raise HTTPException(404, "알 수 없는 영역")


# ---------------------------------------------------------------- 인증
class LoginIn(BaseModel):
    token: str


@public.post("/auth/login")
def login(body: LoginIn, response: Response):
    if not auth.token_ok(body.token):
        raise HTTPException(401, "토큰이 올바르지 않습니다")
    response.set_cookie(auth.COOKIE, body.token, max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax")
    return {"ok": True}


@public.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE)
    return {"ok": True}


@public.get("/auth/desktop")
def desktop_login(request: Request, ticket: str = ""):
    from .. import desktop_access
    peer = request.client.host if request.client else ""
    if not desktop_access.consume(ticket, peer):
        raise HTTPException(401, "유효한 로컬 실행 티켓이 필요합니다")
    response = RedirectResponse("/ui/", status_code=303)
    response.set_cookie(auth.COOKIE, C.get_config()["ui"]["token"], httponly=True, samesite="lax")
    response.headers["Cache-Control"] = "no-store"
    return response


@public.get("/health")
def health():
    return {"ok": True, "time": now_iso()}


@router.get("/auth/youtube/url")
def yt_url():
    try:
        return {"url": youtube.auth_url(), "redirect_uri": youtube.redirect_uri()}
    except FileNotFoundError as e:
        raise HTTPException(409, str(e))


@public.get("/auth/youtube/callback")
def yt_callback(request: Request):
    if request.query_params.get("error"):
        return HTMLResponse(f"<p>연결 취소: {request.query_params.get('error')}</p>")
    try:
        # oauthlib는 https를 기대하므로 요청 URL을 그대로 넘긴다 (OAUTHLIB_INSECURE_TRANSPORT)
        info = youtube.finish(str(request.url))
    except Exception as e:  # noqa: BLE001
        return HTMLResponse(f"<meta charset='utf-8'><p>YouTube 연결 실패: {e}</p>", status_code=400)
    _cache.pop("youtube", None)
    events.emit("config", {"kind": "youtube"})
    return RedirectResponse(f"/ui/#/upload?connected={info.get('title') or ''}")


@router.delete("/auth/youtube")
def yt_disconnect():
    youtube.disconnect()
    _cache.pop("youtube", None)
    return {"ok": True}
