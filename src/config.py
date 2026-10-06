"""전역 설정(config.yaml) 로드·검증·기본값.

파일의 mtime을 보고 바뀌었으면 다시 읽는다. 워커는 매 항목마다 get_config()를
호출하므로 UI에서 저장한 값이 재시작 없이 다음 항목부터 반영된다.
"""
from __future__ import annotations

import copy
import os
import secrets
import sys
import threading
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

# 설치본(PyInstaller)으로 실행 중인지
FROZEN = bool(getattr(sys, "frozen", False))
# 프로그램 파일 위치 (웹 UI 빌드, 확장프로그램 원본, 같이 넣은 ffmpeg). 설치본에서는 읽기 전용일 수 있다
APP_ROOT = Path(getattr(sys, "_MEIPASS", "")) if FROZEN else Path(__file__).resolve().parent.parent


def _data_root() -> Path:
    """설정·DB·영상 등 데이터 위치. 개발 환경은 프로젝트 폴더, 설치본은 사용자 폴더."""
    if os.environ.get("AUTOSET_HOME"):
        return Path(os.environ["AUTOSET_HOME"]).expanduser()
    if not FROZEN:
        return APP_ROOT
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "AutoSet"


ROOT = _data_root()
CONFIG_PATH = ROOT / "config.yaml"

DIRS = {
    "inbox": ROOT / "inbox",
    "work": ROOT / "work",
    "ready": ROOT / "ready",
    "done": ROOT / "done",
    "db": ROOT / "db",
    "logs": ROOT / "logs",
    "presets": ROOT / "presets" / "edit",
    "presets_history": ROOT / "presets" / ".history",
    "prompts": ROOT / "prompts",
    "prompts_history": ROOT / "prompts" / ".history",
    "config_history": ROOT / ".history",
    "assets": ROOT / "assets",
    "thumbs": ROOT / "thumbs",
    "secrets": ROOT / "secrets",
    "web_dist": APP_ROOT / "web" / "dist",
    # 설치본에서는 프로그램 안의 원본을 여기로 복사해 두고, 사용자가 이 폴더를 크롬에 로드한다
    "extension": ROOT / "chrome-extension",
}

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

DEFAULT_CONFIG: dict[str, Any] = {
    "timezone": "Asia/Seoul",
    "server": {"host": "0.0.0.0", "port": 8000},
    "ui": {"token": ""},
    "paths": {"ffmpeg": "", "ffprobe": "", "yt_dlp": ""},
    "history": {"keep": 50},
    "workers": {
        "poll_interval_sec": 3,
        "autostart": ["downloader", "editor", "captioner", "uploader"],
    },
    "download": {
        "cookies_file": "",           # 넷스케이프 형식 cookies.txt (로그인 필요한 릴스용)
        "timeout_sec": 600,
    },
    "edit": {
        "default_preset": "default",
        "encoder": "libx264",         # libx264 | h264_nvenc
        "crf": 20,
        "timeout_sec": 1800,
    },
    "ai": {
        "providers": ["ollama", "gemini"],
        "prompt_profile": "caption",
        "frame_count": 5,
        "silence_db": -45,
        "hold_low_confidence": True,
        "fixed_tags": [],
        "style": {"tone": "담백하고 친근한 말투", "banned_words": []},
        "stt": {"model_size": "small", "language": None, "device": "auto"},
        "ollama": {
            "host": "http://localhost:11434",
            "model": "gemma4:e4b",
            "temperature": 0.4,
            "num_ctx": 8192,
            "timeout_sec": 300,
        },
        "gemini": {"model": "gemini-3.8-flash", "daily_limit": 200, "timeout_sec": 120},
        "anthropic": {"model": "claude-sonnet-5-5", "daily_limit": 50, "timeout_sec": 120},
    },
    "upload": {
        "mode": "api",                # api | extension | selenium
        "daily_limit": 6,             # 기본 API 쿼터 10,000 / videos.insert 1,600
        "category_id": "22",
        "privacy": "public",          # public | unlisted | private
        "language": "ko",
        "made_for_kids": False,
        "credit_source": True,
        "client_secret_path": "secrets/client_secret.json",
        "extension": {
            "date_format": "YYYY. M. D.",
            "time_format": "A h:mm",
            "ampm": ["오전", "오후"],
            "stale_minutes": 30,
        },
        "selenium": {
            "profile_dir": ".browser/selenium",
            "accounts": [],           # 비어 있으면 기존 profile_dir을 기본 계정으로 사용
            "selected_account": "default",
            "chrome_binary": "",
            "version_main": 0,        # 0: 자동, 그 외 Chrome 주 버전
            "login_timeout_sec": 180,
            "step_timeout_sec": 60,
            "upload_timeout_sec": 1800,
            "date_format": "YYYY. M. D.",
            "time_format": "A h:mm",
            "ampm": ["오전", "오후"],
        },
    },
    "schedule": {
        "slots": {d: ["09:00", "18:00"] for d in WEEKDAYS},
        "min_lead_minutes": 20,       # 지금부터 이 시간 이내의 슬롯은 배정하지 않음
        "presets": [
            {"label": "즉시", "type": "immediate"},
            {"label": "다음 예약 슬롯", "type": "next_slot"},
            {"label": "1시간 후", "type": "offset", "minutes": 60},
            {"label": "3시간 후", "type": "offset", "minutes": 180},
            {"label": "오늘 18:00", "type": "at", "day_offset": 0, "time": "18:00"},
            {"label": "내일 09:00", "type": "at", "day_offset": 1, "time": "09:00"},
        ],
    },
    "storage": {"done_keep_days": 30},
}


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


_lock = threading.Lock()
_cache: dict[str, Any] = {"mtime": None, "data": None}


def get_config() -> dict[str, Any]:
    """현재 config.yaml (기본값과 병합). 파일이 바뀌면 다시 읽는다."""
    with _lock:
        try:
            mtime = CONFIG_PATH.stat().st_mtime_ns
        except FileNotFoundError:
            mtime = None
        if _cache["data"] is None or mtime != _cache["mtime"]:
            raw = {}
            if mtime is not None:
                raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
            _cache["data"] = deep_merge(DEFAULT_CONFIG, raw)
            _cache["mtime"] = mtime
        return copy.deepcopy(_cache["data"])


def invalidate() -> None:
    with _lock:
        _cache["data"] = None


def tz() -> ZoneInfo:
    try:
        return ZoneInfo(get_config().get("timezone") or "Asia/Seoul")
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Seoul")


def root_path(p: str | None) -> Path | None:
    """config에 적힌 경로를 절대 경로로 (상대 경로는 프로젝트 루트 기준)."""
    if not p:
        return None
    path = Path(p)
    return path if path.is_absolute() else ROOT / path


# ---------------------------------------------------------------- 검증
def validate_config(cfg: dict[str, Any]) -> dict[str, str]:
    """필드 경로 → 오류 메시지. 비어 있으면 통과."""
    err: dict[str, str] = {}

    def num(path: str, lo: float | None = None, hi: float | None = None, integer=False):
        cur: Any = cfg
        for key in path.split("."):
            if not isinstance(cur, dict) or key not in cur:
                return
            cur = cur[key]
        if isinstance(cur, bool) or not isinstance(cur, (int, float)):
            err[path] = "숫자여야 합니다"
            return
        if integer and int(cur) != cur:
            err[path] = "정수여야 합니다"
        if lo is not None and cur < lo:
            err[path] = f"{lo} 이상이어야 합니다"
        if hi is not None and cur > hi:
            err[path] = f"{hi} 이하여야 합니다"

    try:
        ZoneInfo(cfg.get("timezone", "Asia/Seoul"))
    except Exception:
        err["timezone"] = "알 수 없는 시간대입니다 (예: Asia/Seoul)"
    num("server.port", 1, 65535, True)
    num("history.keep", 1, 1000, True)
    num("workers.poll_interval_sec", 1, 600)
    num("ai.frame_count", 1, 12, True)
    num("ai.silence_db", -100, 0)
    num("ai.ollama.temperature", 0, 2)
    num("ai.ollama.num_ctx", 1024, 262144, True)
    num("ai.ollama.timeout_sec", 10, 3600)
    num("ai.gemini.daily_limit", 0, 100000, True)
    num("ai.anthropic.daily_limit", 0, 100000, True)
    num("upload.daily_limit", 0, 1000, True)
    num("upload.selenium.version_main", 0, 1000, True)
    num("upload.selenium.login_timeout_sec", 30, 3600)
    num("upload.selenium.step_timeout_sec", 10, 600)
    num("upload.selenium.upload_timeout_sec", 60, 14400)
    num("schedule.min_lead_minutes", 0, 1440)
    num("storage.done_keep_days", 1, 3650, True)

    ai = cfg.get("ai", {})
    provs = ai.get("providers", [])
    if not isinstance(provs, list) or not provs:
        err["ai.providers"] = "프로바이더를 하나 이상 지정하세요"
    else:
        bad = [p for p in provs if p not in ("ollama", "gemini", "anthropic")]
        if bad:
            err["ai.providers"] = f"알 수 없는 프로바이더: {', '.join(bad)}"
    if ai.get("stt", {}).get("model_size") not in (
        "tiny", "base", "small", "medium", "large-v2", "large-v3", "large-v3-turbo", "turbo"
    ):
        err["ai.stt.model_size"] = "tiny/base/small/medium/large-v3/turbo 중 하나"

    up = cfg.get("upload", {})
    if up.get("mode") not in ("api", "extension", "selenium"):
        err["upload.mode"] = "api / extension / selenium"
    browser = up.get("selenium", {})
    if not isinstance(browser, dict):
        err["upload.selenium"] = "설정 객체여야 합니다"
    else:
        for key in ("profile_dir", "chrome_binary", "date_format", "time_format"):
            value = browser.get(key)
            if value is not None and (not isinstance(value, str) or (key != "chrome_binary" and not value.strip())):
                err[f"upload.selenium.{key}"] = "문자열 경로 또는 형식을 입력하세요"
        ampm = browser.get("ampm")
        if ampm is not None and (not isinstance(ampm, list) or len(ampm) != 2
                                 or not all(isinstance(v, str) and v for v in ampm)):
            err["upload.selenium.ampm"] = "오전/오후 문자열 두 개를 지정하세요"
        accounts = browser.get("accounts", [])
        if not isinstance(accounts, list):
            err["upload.selenium.accounts"] = "계정 목록이어야 합니다"
        else:
            ids, profiles = set(), set()
            for i, account in enumerate(accounts):
                prefix = f"upload.selenium.accounts.{i}"
                if not isinstance(account, dict):
                    err[prefix] = "계정 객체여야 합니다"
                    continue
                account_id = account.get("id")
                if not isinstance(account_id, str) or not account_id or len(account_id) > 80 \
                        or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in account_id):
                    err[f"{prefix}.id"] = "계정 ID는 영문·숫자·_·-로 지정하세요 (80자 이내)"
                elif account_id in ids:
                    err[f"{prefix}.id"] = "계정 ID가 중복되었습니다"
                else:
                    ids.add(account_id)
                if not isinstance(account.get("name"), str) or not account["name"].strip():
                    err[f"{prefix}.name"] = "계정 이름을 입력하세요"
                profile = account.get("profile_dir")
                if not isinstance(profile, str) or not profile.strip():
                    err[f"{prefix}.profile_dir"] = "전용 프로필 폴더를 입력하세요"
                else:
                    try:
                        resolved = str(root_path(profile).resolve()).casefold()
                        if resolved in profiles:
                            err[f"{prefix}.profile_dir"] = "계정마다 서로 다른 프로필 폴더를 지정하세요"
                        profiles.add(resolved)
                    except (OSError, ValueError):
                        err[f"{prefix}.profile_dir"] = "올바른 프로필 폴더 경로를 입력하세요"
            if browser.get("selected_account", "default") not in (ids if accounts else {"default"}):
                err["upload.selenium.selected_account"] = "업로드에 사용할 계정을 선택하세요"
    if up.get("privacy") not in ("public", "unlisted", "private"):
        err["upload.privacy"] = "public / unlisted / private"

    slots = cfg.get("schedule", {}).get("slots", {})
    if not isinstance(slots, dict):
        err["schedule.slots"] = "요일별 시각 목록이어야 합니다"
    else:
        for day, times in slots.items():
            if day not in WEEKDAYS:
                err[f"schedule.slots.{day}"] = "요일 키는 mon~sun"
                continue
            for t in times or []:
                if not _valid_hhmm(t):
                    err[f"schedule.slots.{day}"] = f"잘못된 시각: {t} (HH:MM)"
    for i, p in enumerate(cfg.get("schedule", {}).get("presets", []) or []):
        t = p.get("type")
        if t not in ("immediate", "next_slot", "offset", "at"):
            err[f"schedule.presets.{i}"] = "type은 immediate/next_slot/offset/at"
        elif t == "at" and not _valid_hhmm(p.get("time", "")):
            err[f"schedule.presets.{i}"] = "time은 HH:MM"
        elif t == "offset" and not isinstance(p.get("minutes"), (int, float)):
            err[f"schedule.presets.{i}"] = "minutes가 필요합니다"
    return err


def _valid_hhmm(t: Any) -> bool:
    if not isinstance(t, str) or len(t.split(":")) != 2:
        return False
    h, m = t.split(":")
    return h.isdigit() and m.isdigit() and 0 <= int(h) < 24 and 0 <= int(m) < 60


def ensure_token(cfg_raw: dict) -> bool:
    """ui.token이 없으면 생성. 생성했으면 True."""
    ui = cfg_raw.setdefault("ui", {})
    if not ui.get("token"):
        ui["token"] = secrets.token_urlsafe(18)
        return True
    return False
