"""YouTube OAuth2 (토큰 파일 로컬 저장)과 API 클라이언트."""
from __future__ import annotations

import json
import os
from typing import Any

from . import config as C
from . import models as M

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly"]
TOKEN_PATH = C.DIRS["secrets"] / "youtube_token.json"

# 로컬 http 리다이렉트 허용 (localhost 루프백)
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")


def client_secret_path():
    return C.root_path(C.get_config()["upload"].get("client_secret_path") or "secrets/client_secret.json")


def redirect_uri() -> str:
    port = C.get_config()["server"].get("port", 8000)
    return f"http://localhost:{port}/auth/youtube/callback"


def _flow(state: str | None = None):
    from google_auth_oauthlib.flow import Flow
    path = client_secret_path()
    if not path or not path.exists():
        raise FileNotFoundError(f"OAuth 클라이언트 파일이 없습니다: {path} (README의 OAuth 연결 참고)")
    flow = Flow.from_client_secrets_file(str(path), scopes=SCOPES, state=state)
    flow.redirect_uri = redirect_uri()
    return flow


def auth_url() -> str:
    flow = _flow()
    url, state = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true")
    M.kv_set("oauth_state", state)
    # PKCE code_verifier 가 생성되는 버전을 위해 보관
    M.kv_set("oauth_verifier", getattr(flow, "code_verifier", None))
    return url


def finish(full_url: str) -> dict[str, Any]:
    flow = _flow(M.kv_get("oauth_state"))
    verifier = M.kv_get("oauth_verifier")
    if verifier:
        flow.code_verifier = verifier
    flow.fetch_token(authorization_response=full_url)
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(flow.credentials.to_json(), encoding="utf-8")
    info = channel_info(refresh=True)
    return info


def credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    if not TOKEN_PATH.exists():
        return None
    creds = Credentials.from_authorized_user_info(json.loads(TOKEN_PATH.read_text(encoding="utf-8")), SCOPES)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return creds


def service():
    from googleapiclient.discovery import build
    creds = credentials()
    if creds is None:
        raise RuntimeError("YouTube 계정이 연결되지 않았습니다 (업로드 설정 → 연결하기)")
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def channel_info(refresh: bool = False) -> dict[str, Any]:
    cached = M.kv_get("yt_channel")
    if cached and not refresh:
        return cached
    resp = service().channels().list(part="snippet", mine=True).execute()
    items = resp.get("items") or []
    info = {"id": items[0]["id"], "title": items[0]["snippet"]["title"]} if items else {"id": None, "title": None}
    M.kv_set("yt_channel", info)
    return info


def status() -> dict[str, Any]:
    secret = client_secret_path()
    out = {"client_secret": bool(secret and secret.exists()), "connected": TOKEN_PATH.exists(),
           "channel": None, "error": None, "redirect_uri": redirect_uri()}
    if out["connected"]:
        try:
            out["channel"] = channel_info()
        except Exception as e:  # noqa: BLE001
            out["error"] = str(e)
    return out


def disconnect() -> None:
    TOKEN_PATH.unlink(missing_ok=True)
    M.kv_set("yt_channel", None)
