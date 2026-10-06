#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
python3.12 -m venv .venv-build
.venv-build/bin/python -m pip install -r packaging/requirements.txt
.venv-build/bin/python packaging/fetch_ffmpeg.py
npm --prefix web ci
npm --prefix web run build
.venv-build/bin/python packaging/build.py --ffmpeg-dir .build-tools/ffmpeg-macos "$@"
