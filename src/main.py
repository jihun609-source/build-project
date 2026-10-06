"""서버 + 워커를 한 번에 띄우는 진입점.

    python -m src.main            # API(8000) + 모든 워커
    python -m src.workers.editor  # 워커 하나만 단독 실행
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

from . import config as C

_booted = False


def bootstrap() -> None:
    global _booted
    if _booted:
        return
    os.environ.setdefault("PYTHONUTF8", "1")
    load_dotenv(C.ROOT / ".env")
    from . import models, storage
    storage.ensure_initial_files()
    models.init_db()
    _booted = True


_workers: dict = {}


def workers() -> dict:
    """이름 → 워커 인스턴스 (API 프로세스 안에서 스레드로 실행)."""
    if not _workers:
        from .workers.captioner import Captioner
        from .workers.downloader import Downloader
        from .workers.editor import Editor
        from .workers.uploader import Uploader
        for w in (Downloader(), Editor(), Captioner(), Uploader()):
            _workers[w.name] = w
    return _workers


def main() -> None:
    bootstrap()
    from .server_runtime import run_server
    cfg = C.get_config()
    host = cfg["server"].get("host", "0.0.0.0")
    port = int(cfg["server"].get("port", 8000))
    print(f"AutoSet 서버: http://127.0.0.1:{port}/ui  (토큰: config.yaml ui.token)", flush=True)
    try:
        run_server("src.api:app", host=host, port=port, log_level="info", access_log=False)
    except Exception:
        from .workers.base import file_logger
        file_logger("api").exception("서버 실행 오류로 종료됨")
        raise


if __name__ == "__main__":
    sys.exit(main())
