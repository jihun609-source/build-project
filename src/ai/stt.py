"""faster-whisper 음성 인식 + 원본 → 편집본 시각 변환."""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Any

from .. import config as C

_model = None
_model_key: tuple | None = None
_lock = threading.Lock()
device_used: str | None = None


def _add_cuda_dll_dirs() -> None:
    """pip로 설치한 nvidia-cublas/cudnn DLL 경로를 윈도우 DLL 검색 경로에 추가."""
    if os.name != "nt":
        return
    # 개발 환경(venv)과 설치본(PyInstaller: _internal/nvidia) 두 위치를 모두 본다
    bases = [Path(sys.prefix) / "Lib" / "site-packages" / "nvidia", C.APP_ROOT / "nvidia"]
    subs = [sub for base in bases if base.exists() for sub in base.iterdir()]
    for sub in subs:
        b = sub / "bin"
        if b.is_dir():
            try:
                os.add_dll_directory(str(b))
            except OSError:
                pass
            os.environ["PATH"] = str(b) + os.pathsep + os.environ.get("PATH", "")


def get_model():
    global _model, _model_key, device_used
    cfg = C.get_config()["ai"]["stt"]
    size = cfg.get("model_size", "small")
    want = cfg.get("device", "auto")
    key = (size, want)
    with _lock:
        if _model is not None and _model_key == key:
            return _model
        _add_cuda_dll_dirs()
        from faster_whisper import WhisperModel
        model = None
        if want in ("auto", "cuda"):
            try:
                import ctranslate2
                if ctranslate2.get_cuda_device_count() > 0:
                    model = WhisperModel(size, device="cuda", compute_type="float16")
                    device_used = "cuda"
            except Exception:  # noqa: BLE001
                if want == "cuda":
                    raise
                model = None
        if model is None:
            model = WhisperModel(size, device="cpu", compute_type="int8")
            device_used = "cpu"
        _model, _model_key = model, key
        return _model


def load_wav(wav: Path):
    """16kHz mono PCM wav → float32 배열 (faster-whisper의 PyAV 디코더를 거치지 않음)."""
    import wave

    import numpy as np
    with wave.open(str(wav), "rb") as w:
        if w.getframerate() != 16000 or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError("16kHz mono 16bit wav가 아닙니다")
        data = w.readframes(w.getnframes())
    return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0


def transcribe(wav: Path) -> dict[str, Any]:
    """원본 기준 segments. 결과가 짧거나 신뢰도가 낮으면 has_speech=False."""
    cfg = C.get_config()["ai"]["stt"]
    model = get_model()
    try:
        segs_iter, info = model.transcribe(load_wav(wav), language=cfg.get("language") or None, vad_filter=True,
                                           beam_size=5, condition_on_previous_text=False)
        segs = list(segs_iter)
    except RuntimeError as e:
        # CUDA 라이브러리 문제면 CPU로 한 번 더
        if device_used == "cuda" and ("cuda" in str(e).lower() or "cublas" in str(e).lower()
                                      or "cudnn" in str(e).lower()):
            _force_cpu()
            return transcribe(wav)
        raise
    segments = [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip(),
                 "avg_logprob": round(s.avg_logprob, 3), "no_speech_prob": round(s.no_speech_prob, 3)}
                for s in segs if s.text.strip()]
    text = " ".join(s["text"] for s in segments).strip()
    if segments:
        w = sum(max(0.01, s["end"] - s["start"]) for s in segments)
        logprob = sum(s["avg_logprob"] * max(0.01, s["end"] - s["start"]) for s in segments) / w
        nospeech = sum(s["no_speech_prob"] * max(0.01, s["end"] - s["start"]) for s in segments) / w
    else:
        logprob, nospeech = -9.0, 1.0
    reason = None
    if len(text.replace(" ", "")) < 5:
        reason = "인식 결과 5자 미만"
    elif logprob < -1.0 or nospeech > 0.6:
        reason = f"신뢰도 낮음 (logprob {logprob:.2f}, no_speech {nospeech:.2f})"
    return {"language": info.language, "language_probability": round(info.language_probability, 3),
            "text": text, "segments": segments, "avg_logprob": round(logprob, 3),
            "no_speech_prob": round(nospeech, 3), "has_speech": reason is None, "reason": reason,
            "device": device_used}


def _force_cpu() -> None:
    global _model, _model_key, device_used
    from faster_whisper import WhisperModel
    size = C.get_config()["ai"]["stt"].get("model_size", "small")
    with _lock:
        _model = WhisperModel(size, device="cpu", compute_type="int8")
        _model_key = (size, "auto")
        device_used = "cpu"


def remap_segments(segments: list[dict], params: dict) -> list[dict]:
    """편집본 시각 = (원본 시각 - trim) / speed + 인트로 길이.
    trim 이전 구간은 버리고, 경계에 걸친 구간은 시작을 잘린 지점으로 맞춘다."""
    trim = float(params.get("trim_start_sec") or 0)
    speed = float(params.get("speed") or 1)
    intro = float(params.get("intro_duration") or 0)
    main_end = intro + float(params.get("main_duration") or 1e9)
    out = []
    for s in segments:
        if s["end"] <= trim:
            continue
        start = max(s["start"], trim)
        a = (start - trim) / speed + intro
        b = (s["end"] - trim) / speed + intro
        b = min(b, main_end)
        if b - a < 0.05:
            continue
        out.append({"start": round(a, 2), "end": round(b, 2), "text": s["text"]})
    return out
