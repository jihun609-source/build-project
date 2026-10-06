"""업로드 워커 (A안: YouTube Data API, D안: Selenium).

- 대상: status=captioned (captioned_review 제외), next_attempt_at 이 지났거나 없는 항목
- 예약 시각이 빠른 항목 우선
- immediate → config 공개 범위로 바로 게시 / scheduled → private + publishAt
- 예약 시각이 이미 지났으면 즉시 게시로 전환하고 events에 기록
- 확장프로그램 모드일 때는 업로드하지 않고, 멈춘 확장프로그램 업로드만 정리
"""
from __future__ import annotations

import re
import shutil
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

from .. import config as C
from .. import models as M
from ..timeutil import iso, next_pacific_midnight, now, now_iso, pacific_day_bounds, parse
from .base import Worker, log

UPLOAD_ORDER = "COALESCE(publish_at, created_at), id"
READY_WHERE = "AND needs_review=0 AND (next_attempt_at IS NULL OR next_attempt_at <= ?)"
PAST_MARGIN = timedelta(minutes=5)


def upload_metadata(item: dict[str, Any]) -> dict[str, Any]:
    cfg = C.get_config()["upload"]
    feed = M.get_feed(item["feed_item_id"]) if item.get("feed_item_id") else None
    desc = (item.get("description") or "").strip()
    # 직접 올린 새 영상(manual://)은 출처가 없으므로 출처 문구를 넣지 않는다
    if cfg.get("credit_source", True) and feed and not str(feed.get("url", "")).startswith("manual://"):
        author = (feed.get("author") or "").lstrip("@")
        credit = f"원본: @{author} / {feed['url']}" if author else f"원본: {feed['url']}"
        if credit not in desc:
            desc = (desc + "\n\n" + credit).strip()
    title = re.sub(r"[<>]", "", (item.get("title") or "").strip())[:100] or "제목 없음"
    tags = [t for t in (item.get("tags") or []) if t][:30]
    return {"title": title, "description": desc[:5000], "tags": tags}


def resolve_publish_now(item: dict[str, Any], worker: str) -> dict[str, Any]:
    """업로드 직전 publish 해석. 지난 예약은 즉시로 바꿔 DB에 반영."""
    if item.get("publish_mode") == "scheduled" and item.get("publish_at"):
        at = parse(item["publish_at"])
        if at <= now() + PAST_MARGIN:
            log(worker, "warning", f"예약 시각({item['publish_at']})이 지나 즉시 게시로 전환", item["id"], "upload")
            M.update_item(item["id"], publish_mode="immediate", publish_at=None, slot_assigned=0)
            return {"mode": "immediate", "at": None}
        return {"mode": "scheduled", "at": at}
    return {"mode": "immediate", "at": None}


def api_uploads_today() -> int:
    start, end = pacific_day_bounds()
    row = M.db().execute(
        "SELECT COUNT(*) AS n FROM items WHERE upload_mode='api' AND uploaded_at >= ? AND uploaded_at < ?",
        (iso(start), iso(end))).fetchone()
    return row["n"]


def finish_uploaded(item_id: int, video_id: str, mode: str) -> None:
    item = M.get_item(item_id)
    final = Path(item["final_path"]) if item and item.get("final_path") else None
    new_path = None
    if final and final.exists():
        dst = C.DIRS["done"] / final.name
        shutil.move(str(final), dst)
        new_path = str(dst)
    M.update_item(item_id, status="uploaded", video_id=video_id, upload_mode=mode, uploaded_at=now_iso(),
                  progress_pct=100, stage_detail=None, error=None,
                  **({"final_path": new_path} if new_path else {}))
    feed_id = item.get("feed_item_id") if item else None
    log("uploader", "info", f"업로드 완료 https://youtu.be/{video_id}", item_id, "upload")
    if feed_id:
        M.db().execute("UPDATE feed_items SET status='fetched' WHERE id=?", (feed_id,))


class QuotaExceeded(Exception):
    pass


class Uploader(Worker):
    name = "uploader"
    stage = "upload"
    fail_status = "failed_upload"

    def tick(self) -> bool:
        cfg = C.get_config()["upload"]
        self._cleanup_stale(cfg)
        mode = cfg.get("mode")
        if mode == "selenium":
            from .. import selenium_accounts as SA
            entry = SA.account(cfg["selenium"])
            state = SA.account_status(entry)
            if state.get("needs_login") or (cfg["selenium"].get("accounts") and not state.get("verified")):
                return False
            if not SA.BROWSER_LOCK.acquire(blocking=False):
                return False
            try:
                item = M.claim_item("captioned", "uploading", "upload", READY_WHERE, (now_iso(),), UPLOAD_ORDER)
                if not item:
                    return False
                M.update_item(item["id"], upload_mode="selenium", upload_account_id=entry["id"])
                item.update(upload_mode="selenium", upload_account_id=entry["id"], _upload_config=cfg)
                self.process_claimed(item, "captioned")
                return True
            finally:
                SA.BROWSER_LOCK.release()
        if mode != "api":
            return False
        from .. import youtube
        if not youtube.TOKEN_PATH.exists():
            # 계정 연결 전에는 항목을 실패시키지 않고 대기
            if not getattr(self, "_warned_no_token", False):
                self.log("warning", "YouTube 계정이 연결되지 않아 업로드 대기 중 (업로드 설정 → 연결하기)")
                self._warned_no_token = True
            return False
        self._warned_no_token = False
        limit = int(cfg.get("daily_limit", 6))
        if limit and api_uploads_today() >= limit:
            return False
        item = M.claim_item("captioned", "uploading", "upload", READY_WHERE, (now_iso(),), UPLOAD_ORDER)
        if not item:
            return False
        M.update_item(item["id"], upload_mode="api")
        item["upload_mode"] = "api"
        self.process_claimed(item, "captioned")
        return True

    def _cleanup_stale(self, cfg: dict) -> None:
        minutes = float(cfg.get("extension", {}).get("stale_minutes", 30))
        cutoff = iso(now() - timedelta(minutes=minutes))
        rows = M.db().execute("SELECT id FROM items WHERE status='uploading' AND upload_mode='extension' "
                              "AND updated_at < ?", (cutoff,)).fetchall()
        for r in rows:
            M.update_item(r["id"], status="failed_upload", error=f"확장프로그램 응답 없음 ({minutes:.0f}분)")
            self.log("error", "확장프로그램 업로드가 응답이 없어 실패 처리", r["id"])

    def handle(self, item: dict[str, Any]) -> None:
        if item.get("upload_mode") == "selenium":
            try:
                from ..selenium_upload import upload
            except ImportError as e:
                raise RuntimeError("D안(Selenium) 업로드는 이 설치본에 포함되어 있지 않습니다. "
                                   "업로드 설정에서 A안(API) 또는 C안(확장프로그램)을 선택하세요.") from e
            upload(self, item)
            return
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload

        from .. import youtube

        item_id = item["id"]
        path = Path(item.get("final_path") or "")
        if not path.exists():
            raise RuntimeError(f"완성본 파일이 없습니다: {path}")
        cfg = C.get_config()["upload"]
        meta = upload_metadata(item)
        pub = resolve_publish_now(item, self.name)
        status: dict[str, Any] = {"selfDeclaredMadeForKids": bool(cfg.get("made_for_kids", False))}
        if pub["mode"] == "scheduled":
            status.update(privacyStatus="private", publishAt=iso(pub["at"]).replace("Z", ".000Z"))
        else:
            status["privacyStatus"] = cfg.get("privacy", "public")
        body = {
            "snippet": {"title": meta["title"], "description": meta["description"], "tags": meta["tags"],
                        "categoryId": str(cfg.get("category_id", "22")),
                        "defaultLanguage": cfg.get("language", "ko"),
                        "defaultAudioLanguage": cfg.get("language", "ko")},
            "status": status,
        }
        yt = youtube.service()
        media = MediaFileUpload(str(path), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
        req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        errors = 0
        self.progress(item_id, 0, "업로드 시작")
        while response is None:
            if self.stopping():
                from ..util.proc import Cancelled
                raise Cancelled()
            try:
                st, response = req.next_chunk(num_retries=3)
                if st:
                    self.progress(item_id, st.progress() * 100)
            except HttpError as e:
                reason = _reason(e)
                if e.resp.status == 403 and reason in ("quotaExceeded", "uploadLimitExceeded", "dailyLimitExceeded"):
                    until = iso(next_pacific_midnight())
                    M.update_item(item_id, status="captioned", next_attempt_at=until, progress_pct=0,
                                  error=f"YouTube 할당량 초과 - {until} 이후 재시도")
                    self.log("warning", f"YouTube 할당량 초과({reason}), 다음날로 미룸", item_id)
                    return
                if e.resp.status in (500, 502, 503, 504) and errors < 3:
                    errors += 1
                    time.sleep(5 * errors)
                    continue
                raise RuntimeError(f"YouTube API 오류 {e.resp.status} {reason}: {_message(e)}") from e
            except (ConnectionError, TimeoutError, OSError) as e:
                if errors < 3:
                    errors += 1
                    self.log("warning", f"업로드 연결 오류, 이어받기 재시도 {errors}/3: {e}", item_id)
                    time.sleep(5 * errors)
                    continue
                raise
        video_id = response.get("id")
        if not video_id:
            raise RuntimeError(f"응답에 video id가 없습니다: {response}")
        finish_uploaded(item_id, video_id, "api")


def _reason(e) -> str:
    try:
        import json
        d = json.loads(e.content.decode())
        return d["error"]["errors"][0].get("reason", "")
    except Exception:  # noqa: BLE001
        return ""


def _message(e) -> str:
    try:
        import json
        return json.loads(e.content.decode())["error"].get("message", "")[:300]
    except Exception:  # noqa: BLE001
        return str(e)[:300]


if __name__ == "__main__":
    from ..main import bootstrap
    bootstrap()
    Uploader().run_forever()
