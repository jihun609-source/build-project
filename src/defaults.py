"""최초 실행 시 생성하는 기본 파일 내용."""
import sys
from pathlib import Path


def default_font() -> str:
    """운영체제별 기본 한글 굵은 폰트."""
    if sys.platform == "darwin":
        cands = ["/System/Library/Fonts/AppleSDGothicNeo.ttc", "/Library/Fonts/AppleGothic.ttf",
                 "/System/Library/Fonts/Supplemental/AppleGothic.ttf"]
    elif sys.platform == "win32":
        cands = ["C:/Windows/Fonts/malgunbd.ttf", "C:/Windows/Fonts/malgun.ttf"]
    else:
        cands = ["/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
                 "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"]
    for c in cands:
        if Path(c).exists():
            return c
    return cands[0]


DEFAULT_PRESET = {
    "name": "default",
    "video": {
        "trim_start_sec": 0,
        "speed": 1.0,
        "keep_audio": True,
        "scale_factor": 1.0,
        "hflip": False,
        "intro_path": None,
        "outro_path": None,
        "watermark_path": None,
        "watermark_position": "bottom_right",
    },
    "caption": {
        "mode": "summary",
        "position": "lower_safe",
        "bottom_margin_ratio": 0.25,
        "start_sec": None,
        "end_sec": None,
        "font_path": default_font(),
        "font_size": 64,
        "min_font_size": 44,
        "color": "#FFFFFF",
        "outline_color": "#000000",
        "outline_width": 4,
        "box": True,
        "box_color": "#000000",
        "box_opacity": 0.45,
        "max_width_ratio": 0.85,
        "side_margin_ratio": 0.037,
        "max_lines": 6,
        "fade_ms": 200,
    },
}

SYSTEM_MD = """너는 한국어 유튜브 쇼츠 채널의 편집자다.
제공되는 프레임 이미지, 음성 전사, 원본 캡션을 보고 영상 내용을 파악한 뒤
영상 하단에 넣을 캡션과 업로드용 제목·설명·태그를 작성한다.

## 공통 원칙
- 프레임과 전사에서 확인되는 내용만 쓴다. 보이지 않거나 들리지 않는 사실은 추측해 쓰지 않는다.
- 원본 캡션은 참고만 하고 문장을 그대로 베끼지 않는다.
- 영상 내용을 제대로 파악하지 못했으면 confidence를 low로 한다.
- 출력은 지정된 JSON 하나만 쓴다. 설명, 마크다운, 코드블록을 붙이지 않는다.

## 하단 캡션 (caption)
- 영상을 처음 보는 사람이 1초 안에 상황을 이해할 수 있는 문장
- 한 줄 16자 이내, 최대 2줄, 줄바꿈은 \\n 으로 표시
- 이모지와 특수기호는 쓰지 않는다

## 요약 (summary)
- 영상 내용을 1~2문장으로 사실대로 정리한다 (내부 기록용)

## 제목 (title)
- 30자 이내
- 핵심 대상 + 상황이나 반전이 드러나게 쓴다
- "충격", "경악", "무조건", "역대급" 같은 과장·낚시 표현은 쓰지 않는다
- 물음표와 느낌표는 합쳐서 최대 1개

## 설명 (description)
- 2~3문장, 첫 문장에 영상의 핵심을 쓴다
- 링크, 연락처, 해시태그는 넣지 않는다

## 태그 (tags)
- 정확히 5개, # 없이
- 영상 내용과 직접 관련된 한국어 단어 위주

## 톤
{{tone}}

## 사용 금지 단어
{{banned_words}}
"""

USER_MD = """아래는 편집할 영상의 정보다.

- 길이: {{duration_sec}}초
- 음성: {{has_speech}}
- 원본 작성자: {{author}}
- 원본 캡션: {{original_caption}}

[음성 전사]
{{transcript}}

첨부한 이미지는 영상에서 시간 순서대로 뽑은 프레임 {{frame_count}}장이다.

{{extra_instruction}}

규칙에 맞춰 지정된 JSON으로만 답하라.
"""

SCHEMA_JSON = """{
  "type": "object",
  "properties": {
    "caption":     { "type": "string", "maxLength": 34 },
    "summary":     { "type": "string", "maxLength": 200 },
    "title":       { "type": "string", "maxLength": 30 },
    "description": { "type": "string", "maxLength": 300 },
    "tags":        { "type": "array", "items": { "type": "string", "maxLength": 20 },
                     "minItems": 5, "maxItems": 5 },
    "confidence":  { "type": "string", "enum": ["high", "medium", "low"] }
  },
  "required": ["caption", "summary", "title", "description", "tags", "confidence"]
}
"""

EXAMPLES_MD = """<!--
좋은 결과 예시를 여기에 적으면 system 프롬프트 뒤에 "## 예시" 제목과 함께 자동으로 붙는다.
이 주석만 있으면 붙이지 않는다.
-->
"""

DEFAULT_PROMPT_FILES = {
    "system.md": SYSTEM_MD,
    "user.md": USER_MD,
    "schema.json": SCHEMA_JSON,
    "examples.md": EXAMPLES_MD,
}
