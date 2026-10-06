"""D안: 전용 Chrome 프로필 + Selenium/undetected-chromedriver 업로드."""
from __future__ import annotations

import json
import random
import re
import sys
import time
from pathlib import Path
from typing import Any

from . import config as C
from . import models as M
from . import selenium_accounts as SA
from .timeutil import local
from .util.clipboard import copy_text
from .util.proc import Cancelled


def human_type(driver, element, text: str, stopping=lambda: False) -> None:
    """복사·붙여넣기 없이 한 글자씩 입력. 이모지도 한 글자씩 CDP로 전달."""
    from selenium.webdriver.common.keys import Keys

    modifier = Keys.COMMAND if sys.platform == "darwin" else Keys.CONTROL
    element.send_keys(modifier, "a")
    element.send_keys(Keys.BACKSPACE)
    for char in text.replace("\r\n", "\n").replace("\r", "\n"):
        if stopping():
            raise Cancelled()
        time.sleep(random.uniform(0.05, 0.1))
        if ord(char) > 0xFFFF:
            # ChromeDriver send_keys는 BMP 밖의 문자(이모지)를 지원하지 않는다.
            driver.execute_cdp_cmd("Input.insertText", {"text": char})
        elif char == "\n" and element.get_attribute("contenteditable") == "true":
            element.send_keys(Keys.SHIFT, Keys.ENTER)
        else:
            element.send_keys(char)


class StudioUpload:
    def __init__(self, worker, item: dict[str, Any], cfg: dict):
        self.worker = worker
        self.item = item
        self.cfg = cfg
        self.browser = cfg["selenium"]
        self.account = SA.account(self.browser, item.get("upload_account_id"))
        self.driver = None
        self.submitted = False
        self.video_id = None
        path = C.DIRS["extension"] / "upload" / "selectors.json"
        self.selectors = json.loads(path.read_text(encoding="utf-8"))

    def check_stop(self):
        if self.worker.stopping():
            raise Cancelled()

    def find(self, selectors, hidden=False, enabled=False):
        from selenium.common.exceptions import StaleElementReferenceException
        from selenium.webdriver.common.by import By

        for selector in ([selectors] if isinstance(selectors, str) else selectors or []):
            for element in self.driver.find_elements(By.CSS_SELECTOR, selector):
                try:
                    if not hidden and not element.is_displayed():
                        continue
                    if enabled and (not element.is_enabled() or element.get_attribute("disabled") is not None
                                    or element.get_attribute("aria-disabled") == "true"):
                        continue
                    return element
                except StaleElementReferenceException:
                    continue
        return None

    def check_dialogs(self):
        unexpected = self.driver.execute_script("""
            const s = arguments[0];
            const visible = e => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden';
            const known = (s.known_dialogs || []).join(',');
            for (const sel of s.any_dialog || []) {
                for (const d of document.querySelectorAll(sel)) {
                    if (!visible(d)) continue;
                    if (known && (d.matches(known) || d.closest(known) || d.querySelector(known))) continue;
                    return (d.innerText || '').trim().slice(0, 150);
                }
            }
            return null;
        """, self.selectors)
        if unexpected is not None:
            raise RuntimeError(f"예상하지 못한 대화상자: {unexpected}")

    def wait(self, predicate, label, timeout=None, dialogs=True, poll=0.3):
        from selenium.common.exceptions import StaleElementReferenceException

        deadline = time.monotonic() + (timeout or self.browser["step_timeout_sec"])
        while time.monotonic() < deadline:
            self.check_stop()
            if dialogs:
                self.check_dialogs()
            try:
                result = predicate()
            except StaleElementReferenceException:
                result = None  # Studio가 화면을 교체했으면 다음 조회에서 다시 찾는다.
            if result:
                return result
            time.sleep(poll)
        raise RuntimeError(f"{label} 대기 시간 초과. 로그인 상태와 selectors.json을 확인하세요.")

    def element(self, key, label, **kwargs):
        return self.wait(lambda: self.find(self.selectors[key], **kwargs), label)

    def retry_stale(self, action, label):
        from selenium.common.exceptions import StaleElementReferenceException

        for attempt in range(4):
            self.check_stop()
            try:
                return action()
            except StaleElementReferenceException as e:
                if attempt == 3:
                    raise RuntimeError(f"{label}: 화면 갱신이 반복되어 진행하지 못했습니다. 다시 시도하세요.") from e
                self.worker.log("debug", f"{label}: 화면 갱신으로 요소를 다시 찾습니다 ({attempt + 1}/3)",
                                self.item["id"])
                time.sleep(0.3)

    def click_element(self, element, resolve=None):
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
        time.sleep(random.uniform(1.0, 2.5))
        self.check_stop()
        self.check_dialogs()
        if resolve:
            # 랜덤 대기 동안 DOM이 교체될 수 있으므로 클릭 직전에 다시 찾는다.
            element = resolve()
            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
        element.click()
        time.sleep(0.4)
        return element

    def click(self, key, label):
        resolve = lambda: self.element(key, label, enabled=True)
        return self.retry_stale(lambda: self.click_element(resolve(), resolve=resolve), label)

    def focus_field(self, key, label):
        element = self.element(key, label, enabled=True)
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
        time.sleep(random.uniform(1.0, 2.5))
        self.check_stop()
        self.check_dialogs()
        # 추천창이 입력칸을 가려도 안전하게 포커스를 옮긴다. 대기 후 최신 입력칸을 사용한다.
        element = self.element(key, label, enabled=True)
        focused = self.driver.execute_script("""
            arguments[0].scrollIntoView({block: 'center'});
            arguments[0].focus();
            return document.activeElement === arguments[0];
        """, element)
        if not focused:
            raise RuntimeError(f"{label} 입력칸에 포커스를 맞추지 못했습니다")
        return element

    def type_field(self, key, text, label, enter=False):
        from selenium.webdriver.common.keys import Keys

        modifier = Keys.COMMAND if sys.platform == "darwin" else Keys.CONTROL
        resolve = lambda: self.element(key, label, enabled=True)
        metadata = key in ("title_box", "description_box")

        def insert():
            if key == "description_box":
                self.worker.log("debug", "D안 설명 입력칸 준비 중", self.item["id"])
            element = self.focus_field(key, label) if metadata else self.click_element(resolve(), resolve=resolve)
            if key == "description_box":
                self.check_stop()
                element.send_keys(modifier, "a")
                element.send_keys(Keys.BACKSPACE)
                # Chrome의 일반 텍스트 붙여넣기가 빈 줄을 보존하도록 편집칸 표시 형식을 맞춘다.
                self.driver.execute_script("arguments[0].style.whiteSpace = 'pre-wrap'; arguments[0].focus();", element)
                pasted = text.replace("\r\n", "\n").replace("\r", "\n")
                if pasted:
                    self.worker.log("debug", f"D안 설명 한 번에 입력 시작 ({len(pasted)}자)", self.item["id"])
                    # 실제 일반 텍스트 붙여넣기: HTML 삽입/Trusted Types 정책 변경을 사용하지 않는다.
                    copy_text(pasted)
                    self.check_stop()
                    element.send_keys(modifier, "v")
                    self.worker.log("debug", "D안 설명 입력 명령 완료 · 내용 확인 중", self.item["id"])
            else:
                human_type(self.driver, element, text, self.worker.stopping)
            return element

        # 제목·설명 재입력은 먼저 내용을 비우므로 부분 입력이 중복되지 않는다.
        element = self.retry_stale(insert, label) if metadata else insert()
        if metadata:
            actual = self.retry_stale(lambda: self.driver.execute_script(
                "return arguments[0].isContentEditable ? arguments[0].innerText : arguments[0].value;",
                resolve()), f"{label} 확인")

            def normalized(value):
                return (value or "").replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ").strip()

            if normalized(actual) != normalized(text):
                raise RuntimeError(f"{label} 입력 내용이 일치하지 않아 게시를 중단했습니다")
            # Esc는 상위 업로드 대화상자를 닫을 수 있으므로 포커스만 해제한다.
            def dismiss_suggestions():
                current = resolve()
                if not enter:
                    self.driver.execute_script("arguments[0].blur();", current)
                return current

            element = self.retry_stale(dismiss_suggestions, f"{label} 추천창 닫기")
        if enter:
            time.sleep(random.uniform(1.0, 2.5))
            self.check_stop()
            element = resolve()
            element.send_keys(Keys.ENTER)
        return element

    def progress(self, pct, text):
        self.worker.progress(self.item["id"], pct, text)

    def start_browser(self):
        from selenium.common.exceptions import WebDriverException

        self.progress(0, f"D안 Chrome 시작 중 · {self.account['name']}")
        self.driver = SA.create_driver(self.browser, self.account)
        saved = SA.account_status(self.account)
        channel_id = saved.get("channel_id") if saved.get("verified") else None
        url = f"https://studio.youtube.com/channel/{channel_id}" if channel_id else "https://studio.youtube.com/"
        try:
            self.driver.get(url)
            self.progress(1, "YouTube 로그인 대기 (서버 PC의 Chrome에서 로그인하세요)")
            self.wait(lambda: self.find(self.selectors["create_button"]), "YouTube 스튜디오 로그인",
                      self.browser["login_timeout_sec"], dialogs=False)
            if channel_id:
                identity = SA.studio_identity(self.driver, self.selectors)
                if not identity or identity["channel_id"] != channel_id:
                    raise RuntimeError("선택한 계정의 채널이 일치하지 않습니다")
        except Cancelled:
            raise
        except (WebDriverException, RuntimeError) as e:
            raise SA.LoginRequired("D안 로그인 필요: Chrome 로그인 버튼으로 계정을 연결한 뒤 로그인 완료를 누르세요.") from e

    def read_video_id(self):
        from selenium.webdriver.common.by import By

        # 업로드 대화상자뿐 아니라 게시 완료 대화상자에 표시된 링크도 검사한다.
        for selector in self.selectors["video_link"] + [
            "ytcp-video-share-dialog a[href]", "ytcp-uploads-still-processing-dialog a[href]"
        ]:
            for element in self.driver.find_elements(By.CSS_SELECTOR, selector):
                href = element.get_attribute("href") or element.text or ""
                match = re.search(r"(?:youtu\.be/|shorts/|[?&]v=)([A-Za-z0-9_-]{11})(?:[^A-Za-z0-9_-]|$)", href)
                if match:
                    return match[1]
        return None

    def visibility(self):
        selectors = self.selectors["visibility"][self.cfg["privacy"]]
        self.click_element(self.wait(lambda: self.find(selectors, enabled=True), "공개 범위"))

    def schedule(self, pub):
        from .routes.upload_bridge import _fmt_date, _fmt_time

        lt = local(pub["at"])
        self.click("schedule_expand", "예약 펼치기")
        self.click("schedule_date_trigger", "예약 날짜")
        self.type_field("schedule_date_input", _fmt_date(lt, self.browser["date_format"]), "예약 날짜", enter=True)
        self.type_field("schedule_time_input", _fmt_time(lt, self.browser["time_format"], self.browser["ampm"]),
                        "예약 시각", enter=True)

    def run(self):
        from selenium.common.exceptions import InvalidSessionIdException, NoSuchWindowException
        from .workers.uploader import finish_uploaded, resolve_publish_now, upload_metadata

        path = Path(self.item.get("final_path") or "")
        if not path.is_file():
            raise RuntimeError(f"완성본 파일이 없습니다: {path}")
        try:
            self.start_browser()
            self.click("create_button", "만들기")
            self.click("upload_menu_item", "동영상 업로드 메뉴")
            self.element("upload_dialog", "업로드 대화상자")
            self.element("file_input", "파일 선택", hidden=True).send_keys(str(path.resolve()))
            self.progress(3, "제목 타이핑·설명 붙여넣기 중")
            meta = upload_metadata(self.item)
            self.type_field("title_box", meta["title"], "제목")
            self.type_field("description_box", meta["description"], "설명")
            kids_key = "made_for_kids" if self.cfg["made_for_kids"] else "not_made_for_kids"
            kids_selectors = self.selectors.get(kids_key) or ["[name='VIDEO_MADE_FOR_KIDS_MFK']"]
            self.click_element(self.wait(lambda: self.find(kids_selectors, enabled=True), "아동용 콘텐츠 설정"))
            if meta["tags"]:
                more = self.find(self.selectors["show_more"], enabled=True)
                if more:
                    self.click_element(more)
                self.type_field("tags_input", ",".join(meta["tags"]) + ",", "태그", enter=True)
            self.progress(10, "세부정보 입력 완료")
            for step in range(3):
                self.click("next_button", f"다음 단계 {step + 1}")

            # 로그인/타이핑 사이에 예약 시간이 지났을 수 있어 이 시점에 다시 해석한다.
            pub = resolve_publish_now(self.item, self.worker.name)
            if pub["mode"] == "scheduled":
                self.schedule(pub)
            else:
                self.visibility()

            def ready_to_publish():
                self.video_id = self.read_video_id() or self.video_id
                label = self.find(self.selectors["progress_label"])
                text = label.text.strip() if label else ""
                pct = re.search(r"(\d{1,3})\s*%", text)
                if pct:
                    value = int(pct[1])
                    self.progress(min(95, 10 + value * 0.85), text)
                    if value < 100:
                        return None
                # 진행 문구가 없는 경우에도 Studio가 활성화한 완료 버튼을 기다린다.
                return self.find(self.selectors["done_button"], enabled=True)

            self.wait(ready_to_publish, "업로드·처리 완료", self.browser["upload_timeout_sec"], poll=1.5)
            # 최종 클릭 직전에도 지난 예약 시각을 즉시 게시로 전환한다.
            latest = resolve_publish_now(self.item, self.worker.name)
            if pub["mode"] == "scheduled" and latest["mode"] == "immediate":
                self.visibility()
            done = self.element("done_button", "게시/예약 버튼", enabled=True)
            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", done)
            time.sleep(random.uniform(1.0, 2.5))
            self.check_stop()
            self.check_dialogs()
            self.progress(95, "게시 결과 확인 중")
            self.submitted = True
            done.click()
            # 사전에 받은 영상 ID만으로 성공 처리하지 않는다. 게시 완료 UI도 확인한다.
            self.element("published_dialog", "게시/예약 완료 대화상자")
            self.video_id = self.wait(lambda: self.read_video_id() or self.video_id, "영상 링크")
            finish_uploaded(self.item["id"], self.video_id, "selenium")
        except BaseException as e:
            if self.submitted:
                link = f" https://youtu.be/{self.video_id}" if self.video_id else ""
                raise RuntimeError(f"D안 게시 결과 확인이 필요합니다.{link} 스튜디오에서 확인한 뒤 재시도하세요: {e}") from e
            if isinstance(e, (InvalidSessionIdException, NoSuchWindowException)):
                raise RuntimeError("D안 Chrome 창이 닫혔거나 연결이 끊겼습니다. 업로드 중에는 창을 닫지 말고 "
                                   "최소화하세요. 실패 항목을 다시 시도할 수 있습니다.") from e
            raise
        finally:
            if self.driver is not None:
                try:
                    self.driver.quit()
                except Exception as e:
                    self.worker.log("warning", f"D안 Chrome 종료 실패: {e}", self.item["id"])


def upload(worker, item: dict[str, Any]) -> None:
    cfg = item.get("_upload_config") or C.get_config()["upload"]
    studio = StudioUpload(worker, item, cfg)
    try:
        studio.run()
    except SA.LoginRequired as e:
        SA.mark_login_required(studio.account, str(e))
        M.update_item(item["id"], stage_detail="D안 로그인 필요")
        raise
