"""서버 뼈대 스모크 테스트 (임시 루트에서 실행).

    .venv\\Scripts\\python -m pytest tests -q
"""
from __future__ import annotations

import importlib
import shutil
import sys
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("root")
    sys.path.insert(0, str(ROOT))
    import src.config as C
    # 프로젝트 파일을 건드리지 않도록 경로를 임시 폴더로 돌린다
    C.ROOT = tmp
    C.CONFIG_PATH = tmp / "config.yaml"
    for k, v in list(C.DIRS.items()):
        C.DIRS[k] = tmp / v.relative_to(ROOT)
    C.DIRS["extension"] = tmp / "chrome-extension"
    (tmp / "chrome-extension" / "upload").mkdir(parents=True)
    shutil.copy(ROOT / "chrome-extension" / "upload" / "selectors.json", tmp / "chrome-extension/upload/")
    C.invalidate()
    import src.storage as S
    importlib.reload(S)
    import src.models as M
    M.DB_PATH = C.DIRS["db"] / "pipeline.sqlite"
    from fastapi.testclient import TestClient
    import src.main as main
    main.bootstrap()
    cfg = S.read_config_raw()
    cfg["workers"] = {"autostart": []}
    S.save_config(cfg)
    from src.api import app
    with TestClient(app) as c:
        c.headers["X-Token"] = C.get_config()["ui"]["token"]
        yield c


def test_auth_required(client):
    r = client.get("/system/status", headers={"X-Token": "wrong"})
    assert r.status_code == 401


def test_status(client):
    r = client.get("/system/status")
    assert r.status_code == 200, r.text
    assert {w["name"] for w in r.json()["workers"]} == {"downloader", "editor", "captioner", "uploader"}


def test_ui_html_not_cached(client):
    assert client.get("/ui/").headers["cache-control"] == "no-store"


def test_feed_and_fetch_schedule(client):
    r = client.post("/feed", json={"url": "https://www.instagram.com/reels/ABC123/", "author": "@tester",
                                   "caption": "hello"})
    assert r.status_code == 200, r.text
    fid = r.json()["results"][0]["id"]
    # 중복 제거
    r2 = client.post("/feed", json={"url": "https://www.instagram.com/reel/ABC123/?x=1"})
    assert r2.json()["results"][0] == {"id": fid, "created": False}
    r = client.post(f"/feed/{fid}/fetch", json={"use_next_slot": True})
    assert r.status_code == 200, r.text
    item = r.json()["feed"]["item"]
    assert item["publish_mode"] == "scheduled" and item["slot_assigned"]
    assert [s["state"] for s in item["stage_progress"]] == ["pending"] * 4
    # 두 번째 항목은 다른 슬롯
    client.post("/feed", json={"url": "https://www.instagram.com/reel/DEF456/"})
    client.post("/feed", json={"url": "https://www.instagram.com/reel/GHI789/"})
    feed = client.get("/feed/items").json()["items"]
    ids = [f["id"] for f in feed if f["status"] == "seen"]
    r = client.post("/feed/bulk", json={"ids": ids, "sequential_slots": True})
    slots = [x["publish_at"] for x in r.json()["results"]]
    assert len(set(slots + [item["publish_at"]])) == 3
    # 즉시로 전환
    r = client.patch(f"/feed/{fid}/schedule", json={"publish_at": None})
    assert r.json()["item"]["publish_mode"] == "immediate"
    # 과거 시각 거부
    r = client.patch(f"/feed/{fid}/schedule", json={"publish_at": "2000-01-01T00:00:00"})
    assert r.status_code == 400


def test_preset_crud_and_history(client):
    r = client.get("/presets/edit/default")
    p = r.json()["preset"]
    p["video"]["speed"] = 3.0
    r = client.put("/presets/edit/default", json={"preset": p})
    assert r.status_code == 400 and "video.speed" in r.json()["fields"]
    p["video"]["speed"] = 1.1
    assert client.put("/presets/edit/default", json={"preset": p}).status_code == 200
    hist = client.get("/presets/edit/default/history").json()["versions"]
    assert len(hist) == 1
    assert client.post("/presets/edit/default/clone", json={"name": "빠르게"}).status_code == 200
    assert client.delete("/presets/edit/default").status_code == 400
    assert client.post(f"/presets/edit/default/restore/{hist[0]['version']}").json()["preset"]["video"]["speed"] == 1.0


def test_prompt_schema_validation(client):
    r = client.put("/prompts/caption/schema.json", json={"content": "{bad"})
    assert r.status_code == 400 and "schema.json" in r.json()["fields"]
    r = client.get("/prompts/caption")
    assert len(r.json()["version"]) == 8


def test_config_validation(client):
    cfg = client.get("/config").json()["config"]
    cfg["upload"]["mode"] = "nope"
    r = client.put("/config", json={"config": cfg})
    assert r.status_code == 400 and "upload.mode" in r.json()["fields"]


def test_d_upload_config_roundtrip(client):
    original = client.get("/config").json()["config"]
    import copy
    cfg = copy.deepcopy(original)
    cfg["upload"]["mode"] = "selenium"
    cfg["upload"]["selenium"]["login_timeout_sec"] = 240
    try:
        r = client.put("/config", json={"config": cfg})
        assert r.status_code == 200, r.text
        stored = client.get("/config").json()["config"]["upload"]
        assert stored["mode"] == "selenium"
        assert stored["selenium"]["login_timeout_sec"] == 240
        assert client.get("/upload/status").json()["mode"] == "selenium"
        assert client.get("/upload/next").status_code == 409
    finally:
        assert client.put("/config", json={"config": original}).status_code == 200


def test_d_account_login_api(client, monkeypatch):
    import copy
    from src import selenium_accounts as SA
    from unittest.mock import Mock
    original = client.get("/config").json()["config"]
    cfg = copy.deepcopy(original)
    cfg["upload"]["mode"] = "selenium"
    cfg["upload"]["selenium"].update(
        selected_account="second", accounts=[
            {"id": "default", "name": "기본 계정", "profile_dir": ".browser/selenium"},
            {"id": "second", "name": "두 번째 계정", "profile_dir": ".browser/second"},
        ])
    session = Mock()
    session.status.return_value = {"active": False, "state": "idle"}
    session.start.return_value = {"active": True, "state": "starting", "account_id": "second"}
    session.finish.return_value = {"active": True, "state": "ready"}
    session.cancel.return_value = {"active": False, "state": "cancelled"}
    monkeypatch.setattr(SA, "LOGIN", session)
    try:
        assert client.put("/config", json={"config": cfg}).status_code == 200
        status = client.get("/upload/selenium/status").json()
        assert status["selected_account"] == "second"
        assert [a["id"] for a in status["accounts"]] == ["default", "second"]
        r = client.post("/upload/selenium/login", json={"account_id": "second"})
        assert r.status_code == 200 and r.json()["account_id"] == "second"
        assert session.start.call_args.args[1] == "second"
        assert client.post("/upload/selenium/login/finish").status_code == 200
        assert client.post("/upload/selenium/login/cancel").status_code == 200
        session.start.side_effect = SA.BrowserBusy("업로드 중")
        assert client.post("/upload/selenium/login", json={"account_id": "second"}).status_code == 409
        session.start.side_effect = ValueError("계정 없음")
        assert client.post("/upload/selenium/login", json={"account_id": "absent"}).status_code == 400
    finally:
        assert client.put("/config", json={"config": original}).status_code == 200


def test_prompt_loader_vars():
    from src.ai import prompt_loader as P
    v = P.build_vars(duration_sec=12.34, frame_count=5, has_speech=True, transcript="가" * 3000)
    assert v["duration_sec"] == "12.3" and "(중략)" in v["transcript"]
    unknown: list[str] = []
    assert P.render("{{tone}} {{nope}}", v, unknown).endswith("{{nope}}") and unknown == ["nope"]


def test_stt_remap():
    from src.ai.stt import remap_segments
    segs = [{"start": 0, "end": 1, "text": "a"}, {"start": 1.5, "end": 4, "text": "b"}, {"start": 5, "end": 7, "text": "c"}]
    out = remap_segments(segs, {"trim_start_sec": 2, "speed": 2, "intro_duration": 1, "main_duration": 10})
    assert out == [{"start": 1.0, "end": 2.0, "text": "b"}, {"start": 2.5, "end": 3.5, "text": "c"}]


def test_generate_validation():
    from src.ai.generate import classify_errors, postprocess, truncate_to_schema
    import json
    schema = json.loads((ROOT / "src" / "defaults.py").read_text(encoding="utf-8").split('SCHEMA_JSON = """')[1]
                        .split('"""')[0])
    data = {"caption": "가" * 40, "summary": "s", "title": "t", "description": "d",
            "tags": ["#a", "b", "c", "d", "e"], "confidence": "high"}
    length, other = classify_errors(data, schema)
    assert length and not other
    fixed = truncate_to_schema(data, schema)
    assert len(fixed["caption"]) == 34
    assert postprocess(fixed)["tags"][0] == "a"
    _ = timedelta


def test_files_routes_not_shadowed(client):
    """/files/{area}/{item_id} 가 thumb·preview·assets 경로를 가로채지 않아야 한다."""
    import src.config as C
    from src import thumbs
    from PIL import Image
    thumbs.path(999).parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 60)).save(thumbs.path(999), "JPEG")
    r = client.get("/files/thumb/999")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    (C.DIRS["work"] / "preview").mkdir(parents=True, exist_ok=True)
    (C.DIRS["work"] / "preview" / "x_1.mp4").write_bytes(b"0")
    assert client.get("/files/preview/x_1.mp4").status_code == 200
    (C.DIRS["assets"] / "a.png").write_bytes(b"0")
    assert client.get("/files/assets/a.png").status_code == 200


def _tiny_mp4(path):
    import subprocess
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=360x640:rate=30:duration=2", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True)


def test_download_only_and_manual_flow(client, tmp_path):
    from src import models as M
    # 1) 자동 업로드 끄고 가져오기 → auto_upload=0, 게시 시각은 무시
    fid = client.post("/feed", json={"url": "https://www.instagram.com/reel/HOLD0001/"}).json()["results"][0]["id"]
    r = client.post(f"/feed/{fid}/fetch", json={"auto_upload": False, "use_next_slot": True})
    item = r.json()["feed"]["item"]
    assert r.status_code == 200 and item["auto_upload"] is False and item["publish_mode"] == "immediate"
    iid = item["id"]
    # 다운로드가 끝났다고 가정 → held
    M.update_item(iid, status="held")
    held = client.get("/manual/items").json()["held"]
    assert [h["id"] for h in held] == [iid]
    assert [s["state"] for s in held[0]["stage_progress"]][:2] == ["done", "held"]

    # 2) 편집본 올리기 → downloaded, source_kind=manual, edit_video=False
    mp4 = tmp_path / "edited.mp4"
    _tiny_mp4(mp4)
    with open(mp4, "rb") as f:
        r = client.post(f"/items/{iid}/manual-video", files={"file": ("my edit.mp4", f, "video/mp4")},
                        data={"edit_preset": "", "edit_video": "false", "publish_at": "", "use_next_slot": "false"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "downloaded" and d["source_kind"] == "manual" and d["edit_video"] is False
    assert d["source_meta"]["filename"] == "my edit.mp4"

    # 3) 영상이 아닌 파일은 거절
    bad = tmp_path / "x.txt"
    bad.write_text("x")
    with open(bad, "rb") as f:
        assert client.post("/manual/upload", files={"file": ("x.txt", f, "text/plain")}).status_code == 400

    # 4) 새 영상 올리기 → 피드 목록에는 안 보이고, 삭제하면 내부 피드 행도 사라짐
    with open(mp4, "rb") as f:
        r = client.post("/manual/upload", files={"file": ("new.mp4", f, "video/mp4")}, data={"edit_video": "false"})
    assert r.status_code == 200, r.text
    new_id, new_fid = r.json()["id"], r.json()["feed_item_id"]
    feed_urls = [x["url"] for x in client.get("/feed/items?status=&limit=300").json()["items"]]
    assert not any(u.startswith("manual://") for u in feed_urls)
    assert client.delete(f"/items/{new_id}").status_code == 200
    assert M.get_feed(new_fid) is None

    # 5) 보류 항목 편집 없이 자동 처리
    fid2 = client.post("/feed", json={"url": "https://www.instagram.com/reel/HOLD0002/"}).json()["results"][0]["id"]
    iid2 = client.post(f"/feed/{fid2}/fetch", json={"auto_upload": False}).json()["feed"]["item"]["id"]
    M.update_item(iid2, status="held")
    r = client.post(f"/items/{iid2}/continue", json={})
    assert r.status_code == 200 and r.json()["status"] == "downloaded" and r.json()["auto_upload"] is True


def test_neutral_video():
    from src.workers.editor import neutral_video
    v = neutral_video()
    assert v["speed"] == 1.0 and v["trim_start_sec"] == 0 and not v["hflip"] and v["intro_path"] is None
