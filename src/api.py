"""FastAPI 진입점: REST API, /ui 정적 서빙, /ws 이벤트."""
from __future__ import annotations

import asyncio
import mimetypes
from contextlib import asynccontextmanager

# 윈도우 레지스트리에 .js가 text/plain으로 등록된 PC에서는 브라우저가 모듈 스크립트를 거부해
# 흰 화면이 된다. 레지스트리 값과 관계없이 올바른 형식으로 서빙한다.
for _ext, _type in ((".js", "text/javascript"), (".mjs", "text/javascript"), (".css", "text/css"),
                    (".svg", "image/svg+xml"), (".json", "application/json"), (".wasm", "application/wasm")):
    mimetypes.add_type(_type, _ext)

from fastapi import Depends, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import auth
from . import config as C
from . import events
from . import models as M
from .main import bootstrap, workers
from .routes import config_api, feed, items, manual, presets_api, prompts_api, secrets_api, system, upload_bridge
from .storage import StorageError
from .workers.base import log


@asynccontextmanager
async def lifespan(app: FastAPI):
    bootstrap()
    events.attach_loop(asyncio.get_running_loop())
    n = M.recover_interrupted()
    if n:
        log("api", "warning", f"비정상 종료로 진행 중이던 항목 {n}개를 이전 단계로 되돌림")
    autostart = C.get_config()["workers"].get("autostart") or []
    for name, w in workers().items():
        if name in autostart:
            w.start()
    from . import thumbs
    thumbs.start()
    log("api", "info", "서버 시작")
    yield
    for w in workers().values():
        w.stop()
    from .selenium_accounts import LOGIN
    LOGIN.shutdown()


app = FastAPI(title="AutoSet 릴스 파이프라인", lifespan=lifespan)


@app.middleware("http")
async def ui_cache_control(request: Request, call_next):
    response = await call_next(request)
    if request.url.path in ("/ui", "/ui/", "/ui/index.html"):
        response.headers["Cache-Control"] = "no-store"
    return response


# 개발용 Vite 서버(5173)와 확장프로그램에서의 호출 허용. 인증은 토큰으로 한다.
app.add_middleware(CORSMiddleware, allow_origin_regex=r".*", allow_credentials=True, allow_methods=["*"],
                   allow_headers=["*"])


@app.exception_handler(StorageError)
async def storage_error(_: Request, exc: StorageError):
    return JSONResponse(status_code=exc.status, content={"detail": str(exc), "fields": exc.fields})


guard = [Depends(auth.require_token)]
for r in (feed.router, items.router, config_api.router, presets_api.router, prompts_api.router,
          upload_bridge.router, secrets_api.router, manual.router, system.router):
    app.include_router(r, dependencies=guard)
app.include_router(system.public)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    if not auth.ws_token_ok(ws):
        await ws.close(code=4401)
        return
    await ws.accept()
    await events.register(ws)
    try:
        await ws.send_json({"type": "hello", "data": {"workers": [w.status() for w in workers().values()]}})
        while True:
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_text('{"type":"pong"}')
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        pass
    finally:
        await events.unregister(ws)


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse("/ui/")


dist = C.DIRS["web_dist"]
if (dist / "index.html").exists():
    app.mount("/ui", StaticFiles(directory=dist, html=True), name="ui")
else:
    @app.get("/ui", include_in_schema=False)
    @app.get("/ui/", include_in_schema=False)
    async def ui_missing():
        return HTMLResponse("<meta charset='utf-8'><h3>웹 UI가 빌드되지 않았습니다</h3>"
                            "<p><code>build_web.bat</code> 을 실행한 뒤 서버를 다시 시작하세요.</p>")
