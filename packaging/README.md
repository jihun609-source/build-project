# 설치 파일 만들기 (개발자용)

사용자는 `releases/windows/`, `releases/macos/arm64/`, `releases/macos/x64/`의 설치 파일만 받으면 됩니다.
아래 도구는 빌드하는 PC에만 필요합니다.
설치본은 Python·웹 UI·yt-dlp·ffmpeg·ffprobe·음성 인식 라이브러리를 포함합니다.
CUDA 라이브러리와 AI 모델, Chrome, Ollama는 포함하지 않습니다. 기본 음성 인식은 CPU에서 동작합니다.

## Windows x64

1. Python 3.12, Node.js 22, [Inno Setup 6](https://jrsoftware.org/isdl.php)를 설치합니다.
2. [FFmpeg](https://www.gyan.dev/ffmpeg/builds/)의 full build를 풉니다. `bin`에 ffmpeg.exe와 ffprobe.exe가 있어야 합니다.
3. 프로젝트에서 실행합니다.

```bat
build_windows.bat --ffmpeg-dir "C:\ffmpeg\bin" --version 1.0.0
```

Inno Setup을 다른 곳에 설치했으면 `--iscc "경로\ISCC.exe"`를 추가합니다.
결과: `releases/windows/AutoSet-1.0.0-windows-x64-setup.exe`, portable.zip, SHA-256 파일, 자체 진단 JSON.
관리자 권한 없이 사용자 계정에 설치하며, 삭제해도 사용자 데이터 폴더는 보존합니다.

## macOS Apple Silicon / Intel

해당 CPU의 맥에서 따로 빌드해야 합니다. Windows에서 macOS 실행 파일을 만드는 방식은 지원되지 않습니다.
[PyInstaller 문서](https://pyinstaller.org/en/stable/usage.html)를 참고하세요.

```bash
brew install python@3.12 python-tk@3.12 node ffmpeg
chmod +x build_mac.command
./build_mac.command --version 1.0.0
```

터미널에서 python3.12와 npm이 실행되어야 합니다. Homebrew 셸 설정을 먼저 적용하세요.
결과: `releases/macos/arm64/AutoSet-1.0.0-macos-arm64.dmg` 또는 `releases/macos/x64/AutoSet-1.0.0-macos-x64.dmg`와 진단 JSON.
앱 안에 FFmpeg의 Homebrew 동적 라이브러리도 수집합니다. 다른 맥에서는 Homebrew가 필요 없습니다.
생성된 앱을 빌드 도구가 없는 새 맥에서도 실행해 Chrome 로그인·자막 합성·업로드를 확인한 뒤 배포하세요.

## GitHub Actions로 세 종류 만들기

이 프로젝트를 GitHub 저장소에 올린 뒤 **Actions → Build desktop installers → Run workflow**에서 버전을 입력합니다.
Windows x64, macOS Apple Silicon, macOS Intel 작업이 각각 수행됩니다.
완료된 작업의 **Artifacts → AutoSet-downloads-1.0.0**을 내려받아 압축을 풀면 다음 폴더로 나뉩니다.

```text
Windows/               # 설치 EXE, 무설치 ZIP
macOS/AppleSilicon/    # M 시리즈 맥용 DMG
macOS/Intel/           # Intel 맥용 DMG
README.md             # AI·확장프로그램·계정 설정
```

`AutoSet-windows-x64`, `AutoSet-macos-arm64`, `AutoSet-macos-x64`에서 한 OS의 파일만 따로 받을 수도 있습니다.
세 빌드와 자체 진단이 모두 성공해야 통합 다운로드 묶음을 만듭니다. 실패한 빌드의 결과는 통합하지 않으며,
`Diagnostics-*`에서 진단 보고서를 확인할 수 있습니다. `v1.0.0` 같은 태그로도 실행할 수 있습니다.
외부 공개나 GitHub Release 게시를 자동으로 수행하지 않습니다.

GitHub에 올릴 코드만 ZIP으로 준비하려면 다음 명령을 실행합니다.

```sh
python packaging/source_archive.py --version 1.0.0
```

`releases/AutoSet-1.0.0-github-source.zip`에 소스와 Actions 설정만 들어갑니다.
개인 설정·영상·DB·API 키·Chrome 프로필·사용자 프롬프트·빌드 도구는 포함하지 않습니다.
저장소에 올릴 때 ZIP의 내용을 풀어 저장소 루트에 넣으세요. 수동 Actions 실행에는 대상 저장소의 쓰기 권한이 필요하며,
워크플로 파일이 기본 브랜치에 있어야 합니다. [GitHub 공식 안내](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)를 참고하세요.

매 빌드에서 패키지의 의존성 import, 웹 UI, 확장프로그램, 숨겨진 Tk 창, ffmpeg/ffprobe,
실제 H.264 자막 합성, 음성 감지 모델, yt-dlp 자식 프로세스를 네트워크·서버 실행 없이 확인합니다.
실패하면 빌드를 실패 처리합니다.
소스의 `.env`, config.yaml, DB, 영상, API 키, Chrome 로그인 프로필은 배포 대상에 포함하지 않습니다.
포함하는 파일 목록은 `AutoSet.spec`에 명시되어 있습니다.

## 서명과 업데이트

현재 빌드는 Windows 코드 서명과 Apple Developer ID 서명·공증을 하지 않습니다.
Windows에서 SmartScreen 안내가 나올 수 있습니다. macOS에서 차단되면 출처를 확인한 후
**시스템 설정 → 개인정보 보호 및 보안 → 확인 없이 열기**를 사용합니다.
정식 배포 시에는 소유한 Windows 서명 인증서 및 Apple Developer 계정으로 서명·공증 단계를 추가해야 합니다.
맥 시스템 전체의 Gatekeeper를 끄는 명령은 사용하지 않습니다.

업데이트 전에 AutoSet을 종료하고 같은 위치에 새 버전을 설치합니다.
사용자 데이터는 Windows `%APPDATA%\AutoSet`, macOS `~/Library/Application Support/AutoSet`에 유지됩니다.
다른 PC로 옮길 때 API 키·OAuth 토큰·Chrome 로그인은 새 PC에서 다시 연결하세요.
`licenses/`와 `THIRD_PARTY.md`의 라이선스 안내를 설치 파일과 함께 보관하세요.
