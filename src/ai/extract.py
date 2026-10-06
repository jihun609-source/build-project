"""오디오·프레임 추출."""
from __future__ import annotations

import json
import re
from pathlib import Path

from PIL import Image

from .. import config as C
from ..util import proc

MEAN_RE = re.compile(r"mean_volume:\s*(-?[\d.]+|-inf) dB")
PTS_RE = re.compile(r"pts_time:([\d.]+)")


def extract_audio(source: Path, out_wav: Path, has_audio: bool) -> dict:
    """원본(속도 변경 전)에서 16kHz mono wav + 평균 음량으로 무음 판정."""
    cfg = C.get_config()
    if not has_audio:
        return {"has_audio": False, "mean_volume": None, "silent": True}
    tmp = out_wav.with_suffix(".tmp.wav")
    r = proc.run([*proc.tool("ffmpeg"), "-hide_banner", "-nostdin", "-y", "-i", str(source), "-vn",
                  "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(tmp)], timeout=300)
    if r.returncode != 0:
        raise proc.ProcError("오디오 추출 실패: " + r.stderr.strip()[-300:])
    tmp.replace(out_wav)
    r = proc.run([*proc.tool("ffmpeg"), "-hide_banner", "-nostdin", "-i", str(out_wav), "-af", "volumedetect",
                  "-f", "null", "-"], timeout=300)
    m = MEAN_RE.search(r.stderr)
    mean = None if not m or m.group(1) == "-inf" else float(m.group(1))
    silent = mean is None or mean <= float(cfg["ai"].get("silence_db", -45))
    return {"has_audio": True, "mean_volume": mean, "silent": silent}


def extract_frames(edited: Path, out_dir: Path, params: dict, count: int) -> list[dict]:
    """편집본에서 장면 전환 후보 + 균등 분할로 count장. 인트로·아웃트로와 앞뒤 0.5초 제외."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.jpg"):
        old.unlink()
    total = float(params.get("edited_duration") or params.get("expected_duration") or 0)
    intro = float(params.get("intro_duration") or 0)
    outro = float(params.get("outro_duration") or 0)
    lo = max(0.5, intro + 0.5 if intro else 0.5)
    hi = min(total - 0.5, total - outro - 0.5 if outro else total - 0.5)
    if hi <= lo:
        lo, hi = 0.0, max(0.1, total - 0.05)

    # 장면 전환 후보 (축소본으로 빠르게)
    r = proc.run([*proc.tool("ffmpeg"), "-hide_banner", "-nostdin", "-ss", f"{lo:.3f}", "-to", f"{hi:.3f}",
                  "-i", str(edited), "-vf", "scale=270:-2,select='gt(scene,0.3)',showinfo", "-an",
                  "-fps_mode", "vfr", "-f", "null", "-"], timeout=300)
    scenes = sorted({round(lo + float(m.group(1)), 2) for m in PTS_RE.finditer(r.stderr)})
    scenes = [t for t in scenes if lo <= t <= hi]
    picks: list[float] = []
    if len(scenes) > count:
        step = len(scenes) / count
        picks = [scenes[int(i * step)] for i in range(count)]
    else:
        picks = list(scenes)
    # 부족분은 균등 분할 지점으로 채움 (기존 후보와 0.5초 이상 떨어진 지점만)
    span = hi - lo
    k = 0
    while len(picks) < count and k < count * 4:
        slots = count + k
        for i in range(slots):
            t = round(lo + span * (i + 0.5) / slots, 2)
            if all(abs(t - p) >= 0.5 for p in picks):
                picks.append(t)
                if len(picks) >= count:
                    break
        k += 1
    picks = sorted(picks)[:count]

    frames = []
    for i, t in enumerate(picks):
        png = out_dir / f"{i:02d}.png"
        r = proc.run([*proc.tool("ffmpeg"), "-hide_banner", "-nostdin", "-y", "-ss", f"{t:.3f}", "-i", str(edited),
                      "-frames:v", "1", str(png)], timeout=60)
        if r.returncode != 0 or not png.exists():
            continue
        jpg = out_dir / f"{i:02d}.jpg"
        with Image.open(png) as im:
            im = im.convert("RGB")
            im.thumbnail((768, 768))
            im.save(jpg, "JPEG", quality=80)
        png.unlink(missing_ok=True)
        frames.append({"file": jpg.name, "time": t, "scene": t in scenes})
    (out_dir / "frames.json").write_text(json.dumps(frames, ensure_ascii=False), encoding="utf-8")
    return frames
