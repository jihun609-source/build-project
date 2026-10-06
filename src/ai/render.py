"""하단 캡션 ASS 생성과 ffmpeg(libass) burn-in."""
from __future__ import annotations

import shutil
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from PIL import ImageFont

from .. import config as C
from ..util import proc

W, H = 1080, 1920
UPPER_RATIO = 0.12
DEFAULT_SIDE_PX = 40


@lru_cache(maxsize=32)
def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def font_name(path: str) -> tuple[str, bool]:
    family, style = _font(path, 40).getname()
    return family, "bold" in (style or "").lower()


@lru_cache(maxsize=32)
def ass_scale(path: str) -> float:
    """ASS(libass)의 Fontsize는 글자 한 칸(em)이 아니라 줄 높이(ascent+descent)다.
    같은 숫자라도 실제 글자는 em / (ascent+descent) 배로 그려진다 (맑은 고딕 ≈ 0.75)."""
    ascent, descent = _font(path, 1000).getmetrics()
    return 1000 / max(1, ascent + descent)


def text_width(text: str, path: str, size: int) -> float:
    """libass가 실제로 그리는 폭 (px)."""
    return _font(path, size).getlength(text) * ass_scale(path)


def _wrap_line(line: str, path: str, size: int, max_w: float) -> list[str]:
    if text_width(line, path, size) <= max_w:
        return [line]
    out, cur = [], ""
    for word in line.split(" "):
        cand = f"{cur} {word}".strip()
        if text_width(cand, path, size) <= max_w:
            cur = cand
            continue
        if cur:
            out.append(cur)
        # 어절 하나가 한 줄보다 길면 글자 단위로 자름
        if text_width(word, path, size) > max_w:
            piece = ""
            for ch in word:
                if text_width(piece + ch, path, size) > max_w and piece:
                    out.append(piece)
                    piece = ch
                else:
                    piece += ch
            cur = piece
        else:
            cur = word
    if cur:
        out.append(cur)
    return out


def _balanced(line: str, path: str, size: int, max_w: float) -> list[str]:
    """폭을 넘는 줄을 나누되, 같은 줄 수 안에서 줄 길이를 고르게 (끝에 한 단어만 남는 것 방지)."""
    best = _wrap_line(line, path, size, max_w)
    if len(best) <= 1:
        return best
    limit = max_w
    while limit > max_w * 0.4:
        limit -= 12
        cand = _wrap_line(line, path, size, limit)
        if len(cand) > len(best) or any(text_width(c, path, size) > max_w for c in cand):
            break
        best = cand
    return best


def wrap(text: str, path: str, size: int, min_size: int, max_w: float, max_lines: int = 2) -> tuple[list[str], int]:
    """1) AI가 준 줄바꿈을 그대로 두고 폰트를 min_size까지 줄여 한 줄씩 맞춘다
    2) 그래도 넘치면 줄을 고르게 나누고, max_lines 안에 드는 가장 큰 크기를 쓴다
    3) 최소 크기에서도 넘치면 max_lines까지만 남기고 말줄임"""
    text = (text or "").replace("\\n", "\n").strip()
    given = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if not given:
        return [], size
    sizes = list(range(size, min_size - 1, -2)) or [size]
    if sizes[-1] != min_size and min_size < size:
        sizes.append(min_size)
    if len(given) <= max_lines:
        for s_ in sizes:
            if all(text_width(ln, path, s_) <= max_w for ln in given):
                return given, s_
    for s_ in sizes:
        lines: list[str] = []
        for ln in given:
            lines += _balanced(ln, path, s_, max_w)
        if len(lines) <= max_lines:
            return lines, s_
    s_ = sizes[-1]
    lines = []
    for ln in given:
        lines += _balanced(ln, path, s_, max_w)
    lines = lines[:max_lines]
    last = lines[-1]
    while last and text_width(last + "…", path, s_) > max_w:
        last = last[:-1]
    lines[-1] = last.rstrip() + "…"
    return lines, s_


def ass_color(hex_rgb: str, opacity: float = 1.0) -> str:
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    alpha = max(0, min(255, round((1 - opacity) * 255)))
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_escape(s: str) -> str:
    return s.replace("\\", "＼").replace("{", "(").replace("}", ")")


def _placement(position: str, bottom_ratio: float) -> tuple[int, int]:
    """(Alignment, MarginV)"""
    if position == "upper":
        return 8, int(H * UPPER_RATIO)
    if position == "center":
        return 5, 0
    return 2, int(H * max(0.0, bottom_ratio))


def display_range(params: dict) -> tuple[float, float]:
    cap = params.get("caption", {})
    intro = float(params.get("intro_duration") or 0)
    main = float(params.get("main_duration") or 0)
    total = float(params.get("edited_duration") or intro + main + float(params.get("outro_duration") or 0))
    start = cap.get("start_sec")
    end = cap.get("end_sec")
    start = intro if start is None else float(start)
    end = (intro + main) if end is None else float(end)
    start = max(0.0, min(start, total))
    end = max(start + 0.1, min(end, total))
    return start, end


def build_ass(params: dict, caption: str | None, segments: list[dict] | None, has_speech: bool) -> tuple[str, dict]:
    cap = params["caption"]
    font_path = str(C.root_path(cap["font_path"]))
    family, bold = font_name(font_path)
    size, min_size = int(cap["font_size"]), int(cap["min_font_size"])
    side = cap.get("side_margin_ratio")
    side_px = DEFAULT_SIDE_PX if side is None else int(W * float(side))
    # 한 줄 폭 = min(최대 폭, 화면 폭 - 좌우 여백)
    max_w = min(W * float(cap.get("max_width_ratio", 0.85)), W - 2 * side_px)
    mode = cap.get("mode", "summary")
    if mode in ("speech", "both") and not (has_speech and segments):
        mode = "summary" if mode == "speech" else "summary_only"
    fade = int(cap.get("fade_ms", 200))
    # max_lines가 없는 예전 스냅샷은 당시 규칙(2줄) 유지
    max_lines = int(cap.get("max_lines", 2))
    pad = max(8, int(size * 0.28))
    text_c = ass_color(cap["color"])
    outline_c = ass_color(cap["outline_color"])
    box_c = ass_color(cap["box_color"], float(cap.get("box_opacity", 0.45)))
    bold_flag = -1 if bold else 0

    styles: list[str] = []
    events: list[str] = []

    def add_style(name: str, align: int, margin_v: int, fsize: int):
        styles.append(f"Style: {name},{family},{fsize},{text_c},{text_c},{outline_c},&H00000000,{bold_flag},0,0,0,"
                      f"100,100,0,0,1,{cap['outline_width']},0,{align},{side_px},{side_px},{margin_v},1")
        if cap.get("box"):
            styles.append(f"Style: {name}Box,{family},{fsize},{text_c},{text_c},{box_c},{box_c},{bold_flag},0,0,0,"
                          f"100,100,0,0,3,{pad},0,{align},{side_px},{side_px},{margin_v},1")

    def add_event(style: str, start: float, end: float, lines: list[str], fsize: int, base: int):
        text = "\\N".join(ass_escape(ln) for ln in lines)
        tags = f"{{\\fad({fade},{fade})}}" if fade else ""
        if fsize != base:
            tags += f"{{\\fs{fsize}}}"
        if cap.get("box"):
            events.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},{style}Box,,0,0,0,,{tags}{text}")
        events.append(f"Dialogue: 1,{ass_time(start)},{ass_time(end)},{style},,0,0,0,,{tags}{text}")

    info: dict[str, Any] = {"mode_used": mode}
    start, end = display_range(params)
    main_align, main_margin = _placement(cap.get("position", "lower_safe"), float(cap.get("bottom_margin_ratio", .25)))

    if mode in ("summary", "summary_only") and caption:
        add_style("Caption", main_align, main_margin, size)
        lines, fs = wrap(caption, font_path, size, min_size, max_w, max_lines)
        add_event("Caption", start, end, lines, fs, size)
        info.update(lines=lines, font_size=fs)
    elif mode in ("speech", "both"):
        sp_size = max(min_size, int(size * 0.85))
        if mode == "both" and caption:
            # caption은 위쪽 고정, 대사는 하단 기준선
            up_align, up_margin = _placement("upper", 0)
            add_style("Caption", up_align, up_margin, size)
            lines, fs = wrap(caption, font_path, size, min_size, max_w, max_lines)
            add_event("Caption", start, end, lines, fs, size)
            info.update(lines=lines, font_size=fs)
            sp_align, sp_margin = _placement("lower_safe", float(cap.get("bottom_margin_ratio", .25)))
        else:
            sp_align, sp_margin = main_align, main_margin
        add_style("Speech", sp_align, sp_margin, sp_size)
        for seg in segments or []:
            a, b = max(seg["start"], 0), seg["end"]
            if b <= a:
                continue
            lines, fs = wrap(seg["text"], font_path, sp_size, min_size, max_w, min(2, max_lines))
            add_event("Speech", a, b, lines, fs, sp_size)
        info["speech_segments"] = len(segments or [])

    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\nYCbCr Matrix: TV.709\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
    )
    body = "\n".join(styles) + "\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, " \
                               "Effect, Text\n" + "\n".join(events) + "\n"
    return header + body, info


def prepare_fonts(workdir: Path, font_path: str) -> str:
    """libass가 폰트를 확실히 찾도록 작업 폴더 fonts/ 에 복사. 반환: 상대 폴더명."""
    fonts = workdir / "fonts"
    fonts.mkdir(parents=True, exist_ok=True)
    src = C.root_path(font_path)
    dst = fonts / src.name
    if not dst.exists() or dst.stat().st_size != src.stat().st_size:
        shutil.copy2(src, dst)
    return "fonts"


def burn(edited: Path, out: Path, ass_text: str, workdir: Path, font_path: str, total: float,
         on_pct: Callable[[float], None], cancel: Callable[[], bool] | None = None) -> None:
    from ..workers.editor import encoder_args
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "caption.ass").write_text(ass_text, encoding="utf-8")
    fontsdir = prepare_fonts(workdir, font_path)
    tmp = out.with_name(out.stem + ".tmp.mp4")
    out.parent.mkdir(parents=True, exist_ok=True)
    args = ["-i", str(edited), "-vf", f"subtitles=filename=caption.ass:fontsdir={fontsdir}",
            "-map", "0:v", "-map", "0:a?", *encoder_args(C.get_config()), str(tmp)]
    proc.ffmpeg_with_progress(args, total, on_pct, timeout=float(C.get_config()["edit"].get("timeout_sec", 1800)),
                              cwd=workdir, cancel=cancel)
    tmp.replace(out)
