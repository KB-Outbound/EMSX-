"""소스 실행과 단일 실행 파일의 읽기 전용 자산 경로를 통일한다."""

from pathlib import Path
import sys


def asset_path(name: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root / "assets" / name
