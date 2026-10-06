"""다운로드 워커: fetch 요청된 항목(queued)을 yt-dlp로 inbox/{id}.mp4 에 받는다."""
from __future__ import annotations

import re
from typing import Any

from .. import config as C
from .. import models as M
from ..util import proc
from .base import Worker

PROG_RE = re.compile(r"PROG\s+([\d.]+)%")


class Downloader(Worker):
    name = "downloader"
    stage = "download"
    fail_status = "failed_download"

    def tick(self) -> bool:
        item = M.claim_item("queued", "downloading", "download")
        if not item:
            return False
        self.process_claimed(item, "queued")
        return True

    def handle(self, item: dict[str, Any]) -> None:
        item_id = item["id"]
        feed = M.get_feed(item["feed_item_id"]) if item.get("feed_item_id") else None
        if not feed:
            raise RuntimeError("연결된 피드 항목이 없습니다")
        cfg = C.get_config()
        inbox = C.DIRS["inbox"]
        target = inbox / f"{item_id}.mp4"
        for old in inbox.glob(f"{item_id}.*"):
            old.unlink(missing_ok=True)

        args = [
            *proc.tool("yt-dlp"),
            "--no-playlist", "--newline", "--no-part", "--no-mtime", "--no-warnings",
            "-f", "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b",
            "--merge-output-format", "mp4",
            "--progress-template", "download:PROG %(progress._percent_str)s",
            "--retries", "3", "--fragment-retries", "3", "--socket-timeout", "30",
            "-o", str(inbox / f"{item_id}.%(ext)s"),
        ]
        ffmpeg = cfg["paths"].get("ffmpeg")
        if ffmpeg:
            args += ["--ffmpeg-location", str(C.root_path(ffmpeg))]
        cookies = C.root_path(cfg["download"].get("cookies_file"))
        if cookies and cookies.exists():
            args += ["--cookies", str(cookies)]
        args.append(feed["url"])

        def attempt():
            def on_line(line: str):
                m = PROG_RE.search(line)
                if m:
                    # 영상·음성 두 스트림을 받으면 퍼센트가 두 번 올라가므로 95%까지만 표시
                    self.progress(item_id, float(m.group(1)) * 0.95)

            rc, err = proc.run_streaming(args, on_line, timeout=float(cfg["download"].get("timeout_sec", 600)),
                                         cancel=self.stopping)
            if rc != 0:
                raise proc.ProcError("yt-dlp 실패: " + _summarize(err))

        proc.retry(attempt, 3, 5, on_retry=lambda n, e: self.log("warning", f"다운로드 재시도 {n}/2: {e}", item_id))

        if not target.exists():
            cands = [p for p in inbox.glob(f"{item_id}.*") if p.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov")]
            if not cands:
                raise RuntimeError("다운로드된 파일을 찾을 수 없습니다")
            cands[0].rename(target)
        meta = proc.probe(target)
        if meta["duration"] <= 0:
            raise RuntimeError("영상 길이를 읽을 수 없습니다")
        # 자동 업로드를 끄고 가져온 항목은 다운로드만 하고 보류 (자막 탭에서 이어서 처리)
        next_status = "downloaded" if item.get("auto_upload", True) else "held"
        M.update_item(item_id, status=next_status, source_path=str(target), source_meta=meta,
                      source_duration=meta["duration"], progress_pct=100)
        self.log("info", f"다운로드 완료 {meta['width']}x{meta['height']} {meta['duration']:.1f}초", item_id)
        # 받은 영상 프레임으로 썸네일을 새로 만든다 (캡처·CDN 이미지보다 확실함)
        from .. import thumbs
        if thumbs.from_video(feed["id"], target, min(1.0, meta["duration"] / 3)):
            thumbs.mark(feed["id"])


def _summarize(err: str) -> str:
    lines = [ln for ln in err.splitlines() if "ERROR" in ln] or err.strip().splitlines()[-3:]
    text = " | ".join(lines)[-500:]
    if "login" in text.lower() or "cookies" in text.lower():
        text += " (로그인이 필요한 릴스일 수 있습니다: config.download.cookies_file 에 cookies.txt 지정)"
    return text


if __name__ == "__main__":
    from ..main import bootstrap
    bootstrap()
    Downloader().run_forever()
