"""prompts/{profile}/ 로드와 {{변수}} 치환. 매 호출마다 파일을 새로 읽는다."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .. import config as C
from .. import storage
from ..timeutil import local_today

VAR_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)

VARIABLES = {
    "duration_sec": "편집본 길이(초, 소수 1자리)",
    "frame_count": "첨부 프레임 수",
    "has_speech": "음성 \"있음\" / \"없음\"",
    "transcript": "전사 텍스트 (없으면 \"(음성 없음)\", 2,000자 초과 시 중간 생략)",
    "original_caption": "원본 캡션 (없으면 \"(없음)\")",
    "author": "원본 작성자 핸들",
    "tone": "config.ai.style.tone",
    "banned_words": "금지어 쉼표 연결 (없으면 \"(없음)\")",
    "extra_instruction": "재생성 시 추가 지시",
    "today": "서버 로컬 날짜",
}


@dataclass
class Prompt:
    profile: str
    version: str
    system: str
    user: str
    schema: dict[str, Any]
    unknown_vars: list[str]


def shorten_transcript(text: str, limit: int = 2000) -> str:
    if len(text) <= limit:
        return text
    half = (limit - 20) // 2
    return text[:half].rstrip() + "\n…(중략)…\n" + text[-half:].lstrip()


def build_vars(*, duration_sec: float | None = None, frame_count: int = 0, has_speech: bool = False,
               transcript: str = "", original_caption: str | None = None, author: str | None = None,
               extra_instruction: str | None = None) -> dict[str, str]:
    style = C.get_config()["ai"].get("style", {})
    banned = [w for w in (style.get("banned_words") or []) if str(w).strip()]
    return {
        "duration_sec": f"{duration_sec:.1f}" if duration_sec is not None else "",
        "frame_count": str(frame_count),
        "has_speech": "있음" if has_speech else "없음",
        "transcript": shorten_transcript(transcript) if (has_speech and transcript) else "(음성 없음)",
        "original_caption": (original_caption or "").strip() or "(없음)",
        "author": author or "",
        "tone": style.get("tone") or "",
        "banned_words": ", ".join(banned) if banned else "(없음)",
        "extra_instruction": (extra_instruction or "").strip(),
        "today": local_today(),
    }


def render(template: str, variables: dict[str, str], unknown: list[str]) -> str:
    def sub(m: re.Match) -> str:
        name = m.group(1)
        if name in variables:
            return variables[name]
        if name not in unknown:
            unknown.append(name)
        return m.group(0)

    return VAR_RE.sub(sub, template)


def load(profile: str | None, variables: dict[str, str], overrides: dict[str, str] | None = None) -> Prompt:
    profile = profile or C.get_config()["ai"].get("prompt_profile") or "caption"
    files = storage.read_profile(profile)
    if overrides:
        files.update({k: v for k, v in overrides.items() if k in storage.PROMPT_FILES})
    unknown: list[str] = []
    system = render(files["system.md"], variables, unknown).rstrip()
    examples = COMMENT_RE.sub("", files.get("examples.md", "")).strip()
    if examples:
        system += "\n\n## 예시\n" + render(examples, variables, unknown)
    user = render(files["user.md"], variables, unknown).strip()
    try:
        schema = json.loads(files["schema.json"])
    except json.JSONDecodeError as e:
        raise ValueError(f"prompts/{profile}/schema.json 문법 오류: {e}") from e
    return Prompt(profile=profile, version=storage.profile_version(files), system=system, user=user,
                  schema=schema, unknown_vars=unknown)
