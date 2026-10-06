"""간단한 토큰 인증: X-Token 헤더, ?token= 쿼리, autoset_token 쿠키 중 하나."""
from __future__ import annotations

import hmac

from fastapi import HTTPException, Request, WebSocket

from . import config as C

COOKIE = "autoset_token"


def token_ok(value: str | None) -> bool:
    expected = C.get_config()["ui"].get("token") or ""
    return bool(value) and bool(expected) and hmac.compare_digest(str(value), expected)


def _extract(headers, query, cookies) -> str | None:
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return headers.get("x-token") or query.get("token") or cookies.get(COOKIE)


async def require_token(request: Request) -> None:
    if not token_ok(_extract(request.headers, request.query_params, request.cookies)):
        raise HTTPException(status_code=401, detail="토큰이 필요합니다")


def ws_token_ok(ws: WebSocket) -> bool:
    return token_ok(_extract(ws.headers, ws.query_params, ws.cookies))
