"""Anthropic Messages API (선택). 스키마는 시스템 프롬프트 끝에 명시."""
from __future__ import annotations

import json
import os

from ... import config as C
from ... import models as M
from ...timeutil import local_today
from .base import Provider, ProviderError


def cfg() -> dict:
    return C.get_config()["ai"]["anthropic"]


def has_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


class Anthropic(Provider):
    name = "anthropic"

    @property
    def model(self) -> str:
        return cfg().get("model", "claude-sonnet-5-5")

    def available(self):
        if not has_key():
            return False, ".env 에 ANTHROPIC_API_KEY 가 없습니다"
        limit = int(cfg().get("daily_limit", 50))
        used = M.usage_on(local_today()).get("anthropic", {}).get("calls", 0)
        if limit and used >= limit:
            return False, f"자체 일일 상한 {limit}회 도달"
        return True, None

    def generate(self, system, user, images, schema, followups=None) -> str:
        import anthropic

        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"],
                                     timeout=float(cfg().get("timeout_sec", 120)), max_retries=2)
        sys_text = (system + "\n\n## 출력 JSON 스키마\n다음 JSON 스키마를 만족하는 JSON 객체 하나만 출력한다.\n"
                    + json.dumps(schema, ensure_ascii=False))
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b}}
                   for b in images]
        content.append({"type": "text", "text": user})
        messages = [{"role": "user", "content": content}]
        for prev, instr in followups or []:
            messages.append({"role": "assistant", "content": prev})
            messages.append({"role": "user", "content": instr})
        try:
            resp = client.messages.create(model=self.model, max_tokens=1500, system=sys_text, messages=messages)
        except anthropic.APIStatusError as e:
            raise ProviderError(f"Anthropic 오류 {e.status_code}: {str(e)[:300]}",
                                fatal=e.status_code in (400, 401, 403, 404, 429)) from e
        except anthropic.APIError as e:
            raise ProviderError(f"Anthropic 호출 실패: {e}") from e
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
