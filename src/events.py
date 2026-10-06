"""WebSocket 브로드캐스트.

워커(스레드)는 emit()만 호출한다. API 프로세스의 이벤트 루프가 등록돼 있으면
call_soon_threadsafe 로 넘겨 연결된 모든 클라이언트에 push 하고, 없으면(워커 단독 실행)
조용히 버린다. 상태는 DB에 있으므로 UI는 폴링으로도 같은 값을 받는다.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any

from fastapi import WebSocket

_loop: asyncio.AbstractEventLoop | None = None
_clients: set[WebSocket] = set()
_lock = threading.Lock()
_last_progress: dict[int, tuple[float, float]] = {}


def attach_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


async def register(ws: WebSocket) -> None:
    with _lock:
        _clients.add(ws)


async def unregister(ws: WebSocket) -> None:
    with _lock:
        _clients.discard(ws)


def client_count() -> int:
    return len(_clients)


async def _broadcast(text: str) -> None:
    with _lock:
        targets = list(_clients)
    dead = []
    for ws in targets:
        try:
            await ws.send_text(text)
        except Exception:  # noqa: BLE001
            dead.append(ws)
    if dead:
        with _lock:
            for ws in dead:
                _clients.discard(ws)


def emit(kind: str, data: Any) -> None:
    loop = _loop
    if loop is None or loop.is_closed() or not _clients:
        return
    text = json.dumps({"type": kind, "data": data, "ts": time.time()}, ensure_ascii=False, default=str)
    try:
        loop.call_soon_threadsafe(lambda: asyncio.ensure_future(_broadcast(text)))
    except RuntimeError:
        pass


def should_emit_progress(item_id: int, pct: float) -> bool:
    """진행률 push 과다 방지: 1% 이상 변했거나 0.5초가 지났을 때만."""
    now = time.monotonic()
    last = _last_progress.get(item_id)
    if last is None or abs(pct - last[0]) >= 1 or now - last[1] >= 0.5 or pct >= 100:
        _last_progress[item_id] = (pct, now)
        return True
    return False
