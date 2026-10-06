"""Windows 서버에서 강제로 끊긴 HTTP/WebSocket 연결 이후에도 응답하는지 확인."""
from __future__ import annotations

import asyncio
import contextlib
import socket
import struct
import sys
import threading
import time
import urllib.request
from unittest.mock import MagicMock, Mock

import pytest
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from src import server_runtime as R


def test_windows_loop_factory_uses_selector():
    with asyncio.Runner(loop_factory=R.windows_server_loop) as runner:
        async def check():
            return isinstance(asyncio.get_running_loop(), asyncio.SelectorEventLoop)
        assert runner.run(check())


def test_windows_runtime_passes_loop_factory_to_runner(monkeypatch):
    import uvicorn
    monkeypatch.setattr(R.sys, "platform", "win32")
    config = Mock()
    server = Mock()
    server.serve.return_value = object()
    config_class, server_class, runner_class = Mock(return_value=config), Mock(return_value=server), MagicMock()
    monkeypatch.setattr(uvicorn, "Config", config_class)
    monkeypatch.setattr(uvicorn, "Server", server_class)
    monkeypatch.setattr(R.asyncio, "Runner", runner_class)
    R.run_server("src.api:app", host="127.0.0.1", port=8010)
    runner_class.assert_called_once_with(loop_factory=R.windows_server_loop)
    runner_class.return_value.__enter__.return_value.run.assert_called_once_with(server.serve.return_value)
    config_class.assert_called_once_with("src.api:app", loop="asyncio", host="127.0.0.1", port=8010)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 연결 재설정 회귀 검사")
def test_windows_server_survives_reset_connections():
    import uvicorn

    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.websocket("/ws")
    async def ws(ws: WebSocket):
        await ws.accept()
        try:
            while True:
                await ws.receive_text()
                await ws.send_text("pong")
        except WebSocketDisconnect:
            pass

    # 실제 설정·DB·워커를 사용하지 않는 테스트 서버. OS가 빈 포트를 선택한다.
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    errors = []
    thread_errors = []
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                         lifespan="off", log_level="error", access_log=False))

    def run():
        try:
            with asyncio.Runner(loop_factory=R.windows_server_loop) as runner:
                runner.get_loop().set_exception_handler(lambda loop, context: errors.append(context))
                runner.run(server.serve(sockets=[listener]))
        except BaseException as error:
            thread_errors.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline and thread.is_alive():
            time.sleep(0.02)
        assert server.started, thread_errors
        for _ in range(12):
            # 요청을 보낸 뒤 RST로 HTTP 연결을 강제로 끊는다.
            with socket.create_connection(("127.0.0.1", port), timeout=3) as conn:
                conn.sendall(b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n")
                conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("HH", 1, 0))
            # WebSocket 핸드셰이크 완료 후에도 RST로 끊는다.
            with socket.create_connection(("127.0.0.1", port), timeout=3) as conn:
                conn.sendall(b"GET /ws HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                             b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n")
                assert b"101 Switching Protocols" in conn.recv(4096)
                conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("HH", 1, 0))
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
                assert response.status == 200
                assert b'"ok":true' in response.read()
        assert not errors, [str(context.get("exception")) for context in errors]
        assert thread.is_alive()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        with contextlib.suppress(OSError):
            listener.close()
    assert not thread.is_alive()
    assert not thread_errors
