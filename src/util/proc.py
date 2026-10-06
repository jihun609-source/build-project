"""외부 실행 파일(ffmpeg, ffprobe, yt-dlp) 호출 공통."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Sequence

from .. import config as C

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class ProcError(RuntimeError):
    pass


class Cancelled(RuntimeError):
    pass


def tool(name: str) -> list[str]:
    """config.paths 에 지정된 경로 → PATH → (yt-dlp) 현재 파이썬 모듈 순으로 찾는다."""
    key = {"yt-dlp": "yt_dlp"}.get(name, name)
    configured = C.get_config().get("paths", {}).get(key)
    if configured:
        return [str(C.root_path(configured))]
    exe = name + (".exe" if os.name == "nt" else "")
    # 설치본: 같이 넣은 ffmpeg/ffprobe 우선
    bundled = C.APP_ROOT / "bin" / exe
    if bundled.exists():
        return [str(bundled)]
    if name == "yt-dlp" and C.FROZEN:
        # 설치본에는 별도 파이썬이 없으므로 프로그램 실행 파일의 yt-dlp 모드를 쓴다 (launcher.py)
        return [sys.executable, "--run-yt-dlp"]
    found = shutil.which(name)
    if not found and name in ("ffmpeg", "ffprobe"):
        # ffmpeg 경로만 지정돼 있으면 같은 폴더의 ffprobe 사용
        other = C.get_config().get("paths", {}).get("ffmpeg" if name == "ffprobe" else "ffprobe")
        if other:
            cand = C.root_path(other).with_name(name + (".exe" if os.name == "nt" else ""))
            if cand.exists():
                return [str(cand)]
    if found:
        return [found]
    venv_bin = Path(sys.executable).parent / (name + (".exe" if os.name == "nt" else ""))
    if venv_bin.exists():
        return [str(venv_bin)]
    if name == "yt-dlp":
        return [sys.executable, "-m", "yt_dlp"]
    raise ProcError(f"{name} 실행 파일을 찾을 수 없습니다 (config.paths.{key} 에 경로를 지정하세요)")


def run(args: Sequence[str], timeout: float = 600, cwd: Path | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    try:
        return subprocess.run(
            list(args), capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, cwd=cwd, env=env, creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as e:
        raise ProcError(f"시간 초과 ({timeout:.0f}초): {Path(args[0]).name}") from e


def run_streaming(args: Sequence[str], on_line: Callable[[str], None], timeout: float = 1800,
                  cwd: Path | None = None, cancel: Callable[[], bool] | None = None,
                  stdout_lines: bool = True) -> tuple[int, str]:
    """stdout(또는 stderr) 줄 단위 콜백. 반환: (returncode, stderr 마지막 부분)."""
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    p = subprocess.Popen(
        list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        errors="replace", cwd=cwd, env=env, creationflags=CREATE_NO_WINDOW, bufsize=1,
    )
    err_tail: list[str] = []

    def pump_err():
        assert p.stderr
        for line in p.stderr:
            err_tail.append(line)
            if len(err_tail) > 80:
                del err_tail[:40]
            if not stdout_lines:
                try:
                    on_line(line.rstrip("\n"))
                except Exception:  # noqa: BLE001
                    pass

    t = threading.Thread(target=pump_err, daemon=True)
    t.start()
    deadline = time.monotonic() + timeout
    assert p.stdout
    try:
        for line in p.stdout:
            if stdout_lines:
                on_line(line.rstrip("\n"))
            if cancel and cancel():
                p.kill()
                raise Cancelled("중지됨")
            if time.monotonic() > deadline:
                p.kill()
                raise ProcError(f"시간 초과 ({timeout:.0f}초): {Path(args[0]).name}")
        rc = p.wait(timeout=max(1, deadline - time.monotonic()))
    except subprocess.TimeoutExpired as e:
        p.kill()
        raise ProcError(f"시간 초과 ({timeout:.0f}초): {Path(args[0]).name}") from e
    t.join(timeout=5)
    return rc, "".join(err_tail[-30:])


def retry(fn: Callable, attempts: int = 3, delay: float = 3.0, on_retry: Callable[[int, Exception], None] | None = None):
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Cancelled:
            raise
        except Exception as e:  # noqa: BLE001
            last = e
            if i < attempts - 1:
                if on_retry:
                    on_retry(i + 1, e)
                time.sleep(delay * (i + 1))
    assert last
    raise last


# ---------------------------------------------------------------- ffprobe
def probe(path: Path) -> dict:
    r = run([*tool("ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            timeout=60)
    if r.returncode != 0:
        raise ProcError(f"ffprobe 실패: {r.stderr.strip()[-300:]}")
    data = json.loads(r.stdout or "{}")
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    dur = float(data.get("format", {}).get("duration") or (v or {}).get("duration") or 0)
    fps = 30.0
    if v and v.get("avg_frame_rate") and v["avg_frame_rate"] != "0/0":
        n, d = v["avg_frame_rate"].split("/")
        fps = float(n) / float(d or 1) if float(d or 1) else 30.0
    rotate = 0
    if v:
        rotate = int((v.get("tags") or {}).get("rotate", 0) or 0)
        for sd in v.get("side_data_list", []) or []:
            if "rotation" in sd:
                rotate = int(sd["rotation"])
    w, h = (v or {}).get("width", 0), (v or {}).get("height", 0)
    if abs(rotate) in (90, 270):
        w, h = h, w
    return {
        "duration": round(dur, 3),
        "width": w, "height": h, "fps": round(fps, 3),
        "has_audio": a is not None,
        "vcodec": (v or {}).get("codec_name"), "acodec": (a or {}).get("codec_name"),
        "size": int(data.get("format", {}).get("size") or 0),
    }


def ffmpeg_filter_path(path: Path | str) -> str:
    """ffmpeg 필터 인자에 넣을 경로: 슬래시로 바꾸고 : ' \\ 이스케이프."""
    s = str(path).replace("\\", "/")
    return s.replace(":", "\\:").replace("'", "\\'")


def ffmpeg_with_progress(args: Sequence[str], total_sec: float, on_pct: Callable[[float], None],
                         timeout: float = 1800, cwd: Path | None = None,
                         cancel: Callable[[], bool] | None = None) -> None:
    """ffmpeg -progress pipe:1 출력을 파싱해 퍼센트 콜백."""
    full = [*tool("ffmpeg"), "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-nostats", *args]

    def on_line(line: str):
        if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
            try:
                us = int(line.split("=", 1)[1])
            except ValueError:
                return
            if total_sec > 0:
                on_pct(min(99.0, us / 1e6 / total_sec * 100))
        elif line.startswith("progress=end"):
            on_pct(100.0)

    rc, err = run_streaming(full, on_line, timeout=timeout, cwd=cwd, cancel=cancel)
    if rc != 0:
        raise ProcError("ffmpeg 실패: " + _tail(err))


def _tail(err: str) -> str:
    lines = [ln for ln in err.strip().splitlines() if ln.strip()]
    return " | ".join(lines[-4:])[-600:]
