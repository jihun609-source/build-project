# AutoSet: 릴스 선별 → 자동 편집 → 유튜브 예약 업로드

인스타그램 릴스를 평소처럼 보는 동안 크롬 확장프로그램이 화면에 지나간 릴스를 기록합니다.
웹 관리 UI에서 고른 것만 내려받아 편집 프리셋대로 편집하고, AI(로컬 Ollama Gemma 4, 실패하면 Gemini)가 만든
하단 캡션을 합성한 뒤 유튜브에 즉시 올리거나 예약 업로드합니다. 서버 PC 한 대가 무인으로 돌리고, 조작과 설정은 모두 브라우저 UI 하나에서 합니다.

```
확장프로그램(수집) ─POST /feed─▶ 피드 ─가져오기─▶ 다운로드(yt-dlp) ▶ 편집(ffmpeg) ▶ 자막(whisper+AI+libass) ▶ 업로드(API / 확장프로그램)
```

## 0. 설치 파일로 시작하기 (Windows / macOS)

설치본은 Python, 웹 화면, ffmpeg/ffprobe, yt-dlp, Selenium과 음성 인식 라이브러리를 포함합니다.
사용자 PC에 Python·Node.js·ffmpeg를 따로 설치할 필요가 없습니다.
Chrome, Ollama 및 AI 모델, API 키, YouTube 로그인은 아래 안내대로 준비하세요.

| PC | 받을 파일 | 실행 방법 |
|---|---|---|
| Windows 10/11 x64 | `AutoSet-1.0.0-windows-x64-setup.exe` | 설치 후 시작 메뉴에서 AutoSet 실행 |
| Windows 무설치 | `AutoSet-1.0.0-windows-x64-portable.zip` | **전체 압축을 풀고** 폴더 안의 AutoSet.exe 실행 |
| Apple Silicon 맥 | `AutoSet-1.0.0-macos-arm64.dmg` | AutoSet.app을 Applications로 드래그한 뒤 실행 |
| Intel 맥 | `AutoSet-1.0.0-macos-x64.dmg` | AutoSet.app을 Applications로 드래그한 뒤 실행 |

파일은 빌드 PC의 `releases/` 또는 GitHub Actions의 Artifacts에 만들어집니다.
빌드 PC에서는 `releases/windows/`, `releases/macos/arm64/`, `releases/macos/x64/`로 구분합니다.
Actions에서는 **AutoSet-downloads-버전**을 받으면 Windows·macOS AppleSilicon·macOS Intel 폴더로 정리되어 있습니다.
macOS 파일은 해당 맥 또는 Actions에서 빌드해야 합니다. 맥 빌드·실행 검증 전에는 배포 완료로 보지 않습니다.
맥 빌드 기준은 macOS 15이며, 이전 macOS 버전 호환성은 별도 검증이 필요합니다.
버전 번호는 빌드 시 바꿀 수 있습니다. 자세한 방법은 [설치 파일 빌드 안내](packaging/README.md)에 있습니다.

실행하면 작은 AutoSet 창과 브라우저 관리 화면이 열립니다. 이 PC에서 자동으로 연 관리 화면은 토큰을 입력하지 않아도 됩니다.
브라우저만 닫으면 프로그램은 계속 실행되며, AutoSet 창의 **종료** 버튼 또는 창 닫기를 누르면 서버와 작업을 정리합니다.
창의 **관리 화면 열기**, **데이터 폴더**, **확장프로그램 폴더** 버튼으로 다시 열 수 있습니다.
동일 PC에서 중복 실행하면 기존 관리 화면을 엽니다. 기본 8000 포트가 사용 중이면 가까운 빈 포트를 선택합니다.
실제 주소와 포트는 AutoSet 창에 표시됩니다. 확장프로그램과 OAuth 설정에는 그 주소를 사용하세요.

| 사용자 데이터 | 위치 |
|---|---|
| Windows | `%APPDATA%\AutoSet` (탐색기 주소창에 입력) |
| macOS | `~/Library/Application Support/AutoSet` (Finder → 이동 → 폴더로 이동) |

이 폴더에 설정, DB, 영상, API 키, Chrome 계정 프로필이 저장됩니다. 업데이트·제거 시에도 데이터는 보존됩니다.
기존 소스 프로젝트의 데이터는 자동으로 옮기지 않습니다. 필요한 설정은 시스템 화면의 백업·복원으로 옮기고,
다른 PC에서는 API 키와 YouTube 계정을 다시 연결하세요. 폰이나 다른 브라우저에서는 데이터 폴더의
`config.yaml`에 있는 `ui.token`으로 로그인합니다.

### 첫 실행 설정 순서

1. AutoSet 실행 → 관리 화면이 열리는지 확인합니다.
2. 릴스 수집을 하려면 Google Chrome을 설치하고 아래 **크롬 확장프로그램 설치** 절차를 진행합니다.
3. AI를 쓰려면 아래 **Ollama·AI 연결**에서 로컬 모델 또는 클라우드 API를 준비합니다.
4. **업로드 설정**에서 A안·C안·D안을 선택하고 사용할 YouTube 계정을 연결합니다.
5. **편집 설정**에서 사용할 프리셋을 고르고 피드에서 영상 한 건으로 확인합니다.

기본 설치본의 음성 인식은 CPU를 사용할 수 있습니다. 첫 음성 인식 때 Whisper 모델을 인터넷에서 내려받으므로
처음에는 시간이 걸립니다. 이후 캐시를 사용합니다. Ollama 모델도 별도로 내려받으며 설치 파일에 포함하지 않습니다.
설치 파일은 현재 코드 서명·Apple 공증을 하지 않으므로 OS의 출처 확인 안내가 나올 수 있습니다.
macOS에서 차단되면 **시스템 설정 → 개인정보 보호 및 보안 → 확인 없이 열기**를 사용하세요.

### Ollama·AI 연결

AI가 제목·설명·캡션을 만들 때는 브라우저의 AI 확장프로그램이 필요하지 않습니다.
AutoSet 크롬 확장프로그램은 **릴스 수집과 C안 업로드**에 쓰이며, AI는 Ollama 또는 API 서버로 직접 요청합니다.

1. [Ollama 다운로드](https://ollama.com/download)에서 Windows 또는 macOS 앱을 설치하고 실행합니다.
2. PowerShell(Windows) 또는 터미널(macOS)에서 기본 모델을 내려받습니다.

```sh
ollama pull gemma4:e4b
ollama list
```

터미널 대신 **AI·프롬프트 → Ollama**의 모델 받기 입력칸에 `gemma4:e4b`를 입력하고
**모델 받기**를 눌러도 됩니다. Ollama 앱은 먼저 실행해 두세요.

3. AutoSet의 **AI·프롬프트**에서 Ollama 주소를 `http://localhost:11434`, 모델을 `gemma4:e4b`로 저장합니다.
4. **시스템**에서 Ollama가 **연결됨**이고 모델이 설치된 상태인지 확인합니다.
5. AI·프롬프트에서 영상 항목을 골라 **테스트 실행**으로 응답을 확인합니다.

Ollama 앱을 종료하면 AI 요청이 실패하므로 AutoSet을 쓰는 동안 켜 두세요.
연결이 안 되면 Ollama 실행 여부, 주소와 모델 이름을 확인합니다. 모델 설치만 안 된 경우에는 `ollama pull`을 다시 실행합니다.
PC 메모리가 부족하거나 응답이 느리면 다른 모델을 준비해 설정의 이름을 바꾸거나 클라우드 API를 사용할 수 있습니다.
Ollama 설치·모델 실행은 [공식 시작 안내](https://docs.ollama.com/quickstart)를 참고하세요.

Gemini·Anthropic을 사용하려면 각 제공업체에서 발급받은 키를 **AI·프롬프트 → API 키**에 등록하고,
프로바이더 순서와 모델을 저장하세요. Ollama를 쓰지 않는 경우 프로바이더 목록에서 제외합니다.
API 키는 사용자 데이터 폴더에 저장되며 설치본에는 다른 사람의 키가 들어 있지 않습니다.
클라우드 API 사용 요금·한도는 본인 계정에 적용됩니다.

### 크롬 확장프로그램 연결 (Windows·macOS 공통)

1. AutoSet 창의 **확장프로그램 폴더**를 누릅니다.
2. Chrome에서 `chrome://extensions` → **개발자 모드** → **압축해제된 확장 프로그램 로드**를 누르고 그 폴더를 선택합니다.
3. 확장프로그램 팝업에서 서버 주소를 AutoSet 창에 표시된 주소의 `/ui/` 앞부분으로 입력합니다. 예: `http://127.0.0.1:8000`.
4. **데이터 폴더 → config.yaml → ui.token**의 값을 복사해 팝업의 토큰에 입력하고 저장합니다. 서버 주소 권한 요청은 허용합니다.
5. 같은 Chrome에서 Instagram에 로그인하고 릴스를 열어 피드에 나타나는지 확인합니다.

Safari용 확장프로그램은 제공하지 않습니다. D안 업로드 자체에는 이 확장프로그램을 Chrome에 로드할 필요가 없으며,
**업로드 설정 → D안 → Chrome 로그인 하기**로 별도의 업로드용 Chrome 계정을 연결합니다.
C안은 확장프로그램을 설치한 Chrome에서 YouTube Studio에 로그인해야 합니다.

## 1. 소스로 설치하기 (개발자용)

| 필요한 것 | 설치 방법 |
|---|---|
| Python 3.11 이상 (3.12 권장) | `winget install Python.Python.3.12` |
| ffmpeg / ffprobe | https://www.gyan.dev/ffmpeg/builds/ 에서 받아 PATH에 추가하거나 `config.yaml`의 `paths.ffmpeg`에 경로 지정 |
| yt-dlp | `requirements.txt`에 포함됩니다 (venv). exe를 쓰려면 `paths.yt_dlp`에 경로 지정 |
| Ollama | https://ollama.com/download 에서 OS에 맞게 설치 후 `ollama pull gemma4:e4b` |
| Node.js 22 | 웹 UI를 빌드할 때만 필요 |

```bat
install.bat                   rem 처음 한 번: venv 생성, 패키지 설치, .env 생성, 웹 UI 빌드
run.bat                       rem 서버 + 워커 실행, 준비되면 관리 화면을 브라우저로 자동으로 엶
stop.bat                      rem 서버 종료 (콘솔 창을 닫거나 Ctrl+C 해도 됨)
```

- `install.bat`은 여러 번 실행해도 안전합니다 (이미 있는 venv·.env는 그대로 둠). 패키지나 웹 UI를 갱신할 때도 다시 실행하면 됩니다.
- 웹 UI 코드만 바꿨다면 `build_web.bat`만 실행해도 됩니다 (빌드 후 브라우저 Ctrl+F5).
- API 키는 `.env`를 직접 고치거나 웹 UI(AI·프롬프트 화면)에서 등록합니다.

- 첫 실행 때 `config.yaml`, `presets/edit/default.yaml`, `prompts/caption/*`, `db/pipeline.sqlite`가 만들어집니다.
- 한국어 윈도우에서는 `PYTHONUTF8=1`이 필요합니다. `run.bat`이 설정해 줍니다.
- Windows 서버는 HTTP·WebSocket 연결을 Selector 이벤트 루프로 처리하여 Python 3.12 Proactor의 연결 종료 콜백 오류를 피합니다. 서버 실행 오류는 `logs/api.log`에 남고, `run.bat`은 오류 종료 시 창을 유지합니다.
- GPU 음성 인식: CUDA를 쓸 수 있으면 faster-whisper가 GPU를 쓰고, 아니면 CPU int8로 자동 전환합니다.
  cuBLAS·cuDNN DLL 오류가 나면 `.venv\Scripts\pip install nvidia-cublas-cu12 nvidia-cudnn-cu12`를 설치하세요.
- 로그인이 필요한 릴스는 브라우저에서 내보낸 `cookies.txt`를 `config.download.cookies_file`에 지정합니다.

## 2. 웹 UI 접속과 토큰 로그인

- 주소: `http://<PC-IP>:8000/ui` (포트는 `config.yaml`의 `server.port`)
- 토큰: `config.yaml`의 `ui.token` (첫 실행 때 자동 생성). 로그인하면 쿠키로 1년간 유지됩니다.
- 같은 공유기에 연결된 폰에서도 접속할 수 있습니다. **시스템** 화면에 서버 주소와 QR 코드가 있습니다.
  윈도우 방화벽에서 해당 포트의 인바운드를 허용해야 합니다.
- 개발 중에는 `dev_web.bat`을 실행하세요. Vite(5173)가 API를 FastAPI로 프록시합니다 (`AUTOSET_PORT` 환경변수로 포트 지정).

화면 구성: 대시보드, 피드, 파이프라인, 편집 설정, AI·프롬프트, 업로드 설정, 시스템

화면을 빌드할 때 이전 메뉴 파일을 보존하여 이미 열려 있는 화면에서도 메뉴 이동을 유지합니다.
이전 파일 로딩이 실패하면 최신 화면을 한 번 다시 받아 요청한 메뉴로 이동합니다.
이미 메뉴가 열리지 않는 화면은 `Ctrl+F5`로 새로고침하세요.

## 3. 크롬 확장프로그램 설치

1. `chrome://extensions` → 개발자 모드 켜기 → **압축해제된 확장 프로그램 로드** → 설치본은 AutoSet 창의 **확장프로그램 폴더**, 소스 실행은 프로젝트의 `chrome-extension` 폴더 선택
2. 확장프로그램 팝업에서 서버 주소(`http://192.168.x.x:8000`)와 토큰을 입력하고 **저장**을 누릅니다.
   서버 주소 권한 요청이 뜨면 허용하세요. 관리 UI의 "다음 항목 업로드" 버튼이 이 권한으로 동작합니다.
3. 인스타그램 릴스(`instagram.com/reels/`)를 평소처럼 스크롤하면, 화면 가운데에 온 릴스가 피드에 기록됩니다.
   페이지 순회, 백그라운드 탭 수집, 영상 다운로드는 하지 않습니다.
   팝업의 **"영상이 끝나면 다음 릴스로 자동 넘기기"**를 켜면 한 번 다 본 릴스에서 다음 릴스로 넘어갑니다 (기본 꺼짐, 탭이 화면에 보일 때만 동작).
   화면 구조를 인식하지 못하면 팝업에 경고가 뜹니다.

## 4. 피드에서 가져오기와 예약

- 카드마다 **편집 프리셋**과 **업로드 시각**을 고른 뒤 **가져오기**를 누릅니다.
  - 업로드 시각: 즉시 / 다음 예약 슬롯 / 1시간 후 / 3시간 후 / 오늘 18:00 / 내일 09:00 / 직접 입력 (서버 시간대 기준, 과거 시각은 고를 수 없음)
- 여러 장을 체크하고 **일괄 가져오기**를 할 수 있습니다. **슬롯에 순차 배정**을 켜면 체크한 순서대로 빈 슬롯을 하나씩 배정합니다.
- 가져온 카드는 4칸 진행 바(다운로드 → 편집 → 자막 → 업로드)로 바뀌고 실시간으로 갱신됩니다.
- 예약 배지를 누르면 업로드가 시작되기 전까지 시각을 바꾸거나 즉시로 전환할 수 있습니다.

게시 방식은 다음과 같습니다.
- 즉시: 편집·자막이 끝나는 대로 업로드하고 기본 공개 범위로 바로 게시합니다.
- 예약: 편집·자막이 끝나는 대로 **비공개 + 예약 시각(publishAt)**으로 업로드합니다. 실제 공개는 유튜브가 그 시각에 처리합니다.
- 업로드 차례가 왔을 때 예약 시각이 이미 지났으면 즉시 게시로 전환하고 이벤트 로그에 남깁니다.
- 예약 슬롯은 **업로드 설정 → 예약 슬롯**에서 요일별로 편집합니다. 직접 입력한 시각은 슬롯을 차지하지 않습니다.

## 5. 편집 프리셋

`presets/edit/{이름}.yaml`에 프리셋 하나당 파일 하나로 저장됩니다. **편집 설정** 화면에서 만들기, 복제, 이름 변경, 삭제, 기본 지정을 할 수 있습니다 (default는 삭제 불가).

- 적용 순서는 고정입니다: 앞부분 자르기 → 속도(음높이 유지 atempo) → 확대 후 중앙 크롭 → 좌우반전 → 워터마크 → 인트로·아웃트로
- 출력은 1080x1920, h264/aac, 30fps입니다.
- 자막 설정: mode(summary/speech/both), 위치(lower_safe/upper/center), 하단 여백(20~40%), 표시 구간, 폰트, 색, 외곽선, 배경 박스, 페이드
- **미리보기**: 선택한 항목에 화면의 값(저장 전 값 포함)을 적용해 5초 샘플을 만듭니다.
- 편집을 시작하면 프리셋 값이 항목의 `edit_params`에 스냅샷으로 저장됩니다. 그래서 설정을 바꿔도 이미 처리된 항목은 바뀌지 않습니다.
  새 설정을 적용하려면 파이프라인 상세 화면에서 **편집 다시 실행**을 누르세요 (자막까지 이어서 다시 실행됩니다).

## 6. AI 프롬프트 수정

`prompts/{프로필}/` 폴더에 네 파일이 있습니다.

| 파일 | 역할 |
|---|---|
| `system.md` | 역할과 작성 규칙 (제목·설명·캡션·태그 기준) |
| `user.md` | 영상 정보를 끼워 넣는 요청 템플릿 |
| `schema.json` | 출력 JSON 스키마. Ollama `format`과 Gemini `response_schema`로 그대로 전달되고 검증에도 쓰입니다 |
| `examples.md` | (선택) 좋은 예시. 내용이 있으면 system 뒤에 `## 예시`로 붙습니다 |

매 AI 호출마다 파일을 새로 읽으므로 직접 수정해도 재시작할 필요가 없습니다. **AI·프롬프트** 화면에서 편집, 버전 비교, 되돌리기, **테스트 실행**(항목에 저장하지 않음)을 할 수 있습니다.

사용할 수 있는 변수:

| 변수 | 값 |
|---|---|
| `{{duration_sec}}` | 편집본 길이(초, 소수 1자리) |
| `{{frame_count}}` | 첨부 프레임 수 |
| `{{has_speech}}` | "있음" / "없음" |
| `{{transcript}}` | 전사 텍스트 (잘린 앞부분 제외, 2,000자 넘으면 중간 생략, 없으면 "(음성 없음)") |
| `{{original_caption}}` | 원본 캡션 (없으면 "(없음)") |
| `{{author}}` | 원본 작성자 핸들 |
| `{{tone}}` | `config.ai.style.tone` |
| `{{banned_words}}` | 금지어 쉼표 연결 (없으면 "(없음)") |
| `{{extra_instruction}}` | "다시 생성"할 때 입력한 추가 지시 |
| `{{today}}` | 서버 로컬 날짜 |

AI 처리 규칙은 다음과 같습니다.
- `config.ai.providers` 순서(기본 ollama → gemini)대로 시도합니다.
- 응답이 JSON 파싱, 스키마, 길이, 금지어 검사를 통과하지 못하면 같은 프로바이더에 1회씩 다시 요청하고, 그래도 안 되면 다음 프로바이더로 넘어갑니다.
- `confidence=low`이고 저신뢰 보류가 켜져 있으면 **검토 대기**로 둡니다. 승인하기 전에는 업로드하지 않습니다.
- Gemini는 429 응답을 받으면 분당 한도일 때 60초 뒤 1회 다시 시도하고, 일일 한도일 때 태평양 시간 자정까지 건너뜁니다.

## 7. 업로드

### A안: YouTube Data API (기본, 권장)
1. Google Cloud Console에서 프로젝트를 만들고 **YouTube Data API v3**를 사용 설정합니다.
2. OAuth 동의 화면을 설정합니다 (테스트 사용자에 본인 계정 추가).
3. 사용자 인증 정보에서 **OAuth 클라이언트 ID**를 만듭니다. 유형은 **웹 애플리케이션**, 승인된 리디렉션 URI는 `http://localhost:8000/auth/youtube/callback`입니다 (포트는 설정에 맞춤).
4. 받은 JSON을 `secrets/client_secret.json`으로 저장합니다.
5. **서버 PC의 브라우저**에서 `http://localhost:8000/ui` → 업로드 설정 → **연결하기**를 누릅니다. 토큰은 `secrets/youtube_token.json`에 저장됩니다.

- 기본 쿼터는 하루 10,000 단위이고 업로드 한 번에 1,600 단위를 쓰므로, 하루 약 6건이 기본 상한(`upload.daily_limit`)입니다.
  상한을 넘은 항목은 다음날로 미루고, 예약 시각이 빠른 항목부터 처리합니다.
- 계정이 연결되기 전에는 업로드 워커가 항목을 실패시키지 않고 기다립니다.

### C안: 크롬 확장프로그램 (보조)
- 업로드 설정에서 모드를 **확장프로그램**으로 바꿉니다.
- 크롬에서 YouTube에 로그인하고 **YouTube 스튜디오 탭을 열어 둔 상태**여야 합니다.
- 팝업이나 웹 UI의 **다음 항목 업로드**를 누르면 한 건만 처리하고 멈춥니다 (자동 반복 없음).
- 로그인 재확인, 저작권 안내 같은 예상하지 못한 대화상자가 뜨면 닫지 않고 즉시 중단해 오류로 보고합니다.
- 스튜디오 화면이 바뀌면 업로드 설정에서 `selectors.json`을 고칩니다. 예약 날짜·시각 입력 형식은 `config.upload.extension.date_format` / `time_format`으로 맞춥니다 (기본값은 한국어 스튜디오 기준).
- ⚠ **YouTube 약관상 자동화 도구 사용은 계정 제재 사유가 될 수 있습니다.** A안을 기본으로 쓰고, C안은 쿼터가 부족할 때만 보조로 쓰세요.

### D안: Selenium + undetected-chromedriver

1. Google Chrome을 설치합니다. 설치본에는 Selenium 의존성이 포함됩니다. 소스 실행이라면 `.venv\Scripts\python -m pip install -r requirements.txt`로 설치하고 `build_web.bat`로 화면을 빌드한 뒤 서버를 재시작합니다.
2. **업로드 설정 → D안 · Selenium + undetected-chromedriver**를 선택하고 저장합니다. 기존 A안·C안도 계속 선택할 수 있습니다.
3. **Chrome 로그인 하기**를 누르고 **서버 PC의 Chrome 창**에서 YouTube에 로그인한 뒤 사용할 채널을 선택합니다. 설정 화면의 **로그인 완료 · 창 닫기**를 누르면 채널 ID가 확인되고 연결이 저장됩니다. 영상 대기열이 없어도 로그인할 수 있고, 창을 닫았으면 버튼으로 다시 열 수 있습니다 (수동 로그인 창은 최소 10분 대기).
4. 여러 계정은 **+ 계정 추가 → 이름 입력 → 저장 → Chrome 로그인 하기 → 로그인 완료** 순서로 각각 연결합니다. 각 계정은 다른 Chrome 프로필을 사용합니다. **업로드할 계정**을 선택하고 저장하면 다음 대기 영상부터 그 계정으로 업로드합니다. 진행 중인 영상은 처음 선택한 계정을 계속 사용합니다.
5. 업로드 워커가 실행 중이면 로그인 확인된 계정으로 검토를 마친 대기 항목을 순서대로 업로드합니다. 로그인 단계에서 창을 닫거나 시간이 지나 실패했던 영상은 로그인 완료 시 다시 대기합니다. 게시 버튼을 누른 뒤 결과가 불확실한 영상은 자동 재시도하지 않습니다.
6. 기존 `.browser/selenium` 프로필은 기본 계정으로 유지합니다. 계정 목록에서 제거해도 프로필 폴더는 삭제하지 않습니다. 화면에서 프로필 경로·Chrome 실행 파일·주 버전·대기 제한을 바꿀 수 있습니다.

- 제목·태그는 글자마다 `time.sleep(random.uniform(0.05, 0.1))` 후 입력합니다. 설명은 클립보드에 복사한 뒤 Windows는 `Ctrl+V`, macOS는 `Command+V`로 한 번에 붙여넣으며 줄바꿈·이모지를 유지합니다. 입력한 제목·설명이 실제 화면 내용과 일치하는지도 검사합니다.
- 버튼과 입력칸 클릭 전에는 `time.sleep(random.uniform(1.0, 2.5))`로 기다립니다.
- 원본 출처·태그·아동용 설정·공개 범위·예약 게시를 적용합니다. 카테고리·언어 설정은 A안용이며, D안은 Studio의 기본값을 사용합니다.
- 예약 날짜·시각 형식은 `upload.selenium.date_format` / `time_format` / `ampm`으로 설정합니다. 기본값은 한국어 Studio 기준입니다.
- C안과 같은 `chrome-extension/upload/selectors.json`을 사용합니다. Studio 화면이 바뀌면 업로드 설정에서 수정합니다.
- 게시 완료 대화상자를 확인한 뒤에만 항목을 완료 처리합니다. 게시 클릭 이후 오류·중지·서버 종료가 발생하면 실패 상태로 남깁니다. 스튜디오에서 게시 여부를 확인하고 재시도하세요.
- D안은 Chrome 창을 표시할 수 있는 데스크톱 세션에서 실행합니다. ChromeDriver는 라이브러리가 처음 실행할 때 내려받습니다.
- Apple Silicon 맥에서는 ARM64 ChromeDriver를 별도 캐시에 내려받고, Intel 맥에서는 x64 드라이버를 사용합니다.
- D안 Chrome은 시작할 때 최대화해서 화면 앞으로 표시합니다. 로그인 창도 동일하게 열리며, 이후에는 직접 최소화하거나 다른 창으로 전환할 수 있습니다.
- 로그인 창과 D안 업로드는 동시에 실행하지 않습니다. 업로드 중에는 완료 후 로그인 창을 열 수 있습니다. 연결된 채널과 실제 업로드 채널이 다르면 게시를 중단하고 다시 로그인을 요청합니다.

구현 참고: [Selenium 요소 입력·클릭](https://www.selenium.dev/documentation/webdriver/elements/interactions/), [undetected-chromedriver 프로필 설정](https://github.com/ultrafunkamsterdam/undetected-chromedriver).

## 8. 설정 저장·이력·백업

- 모든 설정은 서버 파일에 저장됩니다 (브라우저 저장소를 쓰지 않음): `config.yaml`, `presets/edit/*.yaml`, `prompts/*/`
- 파일은 임시 파일에 먼저 쓴 뒤 교체합니다. 저장 중 전원이 꺼져도 파일이 깨지지 않습니다.
- 저장할 때마다 이전 파일을 `.history/`, `presets/.history/`, `prompts/.history/`에 타임스탬프를 붙여 백업합니다 (`history.keep`, 기본 50개).
  UI에서 비교하고 되돌릴 수 있습니다.
- 워커는 매 항목마다 설정 파일을 다시 읽으므로 재시작하지 않아도 새로 처리되는 항목부터 반영됩니다.
- **시스템 → 백업**은 DB, `config.yaml`, presets, prompts, assets를 zip으로 내려받습니다. zip을 업로드하면 복원하며, 복원 직전 상태는 `.history/pre-restore-*.zip`으로 남습니다.

## 9. 무인 운영 (윈도우)

**절전 모드 해제** (관리자 명령 프롬프트):
```bat
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change monitor-timeout-ac 10
```

**NSSM으로 윈도우 서비스 등록** (https://nssm.cc):
```bat
nssm install AutoSet "E:\AutoSet\.venv\Scripts\python.exe" "-m src.main"
nssm set AutoSet AppDirectory E:\AutoSet
nssm set AutoSet AppEnvironmentExtra PYTHONUTF8=1
nssm set AutoSet AppStdout E:\AutoSet\logs\service.log
nssm set AutoSet AppStderr E:\AutoSet\logs\service.log
nssm set AutoSet Start SERVICE_AUTO_START
nssm start AutoSet
```
서비스 계정에서 Ollama에 접속할 수 있어야 합니다 (Ollama도 부팅 시 자동 시작).

## 10. 실행 방법

```bat
run.bat                                    rem API + 모든 워커 (한 프로세스)
.venv\Scripts\python -m src.workers.editor rem 워커 하나만 단독 실행 (downloader / editor / captioner / uploader)
.venv\Scripts\python -m pytest tests -q    rem 테스트
```
워커는 대시보드에서 하나씩 시작하거나 정지할 수 있습니다. 서버가 비정상 종료되면 진행 중이던 항목을 다음 시작 때 이전 단계로 되돌립니다.

## 폴더

```
config.yaml  presets/edit/  prompts/caption/  .history/
chrome-extension/  (manifest, background, popup, bridge, collect/, upload/selectors.json)
web/  (Vue 3 + Vite + Tailwind → web/dist 를 /ui 로 서빙)
inbox/ 원본   work/ 편집본·캐시   ready/ 완성본   done/ 업로드 완료
db/pipeline.sqlite   logs/워커별.log   secrets/ OAuth
src/ api.py main.py routes/ workers/ ai/ storage.py scheduler.py events.py models.py config.py
```
