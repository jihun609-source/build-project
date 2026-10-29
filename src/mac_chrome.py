"""macOS Chrome discovery and verified, architecture-specific UC driver downloads."""
from __future__ import annotations

import json
from pathlib import Path
import plistlib
import re
import shutil
import ssl
import subprocess
import tempfile
from urllib.request import urlopen

import certifi

from . import config as C


def chrome_binary(configured: str = "") -> Path:
    candidates = [C.root_path(str(Path(configured).expanduser()))] if configured else [
        Path("/Applications/Google Chrome.app"),
        Path.home() / "Applications/Google Chrome.app",
    ]
    for candidate in candidates:
        if candidate.suffix.lower() == ".app":
            candidate = candidate / "Contents/MacOS/Google Chrome"
        if candidate.is_file():
            return candidate.resolve()
    raise RuntimeError("Chrome 실행 파일을 찾을 수 없습니다. Google Chrome을 응용 프로그램(Applications)에 "
                       "설치하고 D안의 Chrome 경로를 비워 두세요. 직접 지정할 때는 Google Chrome.app도 사용할 수 있습니다.")


def chrome_major(binary: Path) -> int:
    try:
        with (binary.parent.parent / "Info.plist").open("rb") as info:
            version = plistlib.load(info)["CFBundleShortVersionString"]
    except (OSError, ValueError, KeyError):
        version = subprocess.run([str(binary), "--version"], capture_output=True, text=True,
                                 check=True, timeout=10).stdout
    match = re.search(r"\b(\d+)\.\d+\.\d+\.\d+", str(version))
    if not match:
        raise RuntimeError("설치된 Chrome 버전을 확인할 수 없습니다. Chrome을 업데이트하고 다시 시도하세요.")
    return int(match[1])


def macos_patcher(base, *, arm64: bool):
    class MacPatcher(base):
        platform = "darwin"
        data_path = str(C.ROOT / ".browser/driver" / ("mac-arm64" if arm64 else "mac-x64"))

        def _set_platform_name(self):
            super()._set_platform_name()
            if arm64:
                self.platform_name = "mac_arm64" if self.is_old_chromedriver else "mac-arm64"

        def _open(self, url):
            # Frozen Python's build-machine CA paths may not exist on a clean Mac.
            context = ssl.create_default_context()
            context.load_verify_locations(cafile=certifi.where())
            return urlopen(url, context=context, timeout=30)

        def fetch_release_number(self):
            from undetected_chromedriver.patcher import LooseVersion
            if self.is_old_chromedriver:
                with self._open(f"{self.url_repo}/LATEST_RELEASE_{self.version_main}") as response:
                    return LooseVersion(response.read().decode().strip())
            endpoint = ("latest-versions-per-milestone-with-downloads.json" if self.version_main
                        else "last-known-good-versions-with-downloads.json")
            with self._open(f"{self.url_repo}/{endpoint}") as response:
                data = json.load(response)
            version = (data["milestones"][str(self.version_main)]["version"] if self.version_main
                       else data["channels"]["Stable"]["version"])
            return LooseVersion(version)

        def fetch_package(self):
            version = self.version_full.vstring
            if self.is_old_chromedriver:
                url = f"{self.url_repo}/{version}/chromedriver_{self.platform_name}.zip"
            else:
                url = (f"https://storage.googleapis.com/chrome-for-testing-public/{version}/"
                       f"{self.platform_name}/chromedriver-{self.platform_name}.zip")
            with tempfile.NamedTemporaryFile(dir=self.data_path, suffix=".zip", delete=False) as output:
                archive = Path(output.name)
                try:
                    with self._open(url) as response:
                        shutil.copyfileobj(response, output)
                except BaseException:
                    output.close()
                    archive.unlink(missing_ok=True)
                    raise
            return str(archive)

        def patch_exe(self):
            result = super().patch_exe()
            # Patching invalidates Google's Mach-O signature; sign the changed file.
            subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", self.executable_path],
                           check=True, capture_output=True, text=True, timeout=30)
            return result

    return MacPatcher
