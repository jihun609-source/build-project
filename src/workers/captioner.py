"""자막 워커: 추출(0~15) → 음성 인식(15~45) → AI 생성(45~75) → 합성(75~100).

하위 단계 결과는 work/{id}/ 에 캐시해 재시도 시 끝난 단계는 건너뛴다.
  audio.wav, audio.json   추출
  stt_raw.json            원본 기준 인식 결과 (재편집해도 재사용)
  frames/, frames.json    편집본 기준 프레임 (재편집 시 삭제)
  ai_result.json          AI 결과 (다시 생성 시 삭제, 캡션 직접 수정 시 유지)
GPU 충돌 방지를 위해 한 번에 한 건만 처리 (GPU_LOCK은 프롬프트 테스트와도 공유).
"""
from __future__ import annotations

import base64
import json
import threading
import time
from pathlib import Path
from typing import Any

from .. import config as C
from .. import models as M
from ..ai import extract, prompt_loader, render, stt
from ..ai.generate import GenerationFailed, generate
from ..util import proc
from .base import Worker

GPU_LOCK = threading.Lock()


def workdir(item_id: int) -> Path:
    d = C.DIRS["work"] / str(item_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _write(p: Path, data: Any) -> None:
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def invalidate(item_id: int, *, frames: bool = False, ai: bool = False, stt_cache: bool = False) -> None:
    d = C.DIRS["work"] / str(item_id)
    if not d.exists():
        return
    if frames:
        for p in (d / "frames").glob("*"):
            p.unlink(missing_ok=True)
    if ai:
        (d / "ai_result.json").unlink(missing_ok=True)
    if stt_cache:
        (d / "stt_raw.json").unlink(missing_ok=True)
        (d / "audio.json").unlink(missing_ok=True)


def ensure_extract(item: dict, log=None) -> dict:
    d = workdir(item["id"])
    audio = _read(d / "audio.json")
    if audio is None:
        src = Path(item["source_path"])
        audio = extract.extract_audio(src, d / "audio.wav", bool((item.get("source_meta") or {}).get("has_audio", True)))
        _write(d / "audio.json", audio)
    frames = _read(d / "frames" / "frames.json")
    if not frames or not all((d / "frames" / f["file"]).exists() for f in frames):
        frames = extract.extract_frames(Path(item["edited_path"]), d / "frames", item["edit_params"],
                                        int(C.get_config()["ai"].get("frame_count", 5)))
    return {"audio": audio, "frames": frames}


def ensure_stt(item: dict, audio: dict, log=None) -> dict:
    d = workdir(item["id"])
    raw = _read(d / "stt_raw.json")
    if raw is None:
        if audio.get("silent") or not audio.get("has_audio"):
            raw = {"text": "", "segments": [], "has_speech": False,
                   "reason": "오디오 없음" if not audio.get("has_audio") else f"무음 (평균 {audio.get('mean_volume')} dB)"}
        else:
            try:
                with GPU_LOCK:
                    raw = stt.transcribe(d / "audio.wav")
            except Exception as e:  # noqa: BLE001
                if log:
                    log("warning", f"음성 인식 실패, 음성 없이 진행: {e}")
                return {"text": "", "segments": [], "has_speech": False, "reason": f"인식 실패: {e}",
                        "segments_edited": [], "text_edited": ""}
        _write(d / "stt_raw.json", raw)
    segs = stt.remap_segments(raw.get("segments") or [], item["edit_params"])
    text = " ".join(s["text"] for s in segs).strip()
    has_speech = bool(raw.get("has_speech")) and len(text.replace(" ", "")) >= 5
    return {**raw, "segments_edited": segs, "text_edited": text, "has_speech": has_speech}


def build_prompt(item: dict, frames: list[dict], speech: dict, *, profile: str | None = None,
                 instruction: str | None = None, overrides: dict | None = None):
    feed = M.get_feed(item["feed_item_id"]) if item.get("feed_item_id") else {}
    variables = prompt_loader.build_vars(
        duration_sec=item.get("edited_duration") or item["edit_params"].get("edited_duration"),
        frame_count=len(frames), has_speech=speech["has_speech"], transcript=speech["text_edited"],
        original_caption=(feed or {}).get("caption"), author=(feed or {}).get("author"),
        extra_instruction=instruction)
    return prompt_loader.load(profile, variables, overrides)


def frame_images(item_id: int, frames: list[dict]) -> list[str]:
    d = workdir(item_id) / "frames"
    return [base64.b64encode((d / f["file"]).read_bytes()).decode() for f in frames if (d / f["file"]).exists()]


def run_ai(item: dict, frames: list[dict], speech: dict, *, profile: str | None = None,
           instruction: str | None = None, overrides: dict | None = None, log=None) -> dict:
    """AI 생성 1회. 반환: ai_result (result, provider, prompt 정보, 호출 로그)."""
    prompt = build_prompt(item, frames, speech, profile=profile, instruction=instruction, overrides=overrides)
    if prompt.unknown_vars and log:
        log("warning", f"알 수 없는 프롬프트 변수: {', '.join(prompt.unknown_vars)}")
    images = frame_images(item["id"], frames)
    t0 = time.monotonic()
    try:
        with GPU_LOCK:
            data, provider, calls = generate(prompt.system, prompt.user, images, prompt.schema, on_event=log)
    except GenerationFailed as e:
        return {"ok": False, "error": str(e), "calls": e.log, "elapsed_ms": int((time.monotonic() - t0) * 1000),
                "prompt": {"profile": prompt.profile, "version": prompt.version, "system": prompt.system,
                           "user": prompt.user}}
    return {"ok": True, "result": data, "provider": provider, "calls": calls,
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
            "prompt": {"profile": prompt.profile, "version": prompt.version, "system": prompt.system,
                       "user": prompt.user, "images": len(images)}}


class Captioner(Worker):
    name = "captioner"
    stage = "caption"
    fail_status = "failed_caption"

    def tick(self) -> bool:
        item = M.claim_item("edited", "captioning", "caption")
        if not item:
            return False
        self.process_claimed(item, "edited")
        return True

    def handle(self, item: dict[str, Any]) -> None:
        item_id = item["id"]
        d = workdir(item_id)
        if not item.get("edited_path") or not Path(item["edited_path"]).exists():
            raise RuntimeError("편집본이 없습니다 (편집 다시 실행 필요)")
        params = item["edit_params"]

        def log(level, msg):
            self.log(level, msg, item_id)

        # 1) 추출
        self.progress(item_id, 1, "추출")
        ex = ensure_extract(item, log)
        self.progress(item_id, 15, "인식")
        if self.stopping():
            raise proc.Cancelled()
        # 2) 음성 인식
        speech = ensure_stt(item, ex["audio"], log)
        if not speech["has_speech"] and speech.get("reason"):
            log("info", f"음성 없음 처리: {speech['reason']}")
        M.update_item(item_id, _emit=False, transcript=speech["text_edited"] or None, has_speech=speech["has_speech"])
        self.progress(item_id, 45, "생성")
        if self.stopping():
            raise proc.Cancelled()

        # 3) AI 생성 (캐시가 있으면 DB 값을 그대로 사용: 사용자가 직접 수정한 캡션 보존)
        cached = _read(d / "ai_result.json")
        if cached and cached.get("ok"):
            item = M.get_item(item_id)
            log("info", "AI 결과 캐시 사용 (합성만 다시 실행)")
        else:
            res = run_ai(item, ex["frames"], speech, profile=item.get("prompt_profile_override"),
                         instruction=item.get("extra_instruction"), log=log)
            _write(d / "ai_response.json", res)
            if not res["ok"]:
                raise RuntimeError(res["error"])
            r = res["result"]
            # 자막 탭에서 직접 올린 영상은 사용자가 이미 고른 영상이므로 검토 없이 바로 업로드 대기열로
            hold = (r.get("confidence") == "low" and item.get("source_kind") != "manual"
                    and bool(C.get_config()["ai"].get("hold_low_confidence", True)))
            item = M.update_item(
                item_id, caption=r.get("caption"), summary=r.get("summary"), title=r.get("title"),
                description=r.get("description"), tags=r.get("tags") or [], ai_confidence=r.get("confidence"),
                ai_provider_used=res["provider"], prompt_profile=res["prompt"]["profile"],
                prompt_version=res["prompt"]["version"], needs_review=hold)
            _write(d / "ai_result.json", res)
            log("info", f"AI 생성 완료 ({res['provider']}, {res['elapsed_ms'] / 1000:.1f}초, "
                        f"confidence={r.get('confidence')})")
        self.progress(item_id, 75, "합성")

        # 4) 합성
        segs = speech["segments_edited"]
        ass, info = render.build_ass(params, item.get("caption"), segs, speech["has_speech"])
        _write(d / "render.json", info)
        out = C.DIRS["ready"] / f"{item_id}_final.mp4"
        total = float(item.get("edited_duration") or params.get("edited_duration") or 1)
        render.burn(Path(item["edited_path"]), out, ass, d, params["caption"]["font_path"], total,
                    lambda p: self.progress(item_id, 75 + p * 0.25), cancel=self.stopping)
        status = "captioned_review" if item.get("needs_review") else "captioned"
        M.update_item(item_id, status=status, final_path=str(out), progress_pct=100, stage_detail=None)
        if status == "captioned_review":
            log("warning", "confidence=low - 검토 대기 (승인 전까지 업로드하지 않음)")


if __name__ == "__main__":
    from ..main import bootstrap
    bootstrap()
    Captioner().run_forever()
