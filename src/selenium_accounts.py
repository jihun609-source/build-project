"""D안 계정별 Chrome 프로필 및 수동 로그인 세션. 로그인과 업로드는 직렬화한다."""
from __future__ import annotations

import copy
import json
import logging
import os
import platform
import re
import subprocess
import sys
import threading
import time

from . import config as C
from . import models as M
from .timeutil import now_iso

BROWSER_LOCK = threading.Lock()
log = logging.getLogger(__name__)


class BrowserBusy(RuntimeError):
    pass


class LoginRequired(RuntimeError):
    pass


def accounts(browser: dict) -> list[dict]:
    return copy.deepcopy(browser.get("accounts") or [
        {"id": "default", "name": "기본 계정", "profile_dir": browser["profile_dir"]}
    ])


def account(browser: dict, account_id: str | None = None) -> dict:
    selected = account_id or browser.get("selected_account", "default")
    for entry in accounts(browser):
        if entry["id"] == selected:
            return entry
    raise ValueError("저장된 D안 계정을 찾을 수 없습니다. 업로드 설정에서 계정을 저장하세요.")


def profile_key(entry: dict) -> str:
    # 이름을 변경하거나 프로필을 교체했을 때 다른 프로필의 로그인 기록을 쓰지 않는다.
    import hashlib
    profile = str(C.root_path(entry["profile_dir"]).resolve()).casefold()
    return "selenium_account_" + hashlib.sha256(profile.encode()).hexdigest()[:24]


def account_status(entry: dict) -> dict:
    return M.kv_get(profile_key(entry), {})


def mark_login_required(entry: dict, message: str):
    M.kv_set(profile_key(entry), {**account_status(entry), "needs_login": True, "verified": False,
                                "error": message, "checked_at": now_iso()})


def _foreground_browser_window(browser_pid: int):
    """이번에 연 Chrome 프로세스의 창만 앞으로 표시한다 (항상 위 고정 없음)."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    signatures = {
        "EnumWindows": ([callback_type, wintypes.LPARAM], wintypes.BOOL),
        "GetWindowThreadProcessId": ([wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
        "IsWindowVisible": ([wintypes.HWND], wintypes.BOOL),
        "IsIconic": ([wintypes.HWND], wintypes.BOOL),
        "GetClassNameW": ([wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
        "ShowWindow": ([wintypes.HWND, ctypes.c_int], wintypes.BOOL),
        "SetWindowPos": ([wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                          ctypes.c_int, ctypes.c_int, wintypes.UINT], wintypes.BOOL),
        "SetForegroundWindow": ([wintypes.HWND], wintypes.BOOL),
    }
    for name, (args, result) in signatures.items():
        function = getattr(user32, name)
        function.argtypes, function.restype = args, result

    windows = []

    @callback_type
    def collect(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == browser_pid and user32.IsWindowVisible(hwnd):
            name = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, name, len(name))
            if name.value == "Chrome_WidgetWin_1":
                windows.append(hwnd)
                return False
        return True

    user32.EnumWindows(collect, 0)
    if windows:
        hwnd = windows[0]
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        # 잠깐 맨 앞으로 옮긴 뒤 즉시 고정을 풀어 사용자가 다른 창으로 전환할 수 있게 한다.
        flags = 0x0001 | 0x0002 | 0x0040  # NOSIZE | NOMOVE | SHOWWINDOW
        try:
            user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, flags)  # HWND_TOPMOST
            user32.SetForegroundWindow(hwnd)
        finally:
            user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, flags)  # HWND_NOTOPMOST


def show_browser_window(driver):
    """시작할 때 한 번만 표시하므로 이후 사용자가 최소화할 수 있다."""
    for action in (
        driver.maximize_window,
        lambda: driver.execute_cdp_cmd("Page.bringToFront", {}),
    ):
        try:
            action()
        except Exception:
            log.debug("Chrome 창 표시 명령 실패", exc_info=True)
    if os.name == "nt" and getattr(driver, "browser_pid", None):
        try:
            _foreground_browser_window(driver.browser_pid)
        except Exception:
            log.warning("Chrome 창을 앞으로 표시하지 못했습니다", exc_info=True)


def apple_silicon_patcher(base):
    """UC 3.5.5 defaults to mac-x64; use the native driver and a separate cache."""
    class NativePatcher(base):
        platform = "darwin"
        data_path = str(C.ROOT / ".browser" / "driver" / "mac-arm64")

        def _set_platform_name(self):
            super()._set_platform_name()
            self.platform_name = "mac_arm64" if self.is_old_chromedriver else "mac-arm64"

        def patch_exe(self):
            result = super().patch_exe()
            # Apple Silicon requires valid code-page signatures after modifying a Mach-O binary.
            subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", self.executable_path],
                           check=True, capture_output=True, text=True, timeout=30)
            return result

    return NativePatcher


def create_driver(browser: dict, entry: dict):
    try:
        import undetected_chromedriver as uc
    except ImportError as e:
        raise RuntimeError("D안 의존성이 없습니다. .venv\\Scripts\\python -m pip install -r requirements.txt 실행") from e
    profile = C.root_path(entry["profile_dir"])
    profile.mkdir(parents=True, exist_ok=True)
    options = uc.ChromeOptions()
    options.add_argument("--lang=ko-KR")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--start-maximized")
    kwargs = {"options": options, "user_data_dir": str(profile.resolve()),
              "use_subprocess": True, "headless": False}
    if browser.get("chrome_binary"):
        kwargs["browser_executable_path"] = str(C.root_path(browser["chrome_binary"]))
    if browser.get("version_main"):
        kwargs["version_main"] = int(browser["version_main"])
    # Callers hold BROWSER_LOCK. Keep the workaround scoped to constructing this driver.
    original_patcher = uc.Patcher
    native_mac = sys.platform == "darwin" and platform.machine().lower() in ("arm64", "aarch64")
    try:
        if native_mac:
            uc.Patcher = apple_silicon_patcher(original_patcher)
        driver = uc.Chrome(**kwargs)
    finally:
        if native_mac:
            uc.Patcher = original_patcher
    driver.set_page_load_timeout(browser["step_timeout_sec"])
    show_browser_window(driver)
    return driver


def studio_identity(driver, selectors: dict) -> dict | None:
    """로그인한 Studio의 채널 ID가 확인되어야 계정 연결을 완료한다."""
    from selenium.webdriver.common.by import By
    from selenium.common.exceptions import StaleElementReferenceException

    ready = False
    for selector in selectors["create_button"]:
        for element in driver.find_elements(By.CSS_SELECTOR, selector):
            try:
                ready = ready or element.is_displayed()
            except StaleElementReferenceException:
                continue
    if not ready:
        return None
    match = re.search(r"^https://studio\.youtube\.com/channel/(UC[A-Za-z0-9_-]{22})(?:[/#?]|$)", driver.current_url)
    if not match:
        return None
    name = driver.execute_script("""
        const el = document.querySelector('#channel-name, #channel-info #name');
        return el ? (el.innerText || '').trim() : '';
    """)
    return {"channel_id": match[1], "channel_name": name or ""}


def login_retries(entry: dict) -> int:
    """게시 클릭 전 로그인 단계에서 실패한 항목만 복구한다."""
    legacy = " OR (upload_account_id IS NULL AND stage_detail LIKE 'YouTube 로그인 대기%')" \
        if entry["id"] == "default" else ""
    rows = M.db().execute(
        "SELECT id FROM items WHERE status='failed_upload' AND upload_mode='selenium' AND "
        f"((upload_account_id=? AND stage_detail='D안 로그인 필요'){legacy})", (entry["id"],)).fetchall()
    for row in rows:
        M.update_item(row["id"], status="captioned", error=None, stage_detail=None,
                      progress_pct=0, next_attempt_at=None)
    return len(rows)


class LoginSession:
    def __init__(self):
        self._guard = threading.Lock()
        self._finish = threading.Event()
        self._cancel = threading.Event()
        self._thread = None
        self._state = {"active": False, "state": "idle", "message": ""}

    def status(self):
        with self._guard:
            return copy.deepcopy(self._state)

    def update(self, **fields):
        with self._guard:
            self._state.update(fields)

    def start(self, browser: dict, account_id: str):
        entry = account(browser, account_id)
        if not BROWSER_LOCK.acquire(blocking=False):
            raise BrowserBusy("D안 Chrome이 사용 중입니다. 업로드가 끝나거나 로그인 창을 닫은 뒤 다시 여세요.")
        with self._guard:
            self._finish.clear()
            self._cancel.clear()
            self._state = {"active": True, "state": "starting", "account_id": entry["id"],
                           "account_name": entry["name"], "message": "서버 PC에서 Chrome을 여는 중입니다",
                           "started_at": now_iso()}
        self._thread = threading.Thread(target=self._run, args=(copy.deepcopy(browser), entry),
                                        name="selenium-login", daemon=True)
        try:
            self._thread.start()
        except BaseException:
            BROWSER_LOCK.release()
            self.update(active=False, state="error", message="로그인 창 시작 실패")
            raise
        return self.status()

    def finish(self):
        state = self.status()
        if not state["active"] or state["state"] != "ready":
            raise ValueError("Chrome에서 로그인하고 사용할 YouTube 채널을 선택한 뒤 완료를 누르세요.")
        self._finish.set()
        return state

    def cancel(self):
        self._cancel.set()
        return self.status()

    def shutdown(self):
        self.cancel()
        if self._thread:
            self._thread.join(timeout=3)

    def _run(self, browser: dict, entry: dict):
        driver = None
        try:
            driver = create_driver(browser, entry)
            driver.get("https://studio.youtube.com/")
            selectors = json.loads((C.DIRS["extension"] / "upload/selectors.json").read_text(encoding="utf-8"))
            # 수동 로그인은 채널을 선택하고 완료 버튼을 누를 때까지 기다린다.
            deadline = time.monotonic() + max(600, browser["login_timeout_sec"])
            while time.monotonic() < deadline:
                if self._cancel.is_set():
                    self.update(state="cancelled", message="로그인 창을 닫았습니다. 다시 열 수 있습니다.")
                    return
                identity = studio_identity(driver, selectors)
                if identity:
                    self.update(state="ready", **identity, message="로그인 확인됨. 사용할 채널을 확인하고 로그인 완료를 누르세요.")
                    if self._finish.is_set():
                        # 창이 열린 사이 설정이 바뀌었어도 시작할 때의 프로필에만 기록한다.
                        M.kv_set(profile_key(entry), {**identity, "verified": True, "needs_login": False,
                                                     "checked_at": now_iso(), "error": None})
                        retried = login_retries(entry)
                        self.update(state="complete", retried=retried, message=f"로그인 완료. 로그인 실패 항목 {retried}건을 다시 대기시켰습니다.")
                        return
                else:
                    self._finish.clear()
                    self.update(state="waiting", channel_id=None, channel_name=None,
                                message="서버 PC의 Chrome에서 YouTube에 로그인하고 채널을 선택하세요.")
                self._cancel.wait(0.5)
            self.update(state="timeout", message="로그인 대기 시간이 지났습니다. 로그인 버튼으로 다시 열 수 있습니다.")
        except Exception as e:
            if e.__class__.__name__ in ("InvalidSessionIdException", "NoSuchWindowException"):
                message = "Chrome 창이 닫혔거나 연결이 끊겼습니다. Chrome 로그인 버튼으로 다시 여세요."
            else:
                message = f"로그인 창 오류: {e}"
            self.update(state="error", message=message)
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass
            self.update(active=False)
            BROWSER_LOCK.release()


LOGIN = LoginSession()
