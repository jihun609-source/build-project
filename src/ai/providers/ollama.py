"""로컬 Ollama (기본 gemma4:e4b)."""
from __future__ import annotations

import json
import time
from typing import Any, Callable

import httpx

from ... import config as C
from .base import Provider, ProviderError


def cfg() -> dict:
    return C.get_config()["ai"]["ollama"]


def host() -> str:
    return (cfg().get("host") or "http://localhost:11434").rstrip("/")


def list_models(timeout: float = 5) -> list[dict[str, Any]]:
    r = httpx.get(f"{host()}/api/tags", timeout=timeout)
    r.raise_for_status()
    return r.json().get("models", [])


def status() -> dict[str, Any]:
    model = cfg().get("model")
    try:
        models = list_models()
    except Exception as e:  # noqa: BLE001
        return {"connected": False, "error": str(e), "model": model, "installed": False, "models": []}
    names = [m.get("name") or m.get("model") for m in models]
    installed = model in names or (":" not in model and f"{model}:latest" in names)
    return {"connected": True, "model": model, "installed": installed, "models": names}


def pull(model: str, on_progress: Callable[[dict], None]) -> None:
    with httpx.stream("POST", f"{host()}/api/pull", json={"model": model, "stream": True},
                      timeout=httpx.Timeout(30, read=600)) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("error"):
                raise RuntimeError(d["error"])
            pct = None
            if d.get("total"):
                pct = round((d.get("completed") or 0) / d["total"] * 100, 1)
            on_progress({"model": model, "status": d.get("status"), "pct": pct})


class Ollama(Provider):
    name = "ollama"

    @property
    def model(self) -> str:
        return cfg().get("model", "gemma4:e4b")

    def available(self):
        return True, None

    def generate(self, system, user, images, schema, followups=None) -> str:
        c = cfg()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user, "images": images},
        ]
        for prev, instr in followups or []:
            messages.append({"role": "assistant", "content": prev})
            messages.append({"role": "user", "content": instr})
        body = {
            "model": self.model, "messages": messages, "format": schema, "think": False, "stream": False,
            "keep_alive": "10m",
            "options": {"temperature": float(c.get("temperature", 0.4)), "num_ctx": int(c.get("num_ctx", 8192))},
        }
        timeout = float(c.get("timeout_sec", 300))
        last: Exception | None = None
        for attempt in range(3):
            try:
                r = httpx.post(f"{host()}/api/chat", json=body, timeout=httpx.Timeout(30, read=timeout))
                if r.status_code == 400 and "think" in r.text.lower() and "think" in body:
                    body.pop("think")
                    continue
                if r.status_code == 404:
                    raise ProviderError(f"Ollama 모델 '{self.model}' 이 설치되지 않았습니다 (AI 화면에서 '모델 받기')",
                                        fatal=True)
                if r.status_code >= 400:
                    raise ProviderError(f"Ollama HTTP {r.status_code}: {r.text[:300]}")
                return (r.json().get("message") or {}).get("content", "")
            except ProviderError as e:
                if e.fatal:
                    raise
                last = e
            except httpx.ConnectError as e:
                raise ProviderError(f"Ollama 연결 실패 ({host()}): {e}", fatal=True) from e
            except httpx.HTTPError as e:
                last = e
            time.sleep(3 * (attempt + 1))
        raise ProviderError(f"Ollama 호출 실패: {last}")
