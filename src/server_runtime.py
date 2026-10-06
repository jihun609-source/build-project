"""Windows 서버의 네트워크 연결 정리 및 실행 루프."""
from __future__ import annotations

import asyncio
import sys


def windows_server_loop() -> asyncio.AbstractEventLoop:
    # HTTP/WebSocket은 Selector로 처리한다. ffmpeg·Chrome은 별도 스레드의
    # subprocess/Popen으로 실행하므로 asyncio의 비동기 subprocess가 필요 없다.
    # Uvicorn의 최신 버전은 정책 설정을 무시하므로 Runner에 직접 전달한다.
    return asyncio.SelectorEventLoop()


def run_server(app: str, **options) -> None:
    import uvicorn

    if sys.platform == "win32":
        server = uvicorn.Server(uvicorn.Config(app, loop="asyncio", **options))
        with asyncio.Runner(loop_factory=windows_server_loop) as runner:
            runner.run(server.serve())
    else:
        uvicorn.run(app, **options)
