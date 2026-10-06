"""D안 계정 분리, 로그인 창 재시작, 채널 확인, 로그인 실패 복구."""
from __future__ import annotations

import copy
import ctypes
import sqlite3
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from selenium.common.exceptions import InvalidSessionIdException

from src import config as C
from src import models as M
from src import selenium_accounts as SA
from src import selenium_upload as SU
from src.workers.uploader import Uploader


@pytest.fixture
def browser(monkeypatch):
    monkeypatch.setitem(C.DIRS, "extension", Path(__file__).resolve().parents[1] / "chrome-extension")
    cfg = copy.deepcopy(C.DEFAULT_CONFIG["upload"]["selenium"])
    cfg["accounts"] = [
        {"id": "one", "name": "계정 1", "profile_dir": ".browser/one"},
        {"id": "two", "name": "계정 2", "profile_dir": ".browser/two"},
    ]
    cfg["selected_account"] = "two"
    return cfg


def test_selected_accounts_keep_separate_profiles(browser):
    assert SA.account(browser)["id"] == "two"
    one = SA.account(browser, "one")
    two = SA.account(browser, "two")
    assert SA.profile_key(one) != SA.profile_key(two)
    assert SA.profile_key({**one, "name": "새 이름"}) == SA.profile_key(one)
    assert SA.profile_key({**one, "profile_dir": ".browser/new"}) != SA.profile_key(one)
    with pytest.raises(ValueError):
        SA.account(browser, "absent")


def test_legacy_profile_is_preserved():
    cfg = copy.deepcopy(C.DEFAULT_CONFIG["upload"]["selenium"])
    cfg["profile_dir"] = ".browser/custom-old"
    assert SA.account(cfg)["profile_dir"] == ".browser/custom-old"


def test_browser_launch_shows_window_once(monkeypatch, browser, tmp_path):
    uc = Mock()
    monkeypatch.setitem(sys.modules, "undetected_chromedriver", uc)
    monkeypatch.setattr(C, "root_path", lambda path: tmp_path)
    show = Mock()
    monkeypatch.setattr(SA, "show_browser_window", show)

    driver = SA.create_driver(browser, SA.account(browser))

    assert driver is uc.Chrome.return_value
    assert uc.Chrome.call_args.kwargs["headless"] is False
    uc.ChromeOptions.return_value.add_argument.assert_any_call("--start-maximized")
    show.assert_called_once_with(driver)


def test_window_display_failure_does_not_stop_upload(monkeypatch):
    driver = Mock(browser_pid=321)
    driver.maximize_window.side_effect = RuntimeError("maximize failed")
    driver.execute_cdp_cmd.side_effect = RuntimeError("CDP failed")
    foreground = Mock(side_effect=OSError("no desktop"))
    monkeypatch.setattr(SA, "_foreground_browser_window", foreground)
    monkeypatch.setattr(SA.os, "name", "nt")

    SA.show_browser_window(driver)

    driver.maximize_window.assert_called_once()
    driver.execute_cdp_cmd.assert_called_once_with("Page.bringToFront", {})
    foreground.assert_called_once_with(321)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows window handles")
def test_foreground_restores_only_own_chrome_and_releases_topmost(monkeypatch):
    from ctypes import wintypes

    user32 = Mock()
    # 다른 Chrome과 같은 프로세스의 보조 창은 건드리지 않는다. 64비트 HWND도 유지한다.
    handles = [0x100000001, 0x100000002, 0x100000003]

    def process_id(hwnd, pid):
        ctypes.cast(pid, ctypes.POINTER(wintypes.DWORD)).contents.value = 123 if hwnd == handles[0] else 321
        return 1

    def window_class(hwnd, name, length):
        name.value = "OtherWindow" if hwnd == handles[1] else "Chrome_WidgetWin_1"
        return len(name.value)

    def enum_windows(callback, param):
        for hwnd in handles:
            if not callback(hwnd, param):
                break
        return True

    user32.GetWindowThreadProcessId.side_effect = process_id
    user32.GetClassNameW.side_effect = window_class
    user32.EnumWindows.side_effect = enum_windows
    user32.IsWindowVisible.return_value = True
    user32.IsIconic.return_value = True
    monkeypatch.setattr(ctypes, "WinDLL", Mock(return_value=user32))

    SA._foreground_browser_window(321)

    user32.ShowWindow.assert_called_once_with(handles[2], 9)
    user32.SetForegroundWindow.assert_called_once_with(handles[2])
    assert [(c.args[0], c.args[1]) for c in user32.SetWindowPos.call_args_list] == [
        (handles[2], -1), (handles[2], -2),
    ]


def test_duplicate_profiles_and_unknown_selection_rejected(browser):
    cfg = copy.deepcopy(C.DEFAULT_CONFIG)
    browser["accounts"][1]["profile_dir"] = ".browser/one/../one"
    browser["selected_account"] = "absent"
    cfg["upload"]["selenium"] = browser
    errors = C.validate_config(cfg)
    assert "upload.selenium.accounts.1.profile_dir" in errors
    assert "upload.selenium.selected_account" in errors


def test_login_window_can_reopen_after_user_closes(monkeypatch, browser):
    driver = Mock()
    monkeypatch.setattr(SA, "create_driver", Mock(return_value=driver))
    monkeypatch.setattr(SA, "studio_identity", Mock(side_effect=InvalidSessionIdException("closed")))
    session = SA.LoginSession()
    for _ in range(2):
        session.start(browser, "one")
        session._thread.join(timeout=3)
        assert not session.status()["active"]
        assert session.status()["state"] == "error"
        assert "다시 여세요" in session.status()["message"]
        assert not SA.BROWSER_LOCK.locked()
    assert driver.quit.call_count == 2


def test_login_rejects_upload_in_progress(browser):
    SA.BROWSER_LOCK.acquire()
    try:
        with pytest.raises(SA.BrowserBusy):
            SA.LoginSession().start(browser, "one")
    finally:
        SA.BROWSER_LOCK.release()


def test_finish_without_login_is_rejected():
    with pytest.raises(ValueError):
        SA.LoginSession().finish()


def test_confirmed_login_records_channel_and_recovers_only_login_failures(monkeypatch, browser):
    driver = Mock()
    identity = {"channel_id": "UC" + "a" * 22, "channel_name": "테스트 채널"}
    monkeypatch.setattr(SA, "create_driver", Mock(return_value=driver))
    monkeypatch.setattr(SA, "studio_identity", Mock(return_value=identity))
    record = Mock()
    recover = Mock(return_value=2)
    monkeypatch.setattr(M, "kv_set", record)
    monkeypatch.setattr(SA, "login_retries", recover)
    session = SA.LoginSession()
    session.update(active=True, state="ready")
    session.finish()
    SA.BROWSER_LOCK.acquire()
    entry = SA.account(browser, "one")
    session._run(browser, entry)
    saved = record.call_args.args[1]
    assert saved["verified"] and not saved["needs_login"]
    assert saved["channel_id"] == identity["channel_id"]
    recover.assert_called_once_with(entry)
    assert session.status()["state"] == "complete"
    assert session.status()["retried"] == 2
    assert not SA.BROWSER_LOCK.locked()
    driver.quit.assert_called_once()


def test_cancel_does_not_connect_account(monkeypatch, browser):
    driver = Mock()
    monkeypatch.setattr(SA, "create_driver", Mock(return_value=driver))
    record = Mock()
    monkeypatch.setattr(M, "kv_set", record)
    session = SA.LoginSession()
    session.cancel()
    SA.BROWSER_LOCK.acquire()
    session._run(browser, SA.account(browser))
    record.assert_not_called()
    assert session.status()["state"] == "cancelled"
    assert not SA.BROWSER_LOCK.locked()


def test_wrong_channel_is_blocked_before_upload(monkeypatch, browser):
    cfg = copy.deepcopy(C.DEFAULT_CONFIG["upload"])
    cfg["selenium"] = browser
    worker = Mock()
    worker.stopping.return_value = False
    studio = SU.StudioUpload(worker, {"id": 1, "upload_account_id": "two"}, cfg)
    driver = Mock()
    monkeypatch.setattr(SA, "create_driver", Mock(return_value=driver))
    monkeypatch.setattr(SA, "account_status", lambda entry: {"verified": True, "channel_id": "UC" + "a" * 22})
    monkeypatch.setattr(SA, "studio_identity", lambda *a: {"channel_id": "UC" + "b" * 22})
    studio.wait = Mock()
    with pytest.raises(SA.LoginRequired):
        studio.start_browser()
    driver.get.assert_called_once_with("https://studio.youtube.com/channel/UC" + "a" * 22)
    assert not studio.submitted


def test_worker_waits_for_manual_connection_and_during_login(monkeypatch, browser):
    cfg = copy.deepcopy(C.DEFAULT_CONFIG["upload"])
    cfg.update(mode="selenium", selenium=browser)
    monkeypatch.setattr(C, "get_config", lambda: {"upload": cfg})
    claim = Mock()
    monkeypatch.setattr(M, "claim_item", claim)
    worker = Uploader()
    worker._cleanup_stale = Mock()
    monkeypatch.setattr(SA, "account_status", lambda entry: {})
    assert not worker.tick()
    claim.assert_not_called()
    monkeypatch.setattr(SA, "account_status", lambda entry: {"verified": True})
    SA.BROWSER_LOCK.acquire()
    try:
        assert not worker.tick()
        claim.assert_not_called()
    finally:
        SA.BROWSER_LOCK.release()


def test_selected_account_is_captured_when_item_is_claimed(monkeypatch, browser):
    cfg = copy.deepcopy(C.DEFAULT_CONFIG["upload"])
    cfg.update(mode="selenium", selenium=browser)
    monkeypatch.setattr(C, "get_config", lambda: {"upload": cfg})
    monkeypatch.setattr(SA, "account_status", lambda entry: {"verified": True})
    item = {"id": 42}
    monkeypatch.setattr(M, "claim_item", lambda *a: item)
    update = Mock()
    monkeypatch.setattr(M, "update_item", update)
    worker = Uploader()
    worker._cleanup_stale = Mock()
    worker.process_claimed = Mock()
    assert worker.tick()
    assert item["upload_account_id"] == "two"
    assert item["_upload_config"] is cfg
    update.assert_called_once_with(42, upload_mode="selenium", upload_account_id="two")
    assert not SA.BROWSER_LOCK.locked()


def test_login_failure_recovery_excludes_publication_and_other_account(monkeypatch, browser):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(M.SCHEMA)
    for aid, detail in [("one", "D안 로그인 필요"), ("two", "D안 로그인 필요"), ("one", "게시 결과 확인 중"),
                        (None, "YouTube 로그인 대기 (서버 PC의 Chrome에서 로그인하세요)")]:
        conn.execute("INSERT INTO items(status,upload_mode,upload_account_id,stage_detail,created_at,updated_at) "
                     "VALUES('failed_upload','selenium',?,?,?,?)", (aid, detail, "2026-01-01", "2026-01-01"))
    monkeypatch.setattr(M, "db", lambda: conn)
    update = Mock()
    monkeypatch.setattr(M, "update_item", update)
    assert SA.login_retries(SA.account(browser, "one")) == 1
    assert update.call_args.args == (1,)
    assert SA.login_retries({"id": "default"}) == 1
    assert update.call_args.args == (4,)
    conn.close()


def test_existing_database_adds_account_column(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(M.SCHEMA.replace("    upload_account_id TEXT,\n", ""))
    assert "upload_account_id" not in {r["name"] for r in conn.execute("PRAGMA table_info(items)")}
    monkeypatch.setattr(M, "db", lambda: conn)
    M.init_db()
    assert "upload_account_id" in {r["name"] for r in conn.execute("PRAGMA table_info(items)")}
    M.init_db()  # 재시작 때도 안전하게 적용
    conn.close()
