"""프로바이더 체인 실행, JSON 검증·재요청·폴백·후처리."""
from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

import jsonschema

from .. import config as C
from .. import models as M
from .providers.anthropic import Anthropic
from .providers.base import Provider, ProviderError
from .providers.gemini import Gemini
from .providers.ollama import Ollama

PROVIDERS: dict[str, type[Provider]] = {"ollama": Ollama, "gemini": Gemini, "anthropic": Anthropic}

REASK_JSON = "직전 출력을 스키마에 맞는 JSON으로만 다시 출력하라. 설명·마크다운·코드블록 없이 JSON 객체 하나만 쓴다."
FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)


class GenerationFailed(Exception):
    def __init__(self, message: str, log: list[dict]):
        super().__init__(message)
        self.log = log


def parse_json(text: str) -> dict[str, Any]:
    t = FENCE_RE.sub("", (text or "").strip()).strip()
    try:
        data = json.loads(t)
    except json.JSONDecodeError:
        a, b = t.find("{"), t.rfind("}")
        if a < 0 or b <= a:
            raise
        data = json.loads(t[a:b + 1])
    if not isinstance(data, dict):
        raise ValueError("JSON 객체가 아닙니다")
    return data


def classify_errors(data: dict, schema: dict) -> tuple[list[str], list[str]]:
    """(길이 초과 오류, 그 밖의 스키마 오류)."""
    length, other = [], []
    for e in jsonschema.Draft7Validator(schema).iter_errors(data):
        path = ".".join(str(p) for p in e.absolute_path) or "(root)"
        msg = f"{path}: {e.message}"
        if e.validator in ("maxLength", "maxItems"):
            length.append(msg)
        else:
            other.append(msg)
    return length, other


def truncate_to_schema(data: dict, schema: dict) -> dict:
    out = dict(data)
    for key, prop in (schema.get("properties") or {}).items():
        v = out.get(key)
        if isinstance(v, str) and "maxLength" in prop and len(v) > prop["maxLength"]:
            out[key] = v[: max(1, prop["maxLength"] - 1)].rstrip() + "…"
        elif isinstance(v, list):
            if "maxItems" in prop:
                v = v[: prop["maxItems"]]
            item = prop.get("items") or {}
            if "maxLength" in item:
                v = [s[: item["maxLength"]] if isinstance(s, str) else s for s in v]
            out[key] = v
    return out


def find_banned(data: dict, banned: list[str]) -> list[str]:
    if not banned:
        return []
    blob = json.dumps(data, ensure_ascii=False)
    return [w for w in banned if w and w in blob]


def postprocess(data: dict) -> dict:
    cfg = C.get_config()["ai"]
    tags = data.get("tags") or []
    if isinstance(tags, str):
        tags = [t for t in re.split(r"[,\s]+", tags) if t]
    merged: list[str] = []
    for t in [*(str(x) for x in tags), *(str(x) for x in (cfg.get("fixed_tags") or []))]:
        t = t.strip().lstrip("#").strip()
        if t and t not in merged:
            merged.append(t)
    data["tags"] = merged
    if isinstance(data.get("caption"), str):
        data["caption"] = data["caption"].replace("\\n", "\n").strip()
    if data.get("confidence") not in ("high", "medium", "low"):
        data["confidence"] = "low"
    return data


def run_provider(p: Provider, system: str, user: str, images: list[str], schema: dict,
                 banned: list[str], log: list[dict]) -> dict:
    followups: list[tuple[str, str]] = []
    used = {"json": False, "length": False, "schema": False, "banned": False}
    while True:
        t0 = time.monotonic()
        entry: dict[str, Any] = {"provider": p.name, "model": p.model, "followups": [f[1] for f in followups]}
        log.append(entry)
        M.usage_incr(p.name, calls=1)
        try:
            raw = p.generate(system, user, images, schema, followups)
        except ProviderError:
            M.usage_incr(p.name, failures=1)
            entry["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
            raise
        entry["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
        entry["raw"] = raw
        try:
            data = parse_json(raw)
        except (ValueError, json.JSONDecodeError) as e:
            entry["error"] = f"JSON 파싱 실패: {e}"
            if used["json"]:
                break
            used["json"] = True
            followups.append((raw, REASK_JSON))
            continue
        length, other = classify_errors(data, schema)
        if other:
            entry["error"] = "스키마 불일치: " + "; ".join(other[:5])
            if used["schema"]:
                break
            used["schema"] = True
            followups.append((raw, REASK_JSON + "\n다음 오류를 고쳐라: " + "; ".join(other[:5])))
            continue
        if length:
            entry["error"] = "길이 초과: " + "; ".join(length[:5])
            if not used["length"]:
                used["length"] = True
                followups.append((raw, "다음 필드가 길이 제한을 넘었다. 제한 안으로 줄여 같은 JSON 형식으로만 다시 출력하라: "
                                  + "; ".join(length[:5])))
                continue
            data = truncate_to_schema(data, schema)
            entry["truncated"] = True
        hits = find_banned(data, banned)
        if hits:
            entry["error"] = "금지어 포함: " + ", ".join(hits)
            if used["banned"]:
                break
            used["banned"] = True
            followups.append((raw, f"다음 금지어를 쓰지 말고 같은 JSON 형식으로만 다시 출력하라: {', '.join(hits)}"))
            continue
        entry.pop("error", None)
        return postprocess(data)
    M.usage_incr(p.name, failures=1)
    raise ProviderError(log[-1].get("error") or "검증 실패")


def generate(system: str, user: str, images: list[str], schema: dict,
             providers: list[str] | None = None,
             on_event: Callable[[str, str], None] | None = None) -> tuple[dict, str, list[dict]]:
    """반환: (결과 dict, 사용한 프로바이더, 호출 로그)."""
    cfg = C.get_config()["ai"]
    order = providers or cfg.get("providers") or ["ollama", "gemini"]
    banned = [w for w in (cfg.get("style", {}).get("banned_words") or []) if str(w).strip()]
    log: list[dict] = []
    errors: list[str] = []
    for i, name in enumerate(order):
        cls = PROVIDERS.get(name)
        if not cls:
            continue
        p = cls()
        ok, why = p.available()
        if not ok:
            errors.append(f"{name}: {why}")
            log.append({"provider": name, "skipped": why})
            continue
        try:
            data = run_provider(p, system, user, images, schema, banned, log)
            if i > 0 or errors:
                M.usage_incr(name, fallbacks=1)
            return data, name, log
        except ProviderError as e:
            errors.append(f"{name}: {e}")
            if on_event:
                on_event("warning", f"{name} 실패, 다음 프로바이더로: {e}")
    raise GenerationFailed("모든 AI 프로바이더 실패 - " + " / ".join(errors), log)
