"""AI 프롬프트 파일 조회·저장·버전·테스트 실행."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from .. import config as C
from .. import events
from .. import models as M
from .. import storage
from ..ai.prompt_loader import VARIABLES

router = APIRouter()


@router.get("/prompts")
def list_profiles():
    return {"profiles": storage.list_profiles(), "default": C.get_config()["ai"].get("prompt_profile"),
            "files": storage.PROMPT_FILES, "variables": VARIABLES}


@router.get("/prompts/{profile}")
def get_profile(profile: str):
    files = storage.read_profile(profile)
    return {"profile": profile, "files": files, "version": storage.profile_version(files)}


class FileIn(BaseModel):
    content: str


@router.put("/prompts/{profile}/{file}")
def save_file(profile: str, file: str, body: FileIn):
    version = storage.save_prompt_file(profile, file, body.content)
    events.emit("config", {"kind": "prompt", "profile": profile, "file": file})
    files = storage.read_profile(profile)
    return {"backup": version, "version": storage.profile_version(files)}


class NameIn(BaseModel):
    name: str


@router.post("/prompts/{profile}/clone")
def clone(profile: str, body: NameIn):
    storage.clone_profile(profile, body.name)
    events.emit("config", {"kind": "prompt", "profile": body.name})
    return {"profile": body.name}


@router.post("/prompts/{profile}/default")
def set_default(profile: str):
    storage.read_profile(profile)
    storage.patch_config({"ai": {"prompt_profile": profile}})
    return {"default": profile}


@router.get("/prompts/{profile}/history")
def history(profile: str):
    return {"versions": storage.prompt_history(profile).list()}


@router.get("/prompts/{profile}/history/{version}")
def history_version(profile: str, version: str):
    fname = version.split("@", 1)[0]
    files = storage.read_profile(profile)
    return {"version": version, "file": fname, "text": storage.prompt_history(profile).read(version),
            "current": files.get(fname, "")}


@router.post("/prompts/{profile}/restore/{version}")
def restore(profile: str, version: str):
    storage.restore_prompt(profile, version)
    events.emit("config", {"kind": "prompt", "profile": profile, "restored": version})
    return get_profile(profile)


class TestIn(BaseModel):
    item_id: int
    profile: str | None = None
    overrides: dict[str, str] | None = None
    instruction: str | None = None


def _test(body: TestIn) -> dict[str, Any]:
    from ..workers import captioner as cap
    item = M.get_item(body.item_id)
    if not item:
        raise HTTPException(404, "항목이 없습니다")
    if not item.get("edited_path") or not item.get("edit_params"):
        raise HTTPException(409, "편집이 끝난 항목만 테스트할 수 있습니다")
    for name, text in (body.overrides or {}).items():
        errs = storage.validate_prompt_file(name, text)
        if errs:
            raise storage.StorageError("프롬프트 오류", errs)
    ex = cap.ensure_extract(item)
    speech = cap.ensure_stt(item, ex["audio"])
    res = cap.run_ai(item, ex["frames"], speech, profile=body.profile, instruction=body.instruction,
                     overrides=body.overrides)
    res["frames"] = [f"/files/frames/{item['id']}/{f['file']}" for f in ex["frames"]]
    res["has_speech"] = speech["has_speech"]
    return res


@router.post("/prompts/test")
async def test(body: TestIn):
    """항목에 저장하지 않고 AI 생성만 실행."""
    return await run_in_threadpool(_test, body)


class ApplyIn(BaseModel):
    item_id: int
    result: dict[str, Any]
    provider: str | None = None
    profile: str | None = None
    version: str | None = None


@router.post("/prompts/test/apply")
def apply(body: ApplyIn):
    """테스트 결과를 항목에 적용하고 자막 합성만 다시."""
    import json
    item = M.get_item(body.item_id)
    if not item:
        raise HTTPException(404, "항목이 없습니다")
    if item["status"] in ("downloading", "editing", "captioning", "uploading", "uploaded"):
        raise HTTPException(409, f"지금은 적용할 수 없습니다 ({item['status']})")
    r = body.result
    M.update_item(body.item_id, caption=r.get("caption"), summary=r.get("summary"), title=r.get("title"),
                  description=r.get("description"), tags=r.get("tags") or [], ai_confidence=r.get("confidence"),
                  ai_provider_used=body.provider, prompt_profile=body.profile, prompt_version=body.version,
                  needs_review=0, status="edited", error=None)
    d = C.DIRS["work"] / str(body.item_id)
    d.mkdir(parents=True, exist_ok=True)
    payload = {"ok": True, "result": r, "provider": body.provider, "applied_from_test": True,
               "prompt": {"profile": body.profile, "version": body.version}}
    (d / "ai_result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (d / "ai_response.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    M.add_event(body.item_id, "api", "caption", "info", "프롬프트 테스트 결과 적용 - 자막 합성 다시 실행")
    return M.item_public(M.get_item(body.item_id))
