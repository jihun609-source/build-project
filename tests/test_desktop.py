"""Desktop credentials, data isolation and OS-specific input; no live server or uploads."""
import copy
import socket
from pathlib import Path
from unittest.mock import Mock

import pytest

from src import config as C, desktop_access as access
from src.launcher import InstanceLock, choose_port
from src.util import clipboard


def test_desktop_ticket_is_local_one_time_and_latest_only():
    old = access.issue()
    token = access.issue()
    assert not access.consume(old, '127.0.0.1')
    assert not access.consume(token, '192.168.1.2')
    assert not access.consume('한글', '127.0.0.1')
    assert access.consume(token, '127.0.0.1')
    assert not access.consume(token, '127.0.0.1')


def test_desktop_ticket_expires(monkeypatch):
    monkeypatch.setattr(access.time, 'monotonic', lambda: 100)
    token = access.issue()
    monkeypatch.setattr(access.time, 'monotonic', lambda: 161)
    assert not access.consume(token, '::1')


def test_desktop_login_sets_http_only_cookie_and_rejects_reuse(monkeypatch):
    from starlette.requests import Request
    from fastapi import HTTPException
    from src.routes.system import desktop_login
    from src import auth
    monkeypatch.setattr(C, 'get_config', lambda: {'ui': {'token': 'private-secret'}})
    request = Request({'type': 'http', 'client': ('127.0.0.1', 9000), 'headers': []})
    ticket = access.issue()
    response = desktop_login(request, ticket)
    assert response.status_code == 303
    assert response.headers['location'] == '/ui/'
    assert f'{auth.COOKIE}=private-secret' in response.headers['set-cookie']
    assert 'HttpOnly' in response.headers['set-cookie']
    assert response.headers['cache-control'] == 'no-store'
    with pytest.raises(HTTPException) as error:
        desktop_login(request, ticket)
    assert error.value.status_code == 401


def test_busy_port_is_skipped():
    with socket.socket() as busy:
        busy.bind(('127.0.0.1', 0))
        port = busy.getsockname()[1]
        assert choose_port(port) != port


def test_instance_lock_can_be_reused_after_shutdown(tmp_path):
    path = tmp_path / 'instance.lock'
    first, second = InstanceLock(path), InstanceLock(path)
    try:
        assert first.acquire()
        assert not second.acquire()
        first.close()
        assert second.acquire()
    finally:
        if not first.file.closed:
            first.close()
        second.close()


def test_frozen_bootstrap_excludes_build_pc_settings_and_preserves_user_data(monkeypatch, tmp_path):
    from src import storage
    app, data = tmp_path / 'app', tmp_path / 'user'
    extension = app / 'chrome-extension'
    (extension / 'upload').mkdir(parents=True)
    (extension / 'upload/selectors.json').write_text('{"bundled": true}', encoding='utf-8')
    (extension / 'background.js').write_text('new version', encoding='utf-8')
    (app / '.env.example').write_text('GEMINI_API_KEY=\n', encoding='utf-8')
    (app / 'config.yaml').write_text('ui: {token: BUILD-PC-PRIVATE}', encoding='utf-8')
    dirs = {key: data / key for key in C.DIRS}
    dirs.update(web_dist=app / 'web/dist', extension=data / 'chrome-extension',
                presets=data / 'presets/edit', prompts=data / 'prompts')
    monkeypatch.setattr(C, 'APP_ROOT', app)
    monkeypatch.setattr(C, 'ROOT', data)
    monkeypatch.setattr(C, 'CONFIG_PATH', data / 'config.yaml')
    monkeypatch.setattr(C, 'DIRS', dirs)
    monkeypatch.setattr(C, 'FROZEN', True)
    storage.ensure_initial_files()
    config_text = (data / 'config.yaml').read_text(encoding='utf-8')
    assert 'BUILD-PC-PRIVATE' not in config_text
    assert (data / '.env').read_text() == 'GEMINI_API_KEY=\n'
    selectors = data / 'chrome-extension/upload/selectors.json'
    selectors.write_text('{"user": true}', encoding='utf-8')
    (data / '.env').write_text('user-kept', encoding='utf-8')
    (extension / 'background.js').write_text('updated version', encoding='utf-8')
    storage.ensure_initial_files()
    assert selectors.read_text() == '{"user": true}'
    assert (data / '.env').read_text() == 'user-kept'
    assert (data / 'config.yaml').read_text(encoding='utf-8') == config_text
    assert (data / 'chrome-extension/background.js').read_text() == 'updated version'
    C.invalidate()


def test_mac_clipboard_preserves_unicode_and_newlines(monkeypatch):
    runner = Mock()
    monkeypatch.setattr(clipboard.sys, 'platform', 'darwin')
    monkeypatch.setattr(clipboard.subprocess, 'run', runner)
    text = '한글😀\n\n설명'
    clipboard.copy_text(text)
    args, kwargs = runner.call_args
    assert args == (['/usr/bin/pbcopy'],)
    assert kwargs['input'] == text
    assert kwargs['encoding'] == 'utf-8' and kwargs['check']


def test_mac_title_uses_command_select_all(monkeypatch):
    from src import selenium_upload as upload
    from selenium.webdriver.common.keys import Keys
    monkeypatch.setattr(upload.sys, 'platform', 'darwin')
    monkeypatch.setattr(upload.time, 'sleep', Mock())
    element = Mock()
    upload.human_type(Mock(), element, '제목')
    assert element.send_keys.call_args_list[0].args == (Keys.COMMAND, 'a')


def test_example_has_no_populated_api_key():
    import re
    example = (Path(__file__).resolve().parents[1] / '.env.example').read_text(encoding='utf-8-sig')
    assert not re.search(r'^\w*(?:KEY|TOKEN|SECRET)\w*[ \t]*=[ \t]*\S+', example, re.MULTILINE)


@pytest.mark.parametrize('version, expected', [(114, 'mac_arm64'), (154, 'mac-arm64')])
def test_apple_silicon_driver_uses_native_arch_and_separate_cache(monkeypatch, tmp_path, version, expected):
    from src.selenium_accounts import apple_silicon_patcher
    from undetected_chromedriver import Patcher
    monkeypatch.setattr(C, 'ROOT', tmp_path)
    native = apple_silicon_patcher(Patcher)(version_main=version)
    assert native.platform_name == expected
    assert native.exe_name == 'chromedriver'
    assert Path(native.executable_path).is_relative_to(tmp_path / '.browser/driver/mac-arm64')


def test_apple_silicon_patcher_is_restored_on_start_failure(monkeypatch, tmp_path):
    import types
    import sys
    from src import selenium_accounts as accounts
    from undetected_chromedriver import Patcher
    uc = types.SimpleNamespace(Patcher=Patcher, ChromeOptions=Mock(), Chrome=Mock(side_effect=RuntimeError('closed')))
    monkeypatch.setitem(sys.modules, 'undetected_chromedriver', uc)
    monkeypatch.setattr(accounts.sys, 'platform', 'darwin')
    monkeypatch.setattr(accounts.platform, 'machine', lambda: 'arm64')
    monkeypatch.setattr(C, 'root_path', lambda _: tmp_path)
    with pytest.raises(RuntimeError, match='closed'):
        accounts.create_driver({'step_timeout_sec': 10}, {'profile_dir': 'test'})
    assert uc.Patcher is Patcher


def test_native_mac_driver_is_signed_after_patching(monkeypatch, tmp_path):
    from src import selenium_accounts as accounts
    from undetected_chromedriver import Patcher
    monkeypatch.setattr(C, 'ROOT', tmp_path)
    native = accounts.apple_silicon_patcher(Patcher)(version_main=154)
    driver = Path(native.executable_path)
    driver.write_bytes(b'{window.cdc_test = "' + b'x' * 100 + b'";}')
    calls = []

    def sign(args, **kwargs):
        assert b'undetected chromedriver' in driver.read_bytes()
        assert kwargs['check'] is True and kwargs['timeout'] == 30
        calls.append(args)

    monkeypatch.setattr(accounts.subprocess, 'run', sign)
    native.patch_exe()
    assert calls == [['/usr/bin/codesign', '--force', '--sign', '-', str(driver)]]
