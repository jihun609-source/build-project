"""편집 워커: 프리셋 스냅샷(edit_params) 기준으로 ffmpeg 한 번의 필터체인 실행.

적용 순서 (고정):
  1. 앞부분 자르기  2. 속도  3. 확대(중앙 크롭)  4. 좌우반전  5. 워터마크  6. 인트로·아웃트로
출력: 1080x1920, h264, aac, 30fps
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from .. import config as C
from .. import models as M
from .. import storage
from ..util import proc
from .base import Worker

W, H, FPS = 1080, 1920, 30
AR = "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo"
WM_POS = {
    "top_left": ("40", "40"),
    "top_right": ("W-w-40", "40"),
    "bottom_left": ("40", "H-h-40"),
    "bottom_right": ("W-w-40", "H-h-40"),
    "center": ("(W-w)/2", "(H-h)/2"),
}


def _fit(label_in: str, label_out: str) -> str:
    return (f"[{label_in}]scale={W}:{H}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={FPS},format=yuv420p[{label_out}]")


def encoder_args(cfg: dict) -> list[str]:
    crf = str(int(cfg["edit"].get("crf", 20)))
    if cfg["edit"].get("encoder") == "h264_nvenc":
        v = ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", crf, "-b:v", "0"]
    else:
        v = ["-c:v", "libx264", "-preset", "veryfast", "-crf", crf]
    return [*v, "-pix_fmt", "yuv420p", "-r", str(FPS), "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
            "-movflags", "+faststart"]


def plan(source_meta: dict, video: dict) -> dict[str, Any]:
    """길이 계산 (진행률·자막 구간·STT 시각 변환에 사용)."""
    trim = float(video.get("trim_start_sec") or 0)
    speed = float(video.get("speed") or 1.0)
    src = float(source_meta["duration"])
    if trim >= src:
        raise ValueError(f"앞부분 자르기({trim}초)가 영상 길이({src:.1f}초) 이상입니다")
    intro = outro = 0.0
    for key in ("intro_path", "outro_path"):
        p = C.root_path(video.get(key))
        if p:
            if not p.exists():
                raise ValueError(f"{key} 파일이 없습니다: {p}")
            d = proc.probe(p)["duration"]
            if key == "intro_path":
                intro = d
            else:
                outro = d
    main = (src - trim) / speed
    return {"source_duration": round(src, 3), "trim_start_sec": trim, "speed": speed,
            "main_duration": round(main, 3), "intro_duration": round(intro, 3),
            "outro_duration": round(outro, 3), "expected_duration": round(intro + main + outro, 3)}


def build_command(src: Path, source_meta: dict, video: dict, out: Path, *, preview_sec: float | None = None,
                  subtitles: str | None = None, fontsdir: str | None = None) -> tuple[list[str], float]:
    """ffmpeg 인자 목록과 진행률 기준 전체 길이."""
    cfg = C.get_config()
    info = plan(source_meta, video)
    trim, speed = info["trim_start_sec"], info["speed"]
    scale = float(video.get("scale_factor") or 1.0)
    keep_audio = bool(video.get("keep_audio", True)) and source_meta.get("has_audio")
    bookends = preview_sec is None

    inputs: list[str] = []
    if trim > 0:
        inputs += ["-ss", f"{trim:.3f}"]
    inputs += ["-i", str(src)]
    n = 1
    wm_idx = intro_idx = outro_idx = None
    wm = C.root_path(video.get("watermark_path"))
    if wm and wm.exists():
        inputs += ["-i", str(wm)]
        wm_idx, n = n, n + 1
    intro = C.root_path(video.get("intro_path")) if bookends else None
    outro = C.root_path(video.get("outro_path")) if bookends else None
    intro_meta = outro_meta = None
    if intro:
        inputs += ["-i", str(intro)]
        intro_idx, n, intro_meta = n, n + 1, proc.probe(intro)
    if outro:
        inputs += ["-i", str(outro)]
        outro_idx, n, outro_meta = n, n + 1, proc.probe(outro)

    f: list[str] = []
    # 1~2. 자르기(-ss) 후 속도
    chain = [f"setpts=(PTS-STARTPTS)/{speed:.4f}" if speed != 1 else "setpts=PTS-STARTPTS",
             f"scale={W}:{H}:force_original_aspect_ratio=decrease",
             f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black", "setsar=1"]
    # 3. 확대 후 중앙 크롭
    if scale > 1.0001:
        chain += [f"scale=trunc({W}*{scale:.4f}/2)*2:trunc({H}*{scale:.4f}/2)*2", f"crop={W}:{H}"]
    # 4. 좌우반전
    if video.get("hflip"):
        chain.append("hflip")
    chain += [f"fps={FPS}", "format=yuv420p"]
    f.append(f"[0:v]{','.join(chain)}[vm0]")
    vlabel = "vm0"
    # 5. 워터마크
    if wm_idx is not None:
        x, y = WM_POS.get(video.get("watermark_position") or "bottom_right", WM_POS["bottom_right"])
        f.append(f"[{wm_idx}:v]format=rgba,scale={int(W * 0.2)}:-1[wm]")
        f.append(f"[{vlabel}][wm]overlay={x}:{y}:format=auto,format=yuv420p[vm1]")
        vlabel = "vm1"
    # 미리보기: 자막 합성까지 한 번에
    if subtitles:
        sub = f"subtitles=filename={subtitles}"
        if fontsdir:
            sub += f":fontsdir={fontsdir}"
        f.append(f"[{vlabel}]{sub}[vm2]")
        vlabel = "vm2"

    main_dur = info["main_duration"]
    if keep_audio:
        a = ["asetpts=PTS-STARTPTS"]
        if speed != 1:
            a.append(f"atempo={speed:.4f}")
        a.append(AR)
        f.append(f"[0:a]{','.join(a)}[am]")
    else:
        f.append(f"anullsrc=r=44100:cl=stereo,atrim=duration={main_dur:.3f},{AR}[am]")

    # 6. 인트로·아웃트로 (원속도)
    segs: list[tuple[str, str]] = []
    for idx, meta, tag in ((intro_idx, intro_meta, "i"), (None, None, "m"), (outro_idx, outro_meta, "o")):
        if tag == "m":
            segs.append((vlabel, "am"))
            continue
        if idx is None:
            continue
        f.append(_fit(f"{idx}:v", f"v{tag}"))
        if meta and meta.get("has_audio"):
            f.append(f"[{idx}:a]asetpts=PTS-STARTPTS,{AR}[a{tag}]")
        else:
            f.append(f"anullsrc=r=44100:cl=stereo,atrim=duration={meta['duration']:.3f},{AR}[a{tag}]")
        segs.append((f"v{tag}", f"a{tag}"))
    if len(segs) > 1:
        f.append("".join(f"[{v}][{a}]" for v, a in segs) + f"concat=n={len(segs)}:v=1:a=1[vout][aout]")
        vmap, amap = "vout", "aout"
    else:
        vmap, amap = vlabel, "am"

    total = info["expected_duration"] if bookends else main_dur
    args = [*inputs, "-filter_complex", ";".join(f), "-map", f"[{vmap}]", "-map", f"[{amap}]",
            *encoder_args(cfg)]
    if preview_sec:
        args += ["-t", f"{preview_sec:.2f}"]
        total = min(total, preview_sec)
    args.append(str(out))
    return args, total


def load_preset_snapshot(name: str | None) -> tuple[str, dict]:
    cfg = C.get_config()
    name = name or cfg["edit"]["default_preset"]
    try:
        p = storage.read_preset(name)
    except storage.StorageError:
        name = "default"
        p = storage.read_preset("default")
    return name, {"video": copy.deepcopy(p["video"]), "caption": copy.deepcopy(p["caption"])}


def neutral_video() -> dict:
    """영상 편집 없이 규격(1080x1920, 30fps)만 맞추는 설정 (직접 편집해 올린 영상용)."""
    from ..defaults import DEFAULT_PRESET
    v = copy.deepcopy(DEFAULT_PRESET["video"])
    v.update(trim_start_sec=0, speed=1.0, keep_audio=True, scale_factor=1.0, hflip=False,
             intro_path=None, outro_path=None, watermark_path=None)
    return v


class Editor(Worker):
    name = "editor"
    stage = "edit"
    fail_status = "failed_edit"

    def tick(self) -> bool:
        item = M.claim_item("downloaded", "editing", "edit")
        if not item:
            return False
        self.process_claimed(item, "downloaded")
        return True

    def handle(self, item: dict[str, Any]) -> None:
        item_id = item["id"]
        src = Path(item["source_path"] or C.DIRS["inbox"] / f"{item_id}.mp4")
        if not src.exists():
            raise RuntimeError(f"원본 파일이 없습니다: {src}")
        meta = item.get("source_meta") or proc.probe(src)
        name, snap = load_preset_snapshot(item.get("edit_preset"))
        if name != item.get("edit_preset"):
            self.log("warning", f"프리셋 '{item.get('edit_preset')}' 이 없어 default 사용", item_id)
        if not item.get("edit_video", True):
            # 직접 편집한 영상: 프리셋의 자막 설정만 쓰고 영상은 규격만 맞춘다
            snap["video"] = neutral_video()
            self.log("info", "영상 편집 없이 규격만 맞춤 (자막 설정은 프리셋 사용)", item_id)
        info = plan(meta, snap["video"])
        # 스냅샷을 먼저 기록: 처리 도중 프리셋이 바뀌어도 이 항목은 이 값으로 끝까지 간다
        M.update_item(item_id, edit_preset=name, edit_params={"preset": name, **snap, **info})
        out =C.DIRS["work"] / f"{item_id}_edited.mp4"
        tmp = out.with_name(out.stem + ".tmp.mp4")
        args, total = build_command(src, meta, snap["video"], tmp)
        self.log("info", f"편집 시작 (프리셋 {name}, 예상 {info['expected_duration']:.1f}초)", item_id)
        cfg = C.get_config()

        def attempt():
            proc.ffmpeg_with_progress(args, total, lambda p: self.progress(item_id, p),
                                      timeout=float(cfg["edit"].get("timeout_sec", 1800)), cancel=self.stopping)

        proc.retry(attempt, 3, 3, on_retry=lambda n, e: self.log("warning", f"편집 재시도 {n}/2: {e}", item_id))
        tmp.replace(out)
        edited = proc.probe(out)["duration"]
        params = {"preset": name, **snap, **info, "edited_duration": edited}
        M.update_item(item_id, status="edited", edited_path=str(out), edit_preset=name, edit_params=params,
                      edited_duration=edited, progress_pct=100)
        self.log("info", f"편집 완료 원본 {info['source_duration']:.1f}초 → {edited:.1f}초", item_id)


if __name__ == "__main__":
    from ..main import bootstrap
    bootstrap()
    Editor().run_forever()
