"""워커 공통: DB 폴링 루프, 시작·정지, 로그(파일 + events 테이블 + WebSocket)."""
from __future__ import annotations

import logging
import threading
import time
import traceback
from logging.handlers import RotatingFileHandler
from typing import Any

from .. import config as C
from .. import events
from .. import models as M
from ..timeutil import now_iso
from ..util.proc import Cancelled

LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}


def file_logger(name: str) -> logging.Logger:
    lg = logging.getLogger(f"autoset.{name}")
    if not lg.handlers:
        C.DIRS["logs"].mkdir(parents=True, exist_ok=True)
        h = RotatingFileHandler(C.DIRS["logs"] / f"{name}.log", maxBytes=5_000_000, backupCount=3,
                                encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        lg.addHandler(h)
        lg.setLevel(logging.DEBUG)
        lg.propagate = False
    return lg


def log(worker: str, level: str, message: str, item_id: int | None = None, stage: str | None = None,
        to_db: bool = True) -> None:
    lg = file_logger(worker)
    prefix = f"[#{item_id}] " if item_id else ""
    lg.log(LEVELS.get(level, logging.INFO), prefix + message)
    if to_db:
        try:
            M.add_event(item_id, worker, stage, level, message)
        except Exception:  # noqa: BLE001
            lg.exception("이벤트 기록 실패")


class Worker:
    name = "worker"
    stage = ""            # download / edit / caption / upload
    fail_status = ""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.current_item: int | None = None
        self.started_at: str | None = None
        self.last_error: str | None = None
        self.processed = 0

    # ---------------------------------------------------------- 제어
    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name=self.name, daemon=True)
        self.started_at = now_iso()
        self._thread.start()
        self.log("info", "워커 시작")
        self._emit_state()

    def stop(self) -> None:
        """현재 처리 중인 외부 프로세스는 중단하고 항목은 이전 단계로 되돌린다."""
        if not self.running:
            return
        self._stop.set()
        self.log("info", "워커 정지 요청")
        self._emit_state()

    def stopping(self) -> bool:
        return self._stop.is_set()

    def status(self) -> dict[str, Any]:
        return {"name": self.name, "running": self.running, "stopping": self._stop.is_set() and self.running,
                "current_item": self.current_item, "started_at": self.started_at,
                "last_error": self.last_error, "processed": self.processed}

    def _emit_state(self) -> None:
        events.emit("worker", self.status())

    # ---------------------------------------------------------- 루프
    def _loop(self) -> None:
        M.db()
        while not self._stop.is_set():
            did = False
            try:
                did = self.tick()
            except Exception as e:  # noqa: BLE001
                self.last_error = str(e)
                self.log("error", f"루프 오류: {e}\n{traceback.format_exc()}")
            finally:
                if self.current_item is not None:
                    self.current_item = None
                    self._emit_state()
            if not did:
                interval = float(C.get_config()["workers"].get("poll_interval_sec", 3))
                self._stop.wait(interval)
        self.log("info", "워커 정지됨")
        self._emit_state()

    def run_forever(self) -> None:
        """단독 실행용 (python -m src.workers.xxx)."""
        self._stop.clear()
        self.started_at = now_iso()
        self.log("info", "워커 단독 실행")
        try:
            self._loop()
        except KeyboardInterrupt:
            pass

    def tick(self) -> bool:
        raise NotImplementedError

    # ---------------------------------------------------------- 공통 처리
    def process_claimed(self, item: dict[str, Any], back_status: str) -> None:
        """claim한 항목을 handle()로 처리. 실패는 failed_{stage}, 중지는 back_status로."""
        item_id = item["id"]
        self.current_item = item_id
        self._emit_state()
        t0 = time.monotonic()
        try:
            self.handle(item)
            self.processed += 1
            self.log("info", f"완료 ({time.monotonic() - t0:.1f}초)", item_id)
        except Cancelled:
            M.update_item(item_id, status=back_status, progress_pct=0, stage_detail=None)
            self.log("warning", "중지되어 이전 단계로 되돌림", item_id)
        except Exception as e:  # noqa: BLE001
            msg = str(e) or e.__class__.__name__
            self.last_error = msg
            cur = M.get_item(item_id) or {}
            M.update_item(item_id, status=self.fail_status, error=msg, attempts=(cur.get("attempts") or 0) + 1)
            self.log("error", f"실패: {msg}", item_id)
            file_logger(self.name).debug(traceback.format_exc())

    def handle(self, item: dict[str, Any]) -> None:
        raise NotImplementedError

    def log(self, level: str, message: str, item_id: int | None = None) -> None:
        log(self.name, level, message, item_id, self.stage or None)

    def progress(self, item_id: int, pct: float, detail: str | None = None) -> None:
        M.set_progress(item_id, pct, detail)
