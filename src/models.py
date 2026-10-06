"""SQLite 스키마와 공통 DB 헬퍼."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterator

from . import config as C
from . import events
from .timeutil import now_iso

DB_PATH = C.DIRS["db"] / "pipeline.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS feed_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    author TEXT,
    caption TEXT,
    thumbnail_url TEXT,
    seen_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'seen',
    fetched_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_feed_status ON feed_items(status);
CREATE INDEX IF NOT EXISTS ix_feed_seen ON feed_items(seen_at);

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_item_id INTEGER REFERENCES feed_items(id),
    source_path TEXT, edited_path TEXT, final_path TEXT,
    source_meta TEXT, source_duration REAL, edited_duration REAL,
    edit_preset TEXT, edit_params TEXT,
    caption TEXT, summary TEXT, title TEXT, description TEXT, tags TEXT,
    transcript TEXT, has_speech INTEGER,
    ai_confidence TEXT, ai_provider_used TEXT,
    prompt_profile TEXT, prompt_version TEXT,
    prompt_profile_override TEXT, extra_instruction TEXT,
    needs_review INTEGER NOT NULL DEFAULT 0,
    video_id TEXT,
    publish_mode TEXT NOT NULL DEFAULT 'immediate',
    publish_at TEXT,
    slot_assigned INTEGER NOT NULL DEFAULT 0,
    upload_mode TEXT,
    upload_account_id TEXT,
    status TEXT NOT NULL,
    stage TEXT, stage_detail TEXT,
    progress_pct REAL NOT NULL DEFAULT 0,
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    uploaded_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_items_status ON items(status);
CREATE INDEX IF NOT EXISTS ix_items_publish ON items(publish_at);
CREATE INDEX IF NOT EXISTS ix_items_feed ON items(feed_item_id);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER,
    worker TEXT,
    stage TEXT,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_item ON events(item_id);
CREATE INDEX IF NOT EXISTS ix_events_worker ON events(worker, id);

CREATE TABLE IF NOT EXISTS ai_usage (
    date TEXT NOT NULL,
    provider TEXT NOT NULL,
    calls INTEGER NOT NULL DEFAULT 0,
    failures INTEGER NOT NULL DEFAULT 0,
    fallbacks INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (date, provider)
);

CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

JSON_FIELDS = ("edit_params", "tags", "source_meta")

STATUS_FLOW = [
    "queued", "downloading", "downloaded", "editing", "edited",
    "captioning", "captioned_review", "captioned", "uploading", "uploaded",
]
# 실패 → 재시도 시 돌아갈 상태
RETRY_TO = {
    "failed_download": "queued",
    "failed_edit": "downloaded",
    "failed_caption": "edited",
    "failed_upload": "captioned",
}
# 서버가 비정상 종료됐을 때 진행 중이던 상태를 되돌릴 곳
RESUME_TO = {
    "downloading": "queued",
    "editing": "downloaded",
    "captioning": "edited",
    "uploading": "captioned",
}

_local = threading.local()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA busy_timeout=30000")
    return c


def db() -> sqlite3.Connection:
    """스레드별 연결 (autocommit)."""
    c = getattr(_local, "conn", None)
    if c is None:
        c = _connect()
        _local.conn = c
    return c


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    c = db()
    c.execute("BEGIN IMMEDIATE")
    try:
        yield c
        c.execute("COMMIT")
    except BaseException:
        c.execute("ROLLBACK")
        raise


MIGRATIONS = [
    ("feed_items", "thumb_attempts", "INTEGER NOT NULL DEFAULT 0"),
    ("items", "upload_account_id", "TEXT"),
    # 피드 가져오기 때 "자동 업로드"를 끄면 0: 다운로드 후 held(보류)에서 멈춘다
    ("items", "auto_upload", "INTEGER NOT NULL DEFAULT 1"),
    # 0이면 편집 프리셋의 영상 설정(자르기·속도 등)을 쓰지 않고 규격(1080x1920)만 맞춘다
    ("items", "edit_video", "INTEGER NOT NULL DEFAULT 1"),
    # reel: 피드에서 받은 원본 / manual: 사용자가 직접 올린 영상
    ("items", "source_kind", "TEXT NOT NULL DEFAULT 'reel'"),
]


def init_db() -> None:
    c = db()
    c.executescript(SCHEMA)
    for table, col, decl in MIGRATIONS:
        cols = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def recover_interrupted() -> int:
    """진행 중 상태로 남은 항목을 이전 단계로 되돌린다 (서버 시작 시).
    확장프로그램 업로드는 브라우저가 계속 처리 중일 수 있어 건드리지 않는다."""
    n = 0
    for st, back in RESUME_TO.items():
        if st == "uploading":
            # 최종 게시 클릭 이후 종료됐다면 재업로드하지 않고 확인을 기다린다.
            db().execute(
                "UPDATE items SET status='failed_upload', error=?, updated_at=? "
                "WHERE status='uploading' AND upload_mode='selenium' AND stage_detail='게시 결과 확인 중'",
                ("D안 게시 결과가 불확실합니다. YouTube 스튜디오에서 확인한 뒤 재시도하세요.", now_iso()),
            )
        extra = " AND COALESCE(upload_mode,'api') IN ('api','selenium')" if st == "uploading" else ""
        cur = db().execute(
            f"UPDATE items SET status=?, progress_pct=0, stage_detail=NULL, updated_at=? WHERE status=?{extra}",
            (back, now_iso(), st),
        )
        n += cur.rowcount
    return n


# ---------------------------------------------------------------- rows
def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    for k in JSON_FIELDS:
        if k in d and isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except json.JSONDecodeError:
                pass
    for k in ("needs_review", "slot_assigned", "auto_upload", "edit_video"):
        if k in d:
            d[k] = bool(d[k])
    if "has_speech" in d and d["has_speech"] is not None:
        d["has_speech"] = bool(d["has_speech"])
    return d


def _encode(fields: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in fields.items():
        if k in JSON_FIELDS and v is not None and not isinstance(v, str):
            v = json.dumps(v, ensure_ascii=False)
        elif isinstance(v, bool):
            v = int(v)
        out[k] = v
    return out


def get_item(item_id: int) -> dict[str, Any] | None:
    return row_to_dict(db().execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone())


def get_feed(feed_id: int) -> dict[str, Any] | None:
    return row_to_dict(db().execute("SELECT * FROM feed_items WHERE id=?", (feed_id,)).fetchone())


def insert_item(fields: dict[str, Any]) -> int:
    fields = _encode({**fields, "created_at": now_iso(), "updated_at": now_iso()})
    cols = ",".join(fields)
    q = ",".join("?" for _ in fields)
    cur = db().execute(f"INSERT INTO items ({cols}) VALUES ({q})", tuple(fields.values()))
    item_id = cur.lastrowid
    events.emit("item", item_public(get_item(item_id)))
    return item_id


def update_item(item_id: int, _emit: bool = True, **fields: Any) -> dict[str, Any] | None:
    if not fields:
        return get_item(item_id)
    fields = _encode({**fields, "updated_at": now_iso()})
    sets = ",".join(f"{k}=?" for k in fields)
    db().execute(f"UPDATE items SET {sets} WHERE id=?", (*fields.values(), item_id))
    item = get_item(item_id)
    if _emit and item:
        events.emit("item", item_public(item))
    return item


def set_progress(item_id: int, pct: float, stage_detail: str | None = None) -> None:
    pct = max(0.0, min(100.0, round(pct, 1)))
    fields: dict[str, Any] = {"progress_pct": pct, "updated_at": now_iso()}
    if stage_detail is not None:
        fields["stage_detail"] = stage_detail
    sets = ",".join(f"{k}=?" for k in fields)
    db().execute(f"UPDATE items SET {sets} WHERE id=?", (*fields.values(), item_id))
    if events.should_emit_progress(item_id, pct) or stage_detail is not None:
        item = get_item(item_id)
        if item:
            events.emit("item", item_public(item))


def claim_item(from_status: str, to_status: str, stage: str, where: str = "", params: tuple = (),
               order: str = "id") -> dict[str, Any] | None:
    """from_status 인 항목 하나를 원자적으로 to_status 로 바꾸고 반환."""
    with tx() as c:
        row = c.execute(
            f"SELECT id FROM items WHERE status=? {where} ORDER BY {order} LIMIT 1",
            (from_status, *params),
        ).fetchone()
        if not row:
            return None
        c.execute(
            "UPDATE items SET status=?, stage=?, stage_detail=NULL, progress_pct=0, error=NULL, "
            "updated_at=? WHERE id=?",
            (to_status, stage, now_iso(), row["id"]),
        )
    item = get_item(row["id"])
    events.emit("item", item_public(item))
    return item


# ---------------------------------------------------------------- events / log
def add_event(item_id: int | None, worker: str | None, stage: str | None, level: str, message: str) -> None:
    ts = now_iso()
    cur = db().execute(
        "INSERT INTO events (item_id, worker, stage, level, message, created_at) VALUES (?,?,?,?,?,?)",
        (item_id, worker, stage, level, message, ts),
    )
    events.emit("log", {"id": cur.lastrowid, "item_id": item_id, "worker": worker, "stage": stage,
                        "level": level, "message": message, "created_at": ts})


def item_events(item_id: int, limit: int = 200) -> list[dict[str, Any]]:
    rows = db().execute("SELECT * FROM events WHERE item_id=? ORDER BY id DESC LIMIT ?", (item_id, limit))
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- ai usage / kv
def usage_incr(provider: str, calls: int = 0, failures: int = 0, fallbacks: int = 0, day: str | None = None) -> None:
    from .timeutil import local_today
    day = day or local_today()
    db().execute(
        "INSERT INTO ai_usage (date, provider, calls, failures, fallbacks) VALUES (?,?,?,?,?) "
        "ON CONFLICT(date, provider) DO UPDATE SET calls=calls+excluded.calls, "
        "failures=failures+excluded.failures, fallbacks=fallbacks+excluded.fallbacks",
        (day, provider, calls, failures, fallbacks),
    )


def usage_on(day: str) -> dict[str, dict[str, int]]:
    rows = db().execute("SELECT * FROM ai_usage WHERE date=?", (day,))
    return {r["provider"]: {"calls": r["calls"], "failures": r["failures"], "fallbacks": r["fallbacks"]}
            for r in rows}


def kv_get(key: str, default: Any = None) -> Any:
    row = db().execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except (TypeError, json.JSONDecodeError):
        return row["value"]


def kv_set(key: str, value: Any) -> None:
    db().execute(
        "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value, ensure_ascii=False, default=str)),
    )


# ---------------------------------------------------------------- 진행 상태 표현
STAGES = ["download", "edit", "caption", "upload"]
_STATUS_POS = {
    "queued": (0, "pending"), "downloading": (0, "running"),
    "downloaded": (1, "pending"), "held": (1, "held"), "editing": (1, "running"),
    "edited": (2, "pending"), "captioning": (2, "running"),
    "captioned_review": (2, "review"),
    "captioned": (3, "pending"), "uploading": (3, "running"),
    "uploaded": (4, "done"),
}
_FAILED_POS = {"failed_download": 0, "failed_edit": 1, "failed_caption": 2, "failed_upload": 3}
_STAGE_OF = {"download": 0, "edit": 1, "caption": 2, "upload": 3}


def stage_progress(item: dict[str, Any]) -> list[dict[str, Any]]:
    status = item["status"]
    pct = item.get("progress_pct") or 0
    if status in _FAILED_POS:
        pos, state = _FAILED_POS[status], "failed"
    elif status == "skipped":
        pos, state = _STAGE_OF.get(item.get("stage") or "download", 0), "skipped"
    else:
        pos, state = _STATUS_POS.get(status, (0, "pending"))
    out = []
    for i, name in enumerate(STAGES):
        if i < pos:
            s = {"stage": name, "state": "done", "pct": 100}
        elif i == pos:
            s = {"stage": name, "state": state,
                 "pct": pct if state in ("running", "failed") else (100 if state == "review" else 0)}
            if state == "failed":
                s["error"] = (item.get("error") or "").splitlines()[0][:200] if item.get("error") else None
            if item.get("stage_detail") and state in ("running", "failed"):
                s["detail"] = item["stage_detail"]
        else:
            s = {"stage": name, "state": "pending", "pct": 0}
        out.append(s)
    return out


def item_public(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if item is None:
        return None
    d = dict(item)
    d["stage_progress"] = stage_progress(item)
    return d
