"""Mac installer regressions: app discovery, version selection and verified downloads."""
import io
import json
from pathlib import Path
import plistlib
import ssl
import sys
from unittest.mock import Mock

import pytest
from undetected_chromedriver import Patcher
from undetected_chromedriver.patcher import LooseVersion

from src import config as C, mac_chrome as mac, selenium_accounts as accounts


def test_mac_app_bundle_and_user_applications_work_without_path(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    bundle = tmp_path / 'Applications/Google Chrome.app'
    binary = bundle / 'Contents/MacOS/Google Chrome'
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b'chrome')
    (bundle / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleShortVersionString': '154.0.8037.98'}))
    assert mac.chrome_binary(str(bundle)) == binary.resolve()
    # Hide system Applications so user-local discovery is checked on every host.
    is_file = Path.is_file
    monkeypatch.setattr(Path, 'is_file', lambda path: False if str(path).replace('\\', '/').startswith('/Applications/') else is_file(path))
    assert mac.chrome_binary() == binary.resolve()
    monkeypatch.setattr(mac.subprocess, 'run', Mock(side_effect=AssertionError('Do not launch Chrome to read its version')))
    assert mac.chrome_major(binary) == 154


def test_missing_explicit_chrome_path_has_actionable_message(tmp_path):
    with pytest.raises(RuntimeError, match='응용 프로그램'):
        mac.chrome_binary(str(tmp_path / 'missing.app'))


def test_mac_launch_uses_installed_major_instead_of_latest_stable(monkeypatch, tmp_path):
    uc = Mock(Patcher=Patcher)
    monkeypatch.setitem(sys.modules, 'undetected_chromedriver', uc)
    monkeypatch.setattr(accounts.sys, 'platform', 'darwin')
    monkeypatch.setattr(accounts.platform, 'machine', lambda: 'arm64')
    monkeypatch.setattr(mac, 'chrome_binary', lambda _: tmp_path / 'Chrome')
    monkeypatch.setattr(mac, 'chrome_major', lambda _: 154)
    monkeypatch.setattr(accounts, 'show_browser_window', Mock())
    accounts.create_driver({'step_timeout_sec': 10, 'version_main': 0}, {'profile_dir': str(tmp_path)})
    assert uc.Chrome.call_args.kwargs['version_main'] == 154
    assert uc.Chrome.call_args.kwargs['browser_executable_path'] == str(tmp_path / 'Chrome')
    assert uc.Patcher is Patcher


@pytest.mark.parametrize('arm64,platform', [(True, 'mac-arm64'), (False, 'mac-x64')])
def test_download_matches_milestone_and_uses_verified_tls_without_system_certs(monkeypatch, tmp_path, arm64, platform):
    monkeypatch.setattr(C, 'ROOT', tmp_path)
    monkeypatch.setenv('SSL_CERT_FILE', str(tmp_path / 'missing.pem'))
    monkeypatch.setenv('SSL_CERT_DIR', str(tmp_path / 'empty-certs'))
    urls = []

    def response(url, *, context, timeout):
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        assert context.cert_store_stats()['x509_ca'] > 0
        assert timeout == 30
        urls.append(url)
        if url.endswith('.json'):
            return io.BytesIO(json.dumps({'milestones': {'154': {'version': '154.0.8037.98'}}}).encode())
        return io.BytesIO(b'google-driver-archive')

    monkeypatch.setattr(mac, 'urlopen', response)
    patcher = mac.macos_patcher(Patcher, arm64=arm64)(version_main=154)
    patcher.version_full = patcher.fetch_release_number()
    archive = Path(patcher.fetch_package())
    assert patcher.version_full.vstring == '154.0.8037.98'
    assert urls[0].endswith('/latest-versions-per-milestone-with-downloads.json')
    assert urls[1] == f'https://storage.googleapis.com/chrome-for-testing-public/154.0.8037.98/{platform}/chromedriver-{platform}.zip'
    assert archive.read_bytes() == b'google-driver-archive'
    assert archive.parent == Path(patcher.data_path)


def test_failed_download_removes_partial_archive(monkeypatch, tmp_path):
    monkeypatch.setattr(C, 'ROOT', tmp_path)
    patcher = mac.macos_patcher(Patcher, arm64=True)(version_main=154)
    patcher.version_full = LooseVersion('154.0.8037.98')
    monkeypatch.setattr(type(patcher), '_open', Mock(side_effect=OSError('network failed')))
    with pytest.raises(OSError, match='network failed'):
        patcher.fetch_package()
    assert not list(Path(patcher.data_path).glob('*.zip'))
