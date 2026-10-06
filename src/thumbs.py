"""피드 썸네일: thumbs/{feed_id}.jpg 로 로컬 저장해 직접 서빙한다.

인스타 CDN 주소는 만료되거나 다른 사이트에서의 표시가 막히므로 쓰지 않는다. 확보 순서:
  1. 확장프로그램이 보낸 화면 캡처(data URL)
  2. 확장프로그램이 보낸 썸네일 주소를 서버가 직접 받기
  3. 둘 다 없으면 백그라운드에서 yt-dlp로 표지 이미지 주소를 조회해 받기 (한 건씩, 최대 3회)
  4. 영상을 다운로드하면 영상 프레임으로 다시 만들기
"""
from __future__ import annotations

import base64
import io
import threading
import time
from pathlib import Path

import httpx
from PIL import Image

from . import config as C
from . import events
from . import models as M
from .util import proc

MAX_SIDE = 480
MAX_ATTEMPTS = 3
_wake = threading.Event()
_thread: threading.Thread | None = None


def path(feed_id: int) -> Path:
    return C.DIRS["thumbs"] / f"{feed_id}.jpg"


def url(feed_id: int) -> str | None:
    p = path(feed_id)
    return f"/files/thumb/{feed_id}?v={int(p.stat().st_mtime)}" if p.exists() else None


def _save_image(feed_id: int, data: bytes) -> bool:
    try:
        with Image.open(io.BytesIO(data)) as im:
            im = im.convert("RGB")
            if im.width < 16 or im.height < 16:
                return False
            im.thumbnail((MAX_SIDE, MAX_SIDE))
            C.DIRS["thumbs"].mkdir(parents=True, exist_ok=True)
            tmp = path(feed_id).with_suffix(".tmp.jpg")
            im.save(tmp, "JPEG", quality=82)
            tmp.replace(path(feed_id))
        return True
    except Exception:  # noqa: BLE001
        return False


def save_data_url(feed_id: int, data_url: str) -> bool:
    if not data_url or "," not in data_url:
        return False
    try:
        raw = base64.b64decode(data_url.split(",", 1)[1])
    except ValueError:
        return False
    return len(raw) < 3_000_000 and _save_image(feed_id, raw)


def fetch_url(feed_id: int, image_url: str) -> bool:
    if not image_url or not image_url.startswith("http"):
        return False
    try:
        r = httpx.get(image_url, timeout=20, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.instagram.com/"})
        return r.status_code == 200 and _save_image(feed_id, r.content)
    except httpx.HTTPError:
        return False


def from_video(feed_id: int, video: Path, at: float = 1.0) -> bool:
    C.DIRS["thumbs"].mkdir(parents=True, exist_ok=True)
    tmp = path(feed_id).with_suffix(".frame.jpg")
    try:
        r = proc.run([*proc.tool("ffmpeg"), "-hide_banner", "-nostdin", "-y", "-ss", f"{at:.2f}", "-i", str(video),
                      "-frames:v", "1", "-vf", f"scale={MAX_SIDE}:-2", str(tmp)], timeout=60)
    except proc.ProcError:
        return False
    if r.returncode != 0 or not tmp.exists():
        return False
    ok = _save_image(feed_id, tmp.read_bytes())
    tmp.unlink(missing_ok=True)
    return ok


def lookup_with_ytdlp(page_url: str) -> str | None:
    try:
        r = proc.run([*proc.tool("yt-dlp"), "--skip-download", "--no-warnings", "--no-playlist",
                      "--socket-timeout", "20", "--print", "thumbnail", page_url], timeout=60)
    except proc.ProcError:
        return None
    line = (r.stdout or "").strip().splitlines()
    return line[-1] if r.returncode == 0 and line and line[-1].startswith("http") else None


def mark(feed_id: int) -> None:
    """썸네일이 생겼음을 UI에 알린다."""
    from .routes.feed import feed_row
    M.db().execute("UPDATE feed_items SET thumb_attempts=? WHERE id=?", (MAX_ATTEMPTS, feed_id))
    events.emit("feed", feed_row(feed_id))


# ---------------------------------------------------------------- 백그라운드 보충
def _loop() -> None:
    M.db()
    while True:
        row = M.db().execute(
            "SELECT id, url, thumbnail_url FROM feed_items WHERE thumb_attempts < ? ORDER BY id DESC LIMIT 1",
            (MAX_ATTEMPTS,)).fetchone()
        if not row:
            _wake.wait(60)
            _wake.clear()
            continue
        fid = row["id"]
        if path(fid).exists():
            mark(fid)
            continue
        M.db().execute("UPDATE feed_items SET thumb_attempts=thumb_attempts+1 WHERE id=?", (fid,))
        ok = fetch_url(fid, row["thumbnail_url"]) if row["thumbnail_url"] else False
        if not ok:
            img = lookup_with_ytdlp(row["url"])
            ok = fetch_url(fid, img) if img else False
        if ok:
            mark(fid)
        time.sleep(2)   # 인스타 요청 간격


def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, name="thumbs", daemon=True)
    _thread.start()


def wake() -> None:
    _wake.set()
