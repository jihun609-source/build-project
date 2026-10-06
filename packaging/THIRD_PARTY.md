# Third-party components

AutoSet bundles Python, PyInstaller's bootloader, Python packages and FFmpeg/ffprobe.
The `licenses/` folder includes package license notices and a version inventory.
Ollama, downloaded AI models and Google Chrome are separate installations.

FFmpeg: https://ffmpeg.org/ — license depends on the selected build's configuration.
Windows build source and corresponding source archives: https://www.gyan.dev/ffmpeg/builds/
macOS build recipe and source URLs: https://github.com/Homebrew/homebrew-core/blob/master/Formula/f/ffmpeg.rb
macOS static builds used by Actions: https://github.com/eugeneware/ffmpeg-static/releases/tag/b6.1.1
macOS binary providers and exact build configurations: https://github.com/eugeneware/ffmpeg-static#sources-of-the-binaries
FFmpeg source: https://git.ffmpeg.org/ffmpeg.git
Run the bundled `ffmpeg -version` to identify the exact build and `ffmpeg -L` for its license.
The Windows full build includes GPL components. Retain these notices when redistributing.

Python: https://www.python.org/psf/license/
PyInstaller: https://pyinstaller.org/en/stable/license.html (bootloader distribution exception).
