"""설정·프리셋·프롬프트 파일 저장 공통 처리.

- 모든 쓰기는 임시 파일에 쓴 뒤 os.replace 로 교체 (저장 중 전원이 꺼져도 원본 유지)
- 덮어쓰기 전 기존 파일을 .history/ 아래에 `{key}@{타임스탬프}{확장자}` 로 백업
- 보관 개수는 config.history.keep
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from . import config as C
from .defaults import DEFAULT_PRESET, DEFAULT_PROMPT_FILES

_write_lock = threading.RLock()
NAME_RE = re.compile(r"^[0-9A-Za-z가-힣_\-]{1,40}$")
PROMPT_FILES = ["system.md", "user.md", "schema.json", "examples.md"]


class StorageError(Exception):
    def __init__(self, message: str, fields: dict[str, str] | None = None, status: int = 400):
        super().__init__(message)
        self.fields = fields or {}
        self.status = status


# ------------------------------------------------------------ 기본 파일 쓰기
def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def dump_yaml(data: Any) -> str:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)


# ------------------------------------------------------------ 이력
def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]


class History:
    """history_dir 안에 key@stamp.ext 형식으로 보관."""

    def __init__(self, history_dir: Path):
        self.dir = history_dir

    def backup(self, key: str, current: Path) -> str | None:
        if not current.exists():
            return None
        self.dir.mkdir(parents=True, exist_ok=True)
        version = f"{key}@{_stamp()}{current.suffix}"
        shutil.copy2(current, self.dir / version)
        self.prune(key)
        return version

    def list(self, key: str | None = None) -> list[dict[str, Any]]:
        if not self.dir.exists():
            return []
        out = []
        for p in self.dir.iterdir():
            if not p.is_file() or "@" not in p.name:
                continue
            k, rest = p.name.split("@", 1)
            if key is not None and k != key:
                continue
            stamp = rest.rsplit(".", 1)[0] if "." in rest else rest
            try:
                ts = datetime.strptime(stamp, "%Y%m%d-%H%M%S-%f").isoformat(timespec="seconds")
            except ValueError:
                ts = stamp
            out.append({"version": p.name, "key": k, "saved_at": ts, "size": p.stat().st_size})
        out.sort(key=lambda x: x["version"].split("@", 1)[1], reverse=True)
        return out

    def read(self, version: str) -> str:
        p = self.dir / version
        if "/" in version or "\\" in version or ".." in version or not p.is_file():
            raise StorageError("버전을 찾을 수 없습니다", status=404)
        return p.read_text(encoding="utf-8")

    def prune(self, key: str) -> None:
        keep = int(C.get_config().get("history", {}).get("keep", 50))
        for entry in self.list(key)[keep:]:
            try:
                (self.dir / entry["version"]).unlink()
            except OSError:
                pass

    def rename_key(self, old: str, new: str) -> None:
        for entry in self.list(old):
            src = self.dir / entry["version"]
            src.rename(self.dir / (new + "@" + entry["version"].split("@", 1)[1]))

    def drop_key(self, key: str) -> None:
        for entry in self.list(key):
            try:
                (self.dir / entry["version"]).unlink()
            except OSError:
                pass


config_history = History(C.DIRS["config_history"])
preset_history = History(C.DIRS["presets_history"])


def prompt_history(profile: str) -> History:
    return History(C.DIRS["prompts_history"] / profile)


def save_with_backup(path: Path, text: str, history: History, key: str) -> str | None:
    with _write_lock:
        if path.exists() and path.read_text(encoding="utf-8") == text:
            return None
        version = history.backup(key, path)
        atomic_write(path, text)
        return version


# ------------------------------------------------------------ 초기 파일
def ensure_initial_files() -> None:
    for d in C.DIRS.values():
        if d.suffix == "":
            d.mkdir(parents=True, exist_ok=True)
    if C.FROZEN:
        # 프로그램 파일은 읽기 전용, 확장프로그램의 사용자 셀렉터 설정은 데이터 폴더에 보존한다.
        source = C.APP_ROOT / "chrome-extension"
        for path in source.rglob("*"):
            if path.is_file():
                relative = path.relative_to(source)
                target = C.DIRS["extension"] / relative
                if relative.as_posix() == "upload/selectors.json" and target.exists():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists() or path.read_bytes() != target.read_bytes():
                    shutil.copy2(path, target)
        example = C.APP_ROOT / ".env.example"
        if example.exists() and not (C.ROOT / ".env").exists():
            shutil.copy2(example, C.ROOT / ".env")
    # config.yaml
    if not C.CONFIG_PATH.exists():
        cfg = copy.deepcopy(C.DEFAULT_CONFIG)
        C.ensure_token(cfg)
        atomic_write(C.CONFIG_PATH, dump_yaml(cfg))
    else:
        raw = yaml.safe_load(C.CONFIG_PATH.read_text(encoding="utf-8")) or {}
        if C.ensure_token(raw):
            atomic_write(C.CONFIG_PATH, dump_yaml(raw))
    C.invalidate()
    # 기본 프리셋
    default_path = C.DIRS["presets"] / "default.yaml"
    if not default_path.exists():
        atomic_write(default_path, dump_yaml(DEFAULT_PRESET))
    # 기본 프롬프트
    prof = C.DIRS["prompts"] / "caption"
    for name, text in DEFAULT_PROMPT_FILES.items():
        if not (prof / name).exists():
            atomic_write(prof / name, text)


# ------------------------------------------------------------ 전역 설정
def read_config_raw() -> dict[str, Any]:
    if not C.CONFIG_PATH.exists():
        return {}
    return yaml.safe_load(C.CONFIG_PATH.read_text(encoding="utf-8")) or {}


def save_config(new_cfg: dict[str, Any]) -> str | None:
    merged = C.deep_merge(C.DEFAULT_CONFIG, new_cfg)
    errors = C.validate_config(merged)
    if errors:
        raise StorageError("설정 값이 올바르지 않습니다", errors)
    # 토큰은 UI에서 지워지지 않게 유지
    if not (merged.get("ui") or {}).get("token"):
        merged.setdefault("ui", {})["token"] = C.get_config()["ui"]["token"]
    presets = list_presets()
    if merged["edit"]["default_preset"] not in presets:
        raise StorageError("설정 값이 올바르지 않습니다",
                           {"edit.default_preset": "존재하지 않는 프리셋입니다"})
    if not (C.DIRS["prompts"] / merged["ai"]["prompt_profile"]).is_dir():
        raise StorageError("설정 값이 올바르지 않습니다",
                           {"ai.prompt_profile": "존재하지 않는 프롬프트 프로필입니다"})
    version = save_with_backup(C.CONFIG_PATH, dump_yaml(merged), config_history, "config")
    C.invalidate()
    return version


def patch_config(patch: dict[str, Any]) -> str | None:
    return save_config(C.deep_merge(C.get_config(), patch))


def restore_config(version: str) -> None:
    text = config_history.read(version)
    data = yaml.safe_load(text) or {}
    save_config(data)


# ------------------------------------------------------------ 편집 프리셋
PRESET_RULES: dict[str, tuple] = {
    # 경로: (타입, 최소, 최대)
    "video.trim_start_sec": ("num", 0, 600),
    "video.speed": ("num", 0.5, 2.0),
    "video.keep_audio": ("bool",),
    "video.scale_factor": ("num", 1.0, 1.5),
    "video.hflip": ("bool",),
    "caption.bottom_margin_ratio": ("num", 0.0, 0.40),
    "caption.side_margin_ratio": ("num", 0.0, 0.20),
    "caption.font_size": ("num", 16, 200),
    "caption.min_font_size": ("num", 12, 200),
    "caption.outline_width": ("num", 0, 20),
    "caption.box": ("bool",),
    "caption.box_opacity": ("num", 0, 1),
    "caption.max_width_ratio": ("num", 0.3, 1.0),
    "caption.max_lines": ("num", 1, 8),
    "caption.fade_ms": ("num", 0, 3000),
}
WATERMARK_POSITIONS = ["top_left", "top_right", "bottom_left", "bottom_right", "center"]
COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def normalize_preset(data: dict[str, Any], name: str) -> dict[str, Any]:
    merged = C.deep_merge(DEFAULT_PRESET, data or {})
    merged["name"] = name
    return merged


def validate_preset(p: dict[str, Any]) -> dict[str, str]:
    err: dict[str, str] = {}
    for path, rule in PRESET_RULES.items():
        sec, key = path.split(".")
        v = p.get(sec, {}).get(key)
        if rule[0] == "bool":
            if not isinstance(v, bool):
                err[path] = "true/false"
        else:
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                err[path] = "숫자여야 합니다"
            elif not (rule[1] <= v <= rule[2]):
                err[path] = f"{rule[1]} ~ {rule[2]} 범위여야 합니다"
    cap, vid = p.get("caption", {}), p.get("video", {})
    if cap.get("mode") not in ("summary", "speech", "both"):
        err["caption.mode"] = "summary / speech / both"
    if cap.get("position") not in ("lower_safe", "upper", "center"):
        err["caption.position"] = "lower_safe / upper / center"
    for k in ("color", "outline_color", "box_color"):
        if not COLOR_RE.match(str(cap.get(k, ""))):
            err[f"caption.{k}"] = "#RRGGBB 형식"
    if isinstance(cap.get("min_font_size"), (int, float)) and isinstance(cap.get("font_size"), (int, float)):
        if cap["min_font_size"] > cap["font_size"]:
            err["caption.min_font_size"] = "기본 크기보다 클 수 없습니다"
    for k in ("start_sec", "end_sec"):
        v = cap.get(k)
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0):
            err[f"caption.{k}"] = "0 이상의 초 또는 비움"
    if isinstance(cap.get("start_sec"), (int, float)) and isinstance(cap.get("end_sec"), (int, float)):
        if cap["end_sec"] <= cap["start_sec"]:
            err["caption.end_sec"] = "시작보다 커야 합니다"
    if not cap.get("font_path") or not C.root_path(cap["font_path"]).exists():
        err["caption.font_path"] = "폰트 파일이 없습니다"
    if vid.get("watermark_position") not in WATERMARK_POSITIONS:
        err["video.watermark_position"] = "/".join(WATERMARK_POSITIONS)
    for k in ("intro_path", "outro_path", "watermark_path"):
        v = vid.get(k)
        if v and not (C.root_path(v) and C.root_path(v).exists()):
            err[f"video.{k}"] = "파일이 없습니다"
    return err


def _preset_path(name: str) -> Path:
    if not NAME_RE.match(name or ""):
        raise StorageError("프리셋 이름은 한글·영문·숫자·_- 1~40자", {"name": "잘못된 이름"})
    return C.DIRS["presets"] / f"{name}.yaml"


def list_presets() -> list[str]:
    names = sorted(p.stem for p in C.DIRS["presets"].glob("*.yaml"))
    if "default" in names:
        names.remove("default")
        names.insert(0, "default")
    return names


def read_preset(name: str) -> dict[str, Any]:
    path = _preset_path(name)
    if not path.exists():
        raise StorageError(f"프리셋 '{name}' 이 없습니다", status=404)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return normalize_preset(data, name)


def save_preset(name: str, data: dict[str, Any], create: bool = False) -> str | None:
    path = _preset_path(name)
    if create and path.exists():
        raise StorageError("같은 이름의 프리셋이 있습니다", {"name": "이미 존재"}, status=409)
    if not create and not path.exists():
        raise StorageError(f"프리셋 '{name}' 이 없습니다", status=404)
    p = normalize_preset(data, name)
    errors = validate_preset(p)
    if errors:
        raise StorageError("프리셋 값이 올바르지 않습니다", errors)
    return save_with_backup(path, dump_yaml(p), preset_history, name)


def clone_preset(src: str, new: str) -> None:
    data = read_preset(src)
    save_preset(new, data, create=True)


def rename_preset(old: str, new: str) -> None:
    if old == "default":
        raise StorageError("default 프리셋은 이름을 바꿀 수 없습니다")
    src, dst = _preset_path(old), _preset_path(new)
    if not src.exists():
        raise StorageError("프리셋이 없습니다", status=404)
    if dst.exists():
        raise StorageError("같은 이름의 프리셋이 있습니다", status=409)
    with _write_lock:
        data = read_preset(old)
        data["name"] = new
        atomic_write(dst, dump_yaml(data))
        src.unlink()
        preset_history.rename_key(old, new)
    cfg = C.get_config()
    if cfg["edit"]["default_preset"] == old:
        patch_config({"edit": {"default_preset": new}})


def delete_preset(name: str) -> None:
    if name == "default":
        raise StorageError("default 프리셋은 삭제할 수 없습니다")
    path = _preset_path(name)
    if not path.exists():
        raise StorageError("프리셋이 없습니다", status=404)
    with _write_lock:
        preset_history.backup(name, path)
        path.unlink()
    if C.get_config()["edit"]["default_preset"] == name:
        patch_config({"edit": {"default_preset": "default"}})


def restore_preset(name: str, version: str) -> None:
    if not version.startswith(name + "@"):
        raise StorageError("이 프리셋의 버전이 아닙니다")
    data = yaml.safe_load(preset_history.read(version)) or {}
    save_preset(name, data, create=not _preset_path(name).exists())


# ------------------------------------------------------------ 프롬프트
def _profile_dir(profile: str) -> Path:
    if not NAME_RE.match(profile or ""):
        raise StorageError("프로필 이름은 한글·영문·숫자·_- 1~40자")
    return C.DIRS["prompts"] / profile


def list_profiles() -> list[str]:
    d = C.DIRS["prompts"]
    return sorted(p.name for p in d.iterdir() if p.is_dir() and not p.name.startswith("."))


def read_profile(profile: str) -> dict[str, str]:
    d = _profile_dir(profile)
    if not d.is_dir():
        raise StorageError(f"프롬프트 프로필 '{profile}' 이 없습니다", status=404)
    return {n: (d / n).read_text(encoding="utf-8") if (d / n).exists() else "" for n in PROMPT_FILES}


def validate_prompt_file(name: str, text: str) -> dict[str, str]:
    if name != "schema.json":
        return {}
    try:
        schema = json.loads(text)
    except json.JSONDecodeError as e:
        return {"schema.json": f"JSON 문법 오류: {e.msg} (줄 {e.lineno}, 칸 {e.colno})"}
    try:
        import jsonschema
        jsonschema.Draft7Validator.check_schema(schema)
    except Exception as e:  # noqa: BLE001
        return {"schema.json": f"JSON 스키마 오류: {getattr(e, 'message', e)}"}
    if schema.get("type") != "object":
        return {"schema.json": "최상위 type은 object여야 합니다"}
    return {}


def save_prompt_file(profile: str, name: str, text: str) -> str | None:
    if name not in PROMPT_FILES:
        raise StorageError(f"알 수 없는 파일: {name}")
    d = _profile_dir(profile)
    if not d.is_dir():
        raise StorageError("프로필이 없습니다", status=404)
    errors = validate_prompt_file(name, text)
    if errors:
        raise StorageError("저장할 수 없습니다", errors)
    return save_with_backup(d / name, text, prompt_history(profile), name)


def clone_profile(src: str, new: str) -> None:
    s, d = _profile_dir(src), _profile_dir(new)
    if not s.is_dir():
        raise StorageError("원본 프로필이 없습니다", status=404)
    if d.exists():
        raise StorageError("같은 이름의 프로필이 있습니다", status=409)
    d.mkdir(parents=True)
    for n in PROMPT_FILES:
        if (s / n).exists():
            atomic_write(d / n, (s / n).read_text(encoding="utf-8"))


def restore_prompt(profile: str, version: str) -> None:
    fname = version.split("@", 1)[0]
    if fname not in PROMPT_FILES:
        raise StorageError("잘못된 버전")
    save_prompt_file(profile, fname, prompt_history(profile).read(version))


def profile_version(files: dict[str, str]) -> str:
    h = hashlib.sha256()
    for n in PROMPT_FILES:
        h.update(n.encode())
        h.update(b"\0")
        h.update(files.get(n, "").encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()[:8]


# ------------------------------------------------------------ 에셋
def save_asset(filename: str, data: bytes) -> str:
    safe = re.sub(r"[^0-9A-Za-z가-힣._\-]", "_", Path(filename).name)[:80] or "asset"
    target = C.DIRS["assets"] / safe
    if target.exists():
        target = C.DIRS["assets"] / f"{target.stem}_{_stamp()}{target.suffix}"
    atomic_write_bytes(target, data)
    return f"assets/{target.name}"
