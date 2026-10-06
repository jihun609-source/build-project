"""D안 타이핑·워커 분기·게시 결과 확인을 실제 게시 없이 검증한다."""
from __future__ import annotations

import copy
import sqlite3
from pathlib import Path
from unittest.mock import Mock

import pytest
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import InvalidSessionIdException, NoSuchWindowException, StaleElementReferenceException

from src import config as C
from src import models as M
from src import selenium_upload as SU
from src.util.proc import Cancelled
from src.workers import uploader as U


def test_typing_is_per_character_with_random_delay(monkeypatch):
    events = []
    driver = Mock()
    driver.execute_cdp_cmd.side_effect = lambda cmd, data: events.append(("cdp", data["text"]))
    element = Mock()
    element.get_attribute.return_value = "true"
    element.send_keys.side_effect = lambda *keys: events.append(("keys", keys))
    monkeypatch.setattr(SU.random, "uniform", lambda lo, hi: round((lo + hi) / 2, 3))
    monkeypatch.setattr(SU.time, "sleep", lambda value: events.append(("sleep", value)))
    SU.human_type(driver, element, "한글\n😀")
    assert events == [
        ("keys", (Keys.CONTROL, "a")), ("keys", (Keys.BACKSPACE,)),
        ("sleep", 0.075), ("keys", ("한",)),
        ("sleep", 0.075), ("keys", ("글",)),
        ("sleep", 0.075), ("keys", (Keys.SHIFT, Keys.ENTER)),
        ("sleep", 0.075), ("cdp", "😀"),
    ]


def test_typing_can_stop(monkeypatch):
    element = Mock()
    monkeypatch.setattr(SU.time, "sleep", Mock())
    with pytest.raises(Cancelled):
        SU.human_type(Mock(), element, "문자", stopping=lambda: True)
    assert element.send_keys.call_count == 2


def make_studio(monkeypatch, tmp_path):
    monkeypatch.setattr(SU, "copy_text", Mock())
    cfg = copy.deepcopy(C.DEFAULT_CONFIG["upload"])
    cfg["mode"] = "selenium"
    file = tmp_path / "final.mp4"
    file.write_bytes(b"test")
    item = {"id": 1, "final_path": str(file), "title": "제목", "description": "설명", "tags": ["태그"]}
    monkeypatch.setitem(C.DIRS, "extension", Path(__file__).resolve().parents[1] / "chrome-extension")
    worker = Mock()
    worker.name = "uploader"
    worker.stopping.return_value = False
    studio = SU.StudioUpload(worker, item, cfg)
    studio.driver = Mock()
    studio.start_browser = Mock()
    studio.click = Mock()
    studio.click_element = Mock(side_effect=lambda element, **kwargs: element)
    studio.focus_field = Mock(side_effect=lambda key, label: studio.element(key, label, enabled=True))
    studio.type_field = Mock()
    studio.element = Mock(return_value=Mock())
    studio.find = Mock(return_value=Mock(text="100%"))
    studio.check_dialogs = Mock()
    studio.read_video_id = Mock(return_value="abcdefghijk")
    studio.schedule = Mock()
    studio.visibility = Mock()
    studio.wait = Mock(side_effect=lambda predicate, *a, **kw: predicate())
    monkeypatch.setattr(SU.time, "sleep", Mock())
    monkeypatch.setattr(U, "upload_metadata", lambda item: {k: item[k] for k in ("title", "description", "tags")})
    finish = Mock()
    monkeypatch.setattr(U, "finish_uploaded", finish)
    return studio, finish


@pytest.mark.parametrize("scheduled", [False, True])
def test_upload_finishes_only_after_publish_confirmation(monkeypatch, tmp_path, scheduled):
    studio, finish = make_studio(monkeypatch, tmp_path)
    from datetime import datetime, timezone
    pub = {"mode": "scheduled" if scheduled else "immediate", "at": datetime(2099, 1, 1, tzinfo=timezone.utc)}
    monkeypatch.setattr(U, "resolve_publish_now", lambda *a: pub)
    studio.cfg["made_for_kids"] = True
    studio.run()
    finish.assert_called_once_with(1, "abcdefghijk", "selenium")
    studio.type_field.assert_any_call("title_box", "제목", "제목")
    studio.type_field.assert_any_call("description_box", "설명", "설명")
    assert any(c.args == ("published_dialog", "게시/예약 완료 대화상자") for c in studio.element.call_args_list)
    if scheduled:
        studio.schedule.assert_called_once_with(pub)
    else:
        studio.visibility.assert_called_once()
    studio.driver.quit.assert_called_once()


def test_video_id_before_publish_is_not_success(monkeypatch, tmp_path):
    studio, finish = make_studio(monkeypatch, tmp_path)
    monkeypatch.setattr(U, "resolve_publish_now", lambda *a: {"mode": "immediate", "at": None})
    def element(key, *a, **kw):
        if key == "published_dialog":
            raise RuntimeError("완료 확인 시간 초과")
        return Mock()
    studio.element.side_effect = element
    with pytest.raises(RuntimeError, match="게시 결과 확인이 필요"):
        studio.run()
    finish.assert_not_called()
    studio.driver.quit.assert_called_once()


def test_click_waits_before_click(monkeypatch, tmp_path):
    studio, _ = make_studio(monkeypatch, tmp_path)
    del studio.click_element
    events = []
    monkeypatch.setattr(SU.random, "uniform", lambda lo, hi: 1.75 if (lo, hi) == (1.0, 2.5) else 0)
    monkeypatch.setattr(SU.time, "sleep", lambda value: events.append(("sleep", value)))
    button = Mock()
    button.click.side_effect = lambda: events.append(("click", None))
    studio.click_element(button)
    assert events == [("sleep", 1.75), ("click", None), ("sleep", 0.4)]


def test_click_relocates_element_after_random_wait(monkeypatch, tmp_path):
    studio, _ = make_studio(monkeypatch, tmp_path)
    del studio.click_element
    old, current = Mock(), Mock()
    state = {"replaced": False}
    monkeypatch.setattr(SU.time, "sleep", lambda delay: state.update(replaced=True))
    resolve = lambda: current if state["replaced"] else old

    assert studio.click_element(old, resolve=resolve) is current
    old.click.assert_not_called()
    current.click.assert_called_once()


def test_metadata_focus_uses_fresh_field_without_mouse_click(monkeypatch, tmp_path):
    studio, _ = make_studio(monkeypatch, tmp_path)
    del studio.focus_field
    old, current = Mock(), Mock()
    state = {"replaced": False}
    studio.element.side_effect = lambda *a, **kw: current if state["replaced"] else old
    monkeypatch.setattr(SU.time, "sleep", lambda delay: state.update(replaced=True))
    studio.driver.execute_script.return_value = True

    assert studio.focus_field("description_box", "설명") is current
    assert studio.driver.execute_script.call_args.args[1] is current
    assert "document.activeElement" in studio.driver.execute_script.call_args.args[0]
    old.click.assert_not_called()
    current.click.assert_not_called()


def test_failed_focus_aborts_before_pasting(monkeypatch, tmp_path):
    studio, _ = make_studio(monkeypatch, tmp_path)
    del studio.type_field
    del studio.focus_field
    studio.driver.execute_script.return_value = False
    with pytest.raises(RuntimeError, match="포커스를 맞추지 못했습니다"):
        studio.type_field("description_box", "긴 설명", "설명")
    studio.driver.execute_cdp_cmd.assert_not_called()


@pytest.mark.parametrize("stage", ["focus", "clear", "verify", "blur"])
def test_description_survives_replacement_at_each_stage(monkeypatch, tmp_path, stage):
    studio, _ = make_studio(monkeypatch, tmp_path)
    del studio.type_field
    del studio.click_element
    old, current = Mock(), Mock()
    state = {"replaced": False}
    studio.element.side_effect = lambda *a, **kw: current if state["replaced"] else old
    text = "긴 설명😀\n#태그"

    def replace():
        state["replaced"] = True
        raise StaleElementReferenceException("Studio replaced the field")

    if stage == "focus":
        def focus(key, label):
            if not state["replaced"]:
                replace()
            return current
        studio.focus_field.side_effect = focus

    def send_keys(*keys):
        if stage == "clear" and keys == (Keys.BACKSPACE,):
            replace()

    old.send_keys.side_effect = send_keys

    def execute(script, element, *args):
        if element is old and ((stage == "verify" and "innerText" in script)
                               or (stage == "blur" and ".blur()" in script)):
            replace()
        if "innerText" in script:
            return text

    studio.driver.execute_script.side_effect = execute

    assert studio.type_field("description_box", text, "설명") is current
    # 입력 뒤 검증/추천창 닫기에서 교체되더라도 전체 설명을 다시 붙여넣지 않는다.
    SU.copy_text.assert_called_once_with(text)
    paste_target = current if stage in ("focus", "clear") else old
    paste_target.send_keys.assert_any_call(Keys.CONTROL, "v")
    studio.driver.execute_cdp_cmd.assert_not_called()
    assert state["replaced"]
    studio.driver.quit.assert_not_called()


def test_partial_title_is_retyped_using_new_field(monkeypatch, tmp_path):
    studio, _ = make_studio(monkeypatch, tmp_path)
    del studio.type_field
    old, current = Mock(), Mock()
    state = {"replaced": False}
    studio.element.side_effect = lambda *a, **kw: current if state["replaced"] else old

    def type_text(driver, element, text, stopping):
        if element is old:
            state["replaced"] = True
            raise StaleElementReferenceException("replaced during typing")

    typing = Mock(side_effect=type_text)
    monkeypatch.setattr(SU, "human_type", typing)
    studio.driver.execute_script.return_value = "제목"
    studio.type_field("title_box", "제목", "제목")
    assert [call.args[1] for call in typing.call_args_list] == [old, current]
    studio.driver.execute_cdp_cmd.assert_not_called()


def test_repeated_replacement_stops_after_bounded_retries(monkeypatch, tmp_path):
    studio, _ = make_studio(monkeypatch, tmp_path)
    del studio.type_field
    studio.focus_field.side_effect = StaleElementReferenceException("always replaced")
    with pytest.raises(RuntimeError, match="화면 갱신이 반복"):
        studio.type_field("description_box", "설명", "설명")
    assert studio.focus_field.call_count == 4
    studio.driver.execute_cdp_cmd.assert_not_called()


@pytest.mark.parametrize("key", ["title_box", "description_box"])
def test_missing_characters_abort_before_publication(monkeypatch, tmp_path, key):
    studio, _ = make_studio(monkeypatch, tmp_path)
    del studio.type_field
    studio.driver.execute_script.return_value = "제"
    monkeypatch.setattr(SU, "human_type", Mock())
    with pytest.raises(RuntimeError, match="입력 내용이 일치하지"):
        studio.type_field(key, "제목", "입력칸")


def test_long_description_is_inserted_all_at_once_with_unicode_and_newlines(monkeypatch, tmp_path):
    studio, _ = make_studio(monkeypatch, tmp_path)
    del studio.type_field
    text = "긴 설명 한글😀\r\n" * 300
    pasted = text.replace("\r\n", "\n")
    studio.driver.execute_script.return_value = pasted
    slow_typing = Mock(side_effect=AssertionError("설명을 글자별로 입력함"))
    monkeypatch.setattr(SU, "human_type", slow_typing)
    studio.type_field("description_box", text, "설명")
    SU.copy_text.assert_called_once_with(pasted)
    studio.element.return_value.send_keys.assert_any_call(Keys.CONTROL, "v")
    assert all("execCommand" not in call.args[0] for call in studio.driver.execute_script.call_args_list)
    studio.driver.execute_cdp_cmd.assert_not_called()
    slow_typing.assert_not_called()


def test_failed_clipboard_copy_aborts_without_publish(monkeypatch, tmp_path):
    studio, finish = make_studio(monkeypatch, tmp_path)
    del studio.type_field
    SU.copy_text.side_effect = RuntimeError("클립보드가 사용 중")
    with pytest.raises(RuntimeError, match="클립보드가 사용 중"):
        studio.type_field("description_box", "설명", "설명")
    assert (Keys.CONTROL, "v") not in [call.args for call in studio.element.return_value.send_keys.call_args_list]
    finish.assert_not_called()
    assert not studio.submitted


def test_title_still_uses_random_character_typing(monkeypatch, tmp_path):
    studio, _ = make_studio(monkeypatch, tmp_path)
    del studio.type_field
    studio.driver.execute_script.return_value = "제목"
    slow_typing = Mock()
    monkeypatch.setattr(SU, "human_type", slow_typing)
    studio.type_field("title_box", "제목", "제목")
    slow_typing.assert_called_once_with(studio.driver, studio.element.return_value, "제목", studio.worker.stopping)
    studio.driver.execute_cdp_cmd.assert_not_called()


@pytest.mark.parametrize("key", ["title_box", "description_box"])
def test_metadata_dismisses_suggestions_before_next_field(monkeypatch, tmp_path, key):
    studio, _ = make_studio(monkeypatch, tmp_path)
    del studio.type_field
    events = []
    element = studio.element.return_value
    element.send_keys.side_effect = lambda *keys: events.append(("keys", keys))

    def execute_script(script, field, *args):
        events.append(("script", script))
        if "innerText" in script:
            return "#태그"

    studio.driver.execute_script.side_effect = execute_script
    monkeypatch.setattr(SU, "human_type", Mock())
    studio.type_field(key, "#태그", "입력칸")
    assert events[-2:] == [
        ("script", "return arguments[0].isContentEditable ? arguments[0].innerText : arguments[0].value;"),
        ("script", "arguments[0].blur();"),
    ]
    assert ("keys", (Keys.ESCAPE,)) not in events


@pytest.mark.parametrize("error", [InvalidSessionIdException, NoSuchWindowException])
@pytest.mark.parametrize("submitted", [False, True])
def test_disconnected_browser_fails_with_clear_message_and_preserves_uncertain_publish(
    monkeypatch, tmp_path, error, submitted,
):
    studio, finish = make_studio(monkeypatch, tmp_path)
    studio.submitted = submitted
    studio.start_browser.side_effect = error("closed")
    expected = "게시 결과 확인이 필요" if submitted else "Chrome 창이 닫혔거나 연결이 끊겼습니다"
    with pytest.raises(RuntimeError, match=expected):
        studio.run()
    finish.assert_not_called()
    studio.driver.quit.assert_called_once()


def test_publish_timeout_never_clicks_done(monkeypatch, tmp_path):
    studio, finish = make_studio(monkeypatch, tmp_path)
    monkeypatch.setattr(U, "resolve_publish_now", lambda *a: {"mode": "immediate", "at": None})
    def wait(predicate, label, *a, **kw):
        if label == "업로드·처리 완료":
            raise RuntimeError("업로드 시간 초과")
        return predicate()
    studio.wait.side_effect = wait
    with pytest.raises(RuntimeError, match="업로드 시간 초과"):
        studio.run()
    finish.assert_not_called()
    assert not studio.submitted
    assert not any(call.args[0] == "done_button" for call in studio.element.call_args_list)
    studio.driver.quit.assert_called_once()


def test_d_mode_claims_without_api_token_or_quota(monkeypatch):
    cfg = copy.deepcopy(C.DEFAULT_CONFIG["upload"])
    cfg["mode"] = "selenium"
    monkeypatch.setattr(C, "get_config", lambda: {"upload": cfg})
    from src import selenium_accounts as SA
    monkeypatch.setattr(SA, "account_status", lambda entry: {})
    monkeypatch.setattr(U, "api_uploads_today", Mock(side_effect=AssertionError("API quota checked")))
    item = {"id": 17}
    monkeypatch.setattr(M, "claim_item", Mock(return_value=item))
    update = Mock()
    monkeypatch.setattr(M, "update_item", update)
    worker = U.Uploader()
    worker._cleanup_stale = Mock()
    worker.process_claimed = Mock()
    assert worker.tick()
    update.assert_called_once_with(17, upload_mode="selenium", upload_account_id="default")
    assert item["upload_mode"] == "selenium"
    worker.process_claimed.assert_called_once_with(item, "captioned")


def test_d_settings_validate():
    cfg = copy.deepcopy(C.DEFAULT_CONFIG)
    cfg["upload"]["mode"] = "selenium"
    assert C.validate_config(cfg) == {}
    cfg["upload"]["selenium"].update(version_main=-1, login_timeout_sec=0, ampm=["오전"])
    errors = C.validate_config(cfg)
    assert {"upload.selenium.version_main", "upload.selenium.login_timeout_sec", "upload.selenium.ampm"} <= errors.keys()


def test_recovery_does_not_reupload_uncertain_publication(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.executescript(M.SCHEMA)
    for mode, detail in [("api", None), ("extension", None), ("selenium", None), ("selenium", "게시 결과 확인 중")]:
        conn.execute("INSERT INTO items(status,upload_mode,stage_detail,created_at,updated_at) VALUES(?,?,?,?,?)",
                     ("uploading", mode, detail, "2026-01-01", "2026-01-01"))
    monkeypatch.setattr(M, "db", lambda: conn)
    assert M.recover_interrupted() == 2
    assert [r[0] for r in conn.execute("SELECT status FROM items ORDER BY id")] == [
        "captioned", "uploading", "captioned", "failed_upload"]
    conn.close()
