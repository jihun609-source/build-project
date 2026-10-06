"""설치본 진입점. 실행/종료 창, 브라우저 열기, 번들 yt-dlp 및 오프라인 진단."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

from src import config as C


def attach_stdio():
    for name, fd in (("stdout", 1), ("stderr", 2)):
        if getattr(sys, name) is None:
            try:
                stream = open(fd, "w", encoding="utf-8", buffering=1, closefd=False)
            except OSError:
                if sys.platform == "win32":
                    import ctypes
                    import msvcrt
                    kernel = ctypes.WinDLL("kernel32")
                    kernel.GetStdHandle.restype = ctypes.c_void_p
                    handle = kernel.GetStdHandle(-11 if fd == 1 else -12)
                    try:
                        descriptor = msvcrt.open_osfhandle(handle, os.O_WRONLY)
                        stream = os.fdopen(descriptor, "w", encoding="utf-8", buffering=1)
                    except (OSError, TypeError):
                        stream = open(os.devnull, "w", encoding="utf-8")
                else:
                    stream = open(os.devnull, "w", encoding="utf-8")
            setattr(sys, name, stream)


def choose_port(preferred: int) -> int:
    for port in range(preferred, min(65536, preferred + 50)):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
                return probe.getsockname()[1]
            except OSError:
                pass
    raise RuntimeError("사용 가능한 포트가 없습니다. 설정의 서버 포트를 변경하세요.")


class InstanceLock:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("a+b")
        if self.file.seek(0, 2) == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)

    def acquire(self):
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def close(self):
        self.file.close()


def self_test() -> dict:
    """실제 서버·워커·모델 다운로드를 시작하지 않는 설치본 검사."""
    import importlib
    from src.util.proc import run, tool
    checks = {}
    for module in ("src.api", "faster_whisper", "ctranslate2", "av", "onnxruntime", "tkinter",
                   "google.genai", "anthropic", "yt_dlp", "undetected_chromedriver"):
        try:
            importlib.import_module(module)
            checks[module] = True
        except Exception as e:
            checks[module] = str(e)
    checks["web"] = (C.DIRS["web_dist"] / "index.html").is_file()
    checks["extension"] = (C.APP_ROOT / "chrome-extension" / "manifest.json").is_file()
    try:
        import tkinter
        interpreter = tkinter.Tcl()
        checks["tcl_runtime"] = bool(interpreter.eval("info patchlevel"))
        window = tkinter.Tk()
        window.withdraw()
        window.update_idletasks()
        window.destroy()
        checks["tk_window"] = True
    except Exception as e:
        checks["tcl_runtime"] = str(e)
    try:
        from faster_whisper.vad import get_vad_model
        checks["whisper_vad"] = get_vad_model() is not None
    except Exception as e:
        checks["whisper_vad"] = str(e)
    for name in ("ffmpeg", "ffprobe"):
        try:
            result = run(tool(name) + ["-version"], timeout=20)
            checks[name] = result.returncode == 0
        except Exception as e:
            checks[name] = str(e)
    try:
        result = run(tool("ffmpeg") + ["-hide_banner", "-filters"], timeout=20)
        checks["subtitles"] = result.returncode == 0 and "subtitles" in result.stdout
    except Exception as e:
        checks["subtitles"] = str(e)
    try:
        import tempfile
        from src.util.proc import ffmpeg_filter_path, probe
        with tempfile.TemporaryDirectory(prefix="autoset-self-test-") as folder:
            directory = Path(folder)
            subtitle = directory / "test.ass"
            subtitle.write_text(
                "[Script Info]\nScriptType: v4.00+\nPlayResX: 160\nPlayResY: 284\n"
                "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
                "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, "
                "Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
                "Style: Default,Arial,16,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,"
                "100,100,0,0,1,1,0,2,10,10,10,1\n"
                "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
                "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,AutoSet 테스트\n", encoding="utf-8")
            video = directory / "sample.mp4"
            result = run(tool("ffmpeg") + ["-hide_banner", "-y", "-f", "lavfi", "-i",
                "color=c=black:s=160x284:d=0.1:r=10", "-vf", f"subtitles='{ffmpeg_filter_path(subtitle)}'",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], timeout=30)
            info = probe(video) if result.returncode == 0 else {}
            checks["video_render"] = info.get("vcodec") == "h264" and info.get("width") == 160
            if result.returncode:
                checks["video_render"] = result.stderr[-500:]
    except Exception as e:
        checks["video_render"] = str(e)
    return {"ok": all(v is True for v in checks.values()), "checks": checks,
            "platform": sys.platform, "data_directory": str(C.ROOT)}


def desktop():
    import tkinter as tk
    from tkinter import ttk, messagebox
    from src.main import bootstrap
    from src import desktop_access, storage
    import uvicorn

    bootstrap()
    lock = InstanceLock(C.ROOT / ".desktop.lock")
    cfg = C.get_config()
    if not lock.acquire():
        lock.close()
        webbrowser.open(f"http://127.0.0.1:{cfg['server']['port']}/ui/")
        return 0
    root = tk.Tk()
    root.title("AutoSet")
    root.geometry("540x265")
    root.resizable(False, False)
    status = tk.StringVar(value="프로그램을 시작하는 중입니다…")
    ttk.Label(root, text="AutoSet", font=("", 20, "bold")).pack(pady=(20, 8))
    ttk.Label(root, textvariable=status, wraplength=500).pack(pady=4)
    ttk.Label(root, text="이 창을 닫거나 종료를 누르면 작업과 서버가 종료됩니다.").pack(pady=6)
    frame = ttk.Frame(root)
    frame.pack(pady=12)
    server = None
    thread = None
    error = []

    def open_ui():
        webbrowser.open(f"http://127.0.0.1:{port}/auth/desktop?ticket={desktop_access.issue()}")

    def open_folder(path):
        if sys.platform == "win32":
            os.startfile(path)
        else:
            import subprocess
            subprocess.Popen(["/usr/bin/open", str(path)])

    def shutdown():
        if server:
            server.should_exit = True
        status.set("진행 중인 작업을 정리하고 종료하는 중입니다…")
        opener.configure(state="disabled")
        closer.configure(state="disabled")
        deadline = time.monotonic() + 15

        def wait_stop():
            if thread and thread.is_alive() and time.monotonic() < deadline:
                root.after(150, wait_stop)
            else:
                lock.close()
                root.destroy()
        wait_stop()

    opener = ttk.Button(frame, text="관리 화면 열기", command=open_ui, state="disabled")
    opener.pack(side="left", padx=4)
    ttk.Button(frame, text="데이터 폴더", command=lambda: open_folder(C.ROOT)).pack(side="left", padx=4)
    ttk.Button(frame, text="확장프로그램 폴더", command=lambda: open_folder(C.DIRS["extension"])).pack(side="left", padx=4)
    closer = ttk.Button(root, text="종료", command=shutdown)
    closer.pack()
    root.protocol("WM_DELETE_WINDOW", shutdown)
    try:
        port = choose_port(int(cfg["server"]["port"]))
        if port != int(cfg["server"]["port"]):
            storage.patch_config({"server": {"port": port}})
        config = uvicorn.Config("src.api:app", host=cfg["server"].get("host", "0.0.0.0"), port=port,
                                loop="asyncio", log_config=None, access_log=False)
        server = uvicorn.Server(config)

        def serve():
            try:
                from src.server_runtime import windows_server_loop
                factory = windows_server_loop if sys.platform == "win32" else asyncio.new_event_loop
                with asyncio.Runner(loop_factory=factory) as runner:
                    runner.run(server.serve())
            except Exception as e:
                error.append(str(e))
                from src.workers.base import file_logger
                file_logger("api").exception("설치본 서버 시작 실패")
        thread = threading.Thread(target=serve, name="desktop-server", daemon=True)
        thread.start()

        def ready():
            if server.started:
                status.set(f"실행 중 · http://127.0.0.1:{port}/ui/")
                opener.configure(state="normal")
                open_ui()
            elif error or not thread.is_alive():
                status.set("시작하지 못했습니다. 데이터 폴더의 logs/api.log를 확인하세요.")
                messagebox.showerror("AutoSet 실행 오류", error[0] if error else "서버가 종료됐습니다")
            else:
                root.after(200, ready)
        ready()
    except Exception as e:
        status.set("시작하지 못했습니다")
        messagebox.showerror("AutoSet 실행 오류", str(e))
    root.mainloop()
    return 0


def main(argv=None):
    import multiprocessing
    multiprocessing.freeze_support()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--run-yt-dlp":
        attach_stdio()
        import yt_dlp
        yt_dlp.main(argv[1:])
        return 0
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    attach_stdio()
    if args.self_test:
        result = self_test()
        text = json.dumps(result, ensure_ascii=False, indent=2)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(text, encoding="utf-8")
        print(text, flush=True)
        return 0 if result["ok"] else 1
    try:
        return desktop()
    except Exception:
        C.ROOT.mkdir(parents=True, exist_ok=True)
        (C.ROOT / "startup-error.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
