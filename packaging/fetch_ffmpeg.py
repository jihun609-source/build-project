"""Fetch matching macOS static builds with libass, plus upstream license notices."""
from __future__ import annotations

import gzip
import os
from pathlib import Path
import platform
import shutil
import ssl
import sys
import urllib.request

import certifi

ROOT = Path(__file__).resolve().parent.parent
RELEASE = 'b6.1.1'
BASE = f'https://github.com/eugeneware/ffmpeg-static/releases/download/{RELEASE}'


def main():
    if sys.platform != 'darwin':
        raise SystemExit('This helper downloads macOS FFmpeg; use the Windows build instructions on Windows.')
    arch = 'arm64' if platform.machine().lower() in ('arm64', 'aarch64') else 'x64'
    directory = ROOT / '.build-tools/ffmpeg-macos'
    directory.mkdir(parents=True, exist_ok=True)
    context = ssl.create_default_context(cafile=certifi.where())
    for tool in ('ffmpeg', 'ffprobe'):
        url = f'{BASE}/{tool}-darwin-{arch}.gz'
        target = directory / tool
        with urllib.request.urlopen(url, context=context, timeout=120) as response:
            with gzip.GzipFile(fileobj=response) as unpacked, target.open('wb') as output:
                shutil.copyfileobj(unpacked, output)
        target.chmod(0o755)
    for notice in ('LICENSE', 'README'):
        with urllib.request.urlopen(f'{BASE}/darwin-{arch}.{notice}', context=context, timeout=30) as response:
            (directory / notice).write_bytes(response.read())
    print(directory)
    if os.environ.get('GITHUB_PATH'):
        with open(os.environ['GITHUB_PATH'], 'a', encoding='utf-8') as paths:
            paths.write(str(directory) + '\n')


if __name__ == '__main__':
    main()
