import os
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
stage = root / 'build' / 'package-resources'
ffmpeg = Path(os.environ['AUTOSET_FFMPEG_DIR'])
extension = '.exe' if sys.platform == 'win32' else ''
datas = [(str(root / 'web' / 'dist'), 'web/dist'),
         (str(root / 'chrome-extension'), 'chrome-extension'),
         (str(root / '.env.example'), '.'),
         (str(root / 'README.md'), '.'),
         (str(root / 'packaging' / 'README.md'), 'packaging'),
         (str(root / 'packaging' / 'THIRD_PARTY.md'), '.'),
         (str(stage / 'licenses'), 'licenses')]
binaries = [(str(ffmpeg / (tool + extension)), 'bin') for tool in ('ffmpeg', 'ffprobe')]
hidden = collect_submodules('src') + ['uvicorn.logging', 'uvicorn.loops.asyncio',
    'uvicorn.protocols.http.h11_impl', 'uvicorn.protocols.websockets.websockets_impl',
    'uvicorn.lifespan.on', 'tkinter', 'tkinter.ttk', 'google.genai', 'anthropic',
    'undetected_chromedriver', 'setuptools', 'tzdata']
for package in ('faster_whisper', 'ctranslate2', 'av', 'onnxruntime', 'tokenizers', 'yt_dlp'):
    more_data, more_binary, more_hidden = collect_all(package)
    datas += more_data
    binaries += more_binary
    hidden += more_hidden
for distribution in ('undetected-chromedriver', 'yt-dlp', 'google-genai', 'anthropic',
                     'faster-whisper', 'ctranslate2', 'onnxruntime', 'selenium', 'tzdata'):
    datas += copy_metadata(distribution)

a = Analysis([str(root / 'src' / 'launcher.py')], pathex=[str(root)],
    binaries=binaries, datas=datas, hiddenimports=hidden,
    excludes=['torch', 'tensorflow', 'nvidia', 'pytest', 'IPython', 'matplotlib'],
    noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='AutoSet',
          debug=False, strip=False, upx=False, console=False,
          disable_windowed_traceback=False)
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='AutoSet')
if sys.platform == 'darwin':
    app = BUNDLE(collection, name='AutoSet.app', bundle_identifier='app.autoset.desktop',
        info_plist={'CFBundleShortVersionString': os.environ.get('AUTOSET_VERSION', '1.0.0'),
                    'NSHighResolutionCapable': True})
