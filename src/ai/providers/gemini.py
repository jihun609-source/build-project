"""Gemini API 무료 등급 (google-genai SDK)."""
from __future__ import annotations

import base64
import os
import time

from ... import config as C
from ... import models as M
from ...timeutil import iso, local_today, next_pacific_midnight, now, parse
from .base import Provider, ProviderError

EXHAUSTED_KEY = "gemini_exhausted_until"


def cfg() -> dict:
    return C.get_config()["ai"]["gemini"]


def has_key() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY"))


def _to_gemini_schema(schema):
    """JSON 스키마 → Gemini response_schema(OpenAPI 부분집합). 지원 안 되는 키는 버림."""
    if isinstance(schema, dict):
        keep = {"type", "properties", "required", "items", "enum", "description", "format",
                "minItems", "maxItems", "nullable", "maxLength", "minLength"}
        out = {}
        for k, v in schema.items():
            if k not in keep:
                continue
            if k == "properties":
                out[k] = {pk: _to_gemini_schema(pv) for pk, pv in v.items()}
            elif k == "items":
                out[k] = _to_gemini_schema(v)
            else:
                out[k] = v
        return out
    return schema


class Gemini(Provider):
    name = "gemini"

    @property
    def model(self) -> str:
        return cfg().get("model", "gemini-2.5-flash")

    def available(self):
        if not has_key():
            return False, ".env 에 GEMINI_API_KEY 가 없습니다"
        until = parse(M.kv_get(EXHAUSTED_KEY))
        if until and until > now():
            return False, f"일일 한도 소진 (리셋 {iso(until)})"
        limit = int(cfg().get("daily_limit", 200))
        used = M.usage_on(local_today()).get("gemini", {}).get("calls", 0)
        if limit and used >= limit:
            return False, f"자체 일일 상한 {limit}회 도달"
        return True, None

    def generate(self, system, user, images, schema, followups=None) -> str:
        from google import genai
        from google.genai import errors, types

        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"],
                              http_options=types.HttpOptions(timeout=int(cfg().get("timeout_sec", 120)) * 1000))
        parts = [types.Part.from_text(text=user)]
        for b64 in images:
            parts.append(types.Part.from_bytes(data=base64.b64decode(b64), mime_type="image/jpeg"))
        contents = [types.Content(role="user", parts=parts)]
        for prev, instr in followups or []:
            contents.append(types.Content(role="model", parts=[types.Part.from_text(text=prev)]))
            contents.append(types.Content(role="user", parts=[types.Part.from_text(text=instr)]))
        conf = dict(system_instruction=system, response_mime_type="application/json",
                    temperature=float(C.get_config()["ai"]["ollama"].get("temperature", 0.4)))
        try:
            gen_conf = types.GenerateContentConfig(**conf, response_json_schema=schema)
        except Exception:  # noqa: BLE001  구버전 SDK
            gen_conf = types.GenerateContentConfig(**conf, response_schema=_to_gemini_schema(schema))

        minute_retry_used = False
        net_attempts = 0
        while True:
            try:
                resp = client.models.generate_content(model=self.model, contents=contents, config=gen_conf)
                return resp.text or ""
            except errors.APIError as e:
                code = getattr(e, "code", None)
                msg = str(e)
                if code == 429:
                    if "PerDay" in msg or "per day" in msg.lower() or "daily" in msg.lower():
                        M.kv_set(EXHAUSTED_KEY, iso(next_pacific_midnight()))
                        raise ProviderError("Gemini 일일 한도 소진 - 오늘은 건너뜁니다", fatal=True) from e
                    if not minute_retry_used:
                        minute_retry_used = True
                        time.sleep(60)
                        continue
                    raise ProviderError("Gemini 분당 한도 초과 (60초 대기 후에도 실패)", fatal=True) from e
                if code == 402:
                    # 선불 크레딧 소진: 충전 전까지 계속 실패하므로 오늘은 건너뛴다
                    M.kv_set(EXHAUSTED_KEY, iso(next_pacific_midnight()))
                    raise ProviderError("Gemini 선불 크레딧 소진 (AI Studio에서 결제/크레딧 확인) - 오늘은 건너뜁니다",
                                        fatal=True) from e
                if code in (500, 502, 503, 504) and net_attempts < 2:
                    net_attempts += 1
                    time.sleep(5 * net_attempts)
                    continue
                raise ProviderError(f"Gemini 오류 {code}: {msg[:300]}", fatal=code in (400, 401, 403, 404)) from e
            except Exception as e:  # noqa: BLE001  네트워크
                if net_attempts < 2:
                    net_attempts += 1
                    time.sleep(5 * net_attempts)
                    continue
                raise ProviderError(f"Gemini 호출 실패: {e}") from e
