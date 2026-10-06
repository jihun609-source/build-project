"""run.bat 에서 쓰는 서버 포트 출력 (config.yaml 이 없으면 기본값)."""
from . import config as C

print(int(C.get_config()["server"].get("port", 8000)))
