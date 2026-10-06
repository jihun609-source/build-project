"""API 키(.env)와 YouTube OAuth 클라이언트 파일 등록.

- 키 값은 응답에 절대 그대로 돌려주지 않고 앞뒤 몇 글자만 보여 준다
- .env 는 임시 파일에 쓴 뒤 교체, 설정 이력(.history)·백업 zip 에는 넣지 않는다
- 저장 즉시 os.environ 에 반영해 재시작 없이 다음 호출부터 쓴다
"""
from __future__ import annotations

import json
import os
import re

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from .. import config as C
from .. import events
from .. import models as M
from .. import storage, youtube

router = APIRouter()

ENV_PATH = C.ROOT / ".env"
KEYS = {
    "gemini": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}
LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def mask(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip()
    return v[:4] + "…" + v[-3:] if len(v) > 10 else "…" * 3


def _write_env(updates: dict[str, str | None]) -> None:
    """기존 줄 순서·주석은 유지하고 해당 키만 바꾼다."""
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    done = set()
    out = []
    for line in lines:
        m = LINE_RE.match(line)
        if m and m.group(1) in updates:
            name = m.group(1)
            out.append(f"{name}={updates[name] or ''}")
            done.add(name)
        else:
            out.append(line)
    for name, value in updates.items():
        if name not in done:
            out.append(f"{name}={value or ''}")
    storage.atomic_write(ENV_PATH, "\n".join(out) + "\n")
    for name, value in updates.items():
        if value:
            os.environ[name] = value
        else:
            os.environ.pop(name, None)


def client_info() -> dict:
    path = youtube.client_secret_path()
    info = {"present": bool(path and path.exists()), "path": str(path), "client_id": None, "type": None,
            "redirect_uri": youtube.redirect_uri(), "redirect_registered": None}
    if info["present"]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            kind = "web" if "web" in data else "installed" if "installed" in data else None
            body = data.get(kind or "", {})
            cid = body.get("client_id") or ""
            info.update(type=kind, client_id=(cid[:12] + "…" + cid[-28:]) if len(cid) > 45 else cid)
            uris = body.get("redirect_uris") or []
            info["redirect_registered"] = youtube.redirect_uri() in uris if kind == "web" else None
        except (OSError, json.JSONDecodeError):
            info["error"] = "client_secret.json 을 읽을 수 없습니다"
    return info


@router.get("/secrets")
def get_secrets():
    from .system import _cache
    _cache.pop("youtube", None)
    return {
        "keys": {p: {"env": env, "set": bool(os.environ.get(env)), "masked": mask(os.environ.get(env))}
                 for p, env in KEYS.items()},
        "youtube_client": client_info(),
        "youtube": youtube.status(),
    }


class KeyIn(BaseModel):
    value: str


@router.put("/secrets/keys/{provider}")
def set_key(provider: str, body: KeyIn):
    env = KEYS.get(provider)
    if not env:
        raise HTTPException(404, "알 수 없는 프로바이더")
    value = body.value.strip()
    if not value or any(ch.isspace() for ch in value) or len(value) < 10:
        raise HTTPException(400, "키 형식이 올바르지 않습니다 (공백 없이 전체 키를 붙여 넣으세요)")
    _write_env({env: value})
    if provider == "gemini":
        from ..ai.providers.gemini import EXHAUSTED_KEY
        M.kv_set(EXHAUSTED_KEY, None)
    M.add_event(None, "api", None, "info", f"{env} 등록/변경")
    events.emit("config", {"kind": "secrets"})
    return get_secrets()


@router.delete("/secrets/keys/{provider}")
def delete_key(provider: str):
    env = KEYS.get(provider)
    if not env:
        raise HTTPException(404, "알 수 없는 프로바이더")
    _write_env({env: None})
    M.add_event(None, "api", None, "info", f"{env} 삭제")
    events.emit("config", {"kind": "secrets"})
    return get_secrets()


@router.post("/secrets/keys/{provider}/test")
def test_key(provider: str):
    """짧은 호출로 키가 동작하는지 확인 (항목·사용량 집계에는 넣지 않음)."""
    schema = {"type": "object", "properties": {"ok": {"type": "string"}}, "required": ["ok"]}
    try:
        if provider == "gemini":
            from ..ai.providers.gemini import Gemini
            if not os.environ.get(KEYS["gemini"]):
                raise HTTPException(409, "키가 없습니다")
            p = Gemini()
        elif provider == "anthropic":
            from ..ai.providers.anthropic import Anthropic
            if not os.environ.get(KEYS["anthropic"]):
                raise HTTPException(409, "키가 없습니다")
            p = Anthropic()
        else:
            raise HTTPException(404, "알 수 없는 프로바이더")
        raw = p.generate("JSON으로만 답한다.", '{"ok": "yes"} 를 그대로 출력하라.', [], schema)
        return {"ok": True, "model": p.model, "response": raw[:200]}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:500]}


def _save_client(data: dict) -> dict:
    kind = "web" if "web" in data else "installed" if "installed" in data else None
    if not kind or not data[kind].get("client_id") or not data[kind].get("client_secret"):
        raise HTTPException(400, "Google Cloud에서 받은 OAuth 클라이언트 JSON이 아닙니다 "
                                 "(web 또는 installed 안에 client_id, client_secret 이 있어야 함)")
    path = youtube.client_secret_path()
    storage.atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))
    M.add_event(None, "api", None, "info", "YouTube OAuth 클라이언트 파일 등록")
    events.emit("config", {"kind": "secrets"})
    return get_secrets()


@router.post("/secrets/youtube-client")
async def upload_client(file: UploadFile = File(...)):
    raw = await file.read()
    if len(raw) > 100_000:
        raise HTTPException(413, "파일이 너무 큽니다")
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(400, "JSON 파일이 아닙니다")
    return _save_client(data)


class ClientText(BaseModel):
    text: str


@router.put("/secrets/youtube-client")
def paste_client(body: ClientText):
    try:
        data = json.loads(body.text)
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"JSON 문법 오류: {e.msg}")
    return _save_client(data)


@router.delete("/secrets/youtube-client")
def delete_client():
    path = youtube.client_secret_path()
    if path and path.exists():
        path.unlink()
    youtube.disconnect()
    events.emit("config", {"kind": "secrets"})
    return get_secrets()
