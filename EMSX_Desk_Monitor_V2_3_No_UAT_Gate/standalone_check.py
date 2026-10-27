"""Bloomberg 연결 없이 배포 파일의 네이티브 모듈과 자산을 검사한다."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys
import traceback
import wave
from resource_paths import asset_path


def run_check(report: str) -> int:
    result: dict[str, object] = {
        "성공": False,
        "독립실행": bool(getattr(sys, "frozen", False)),
    }
    root = None
    try:
        import blpapi
        import tkinter as tk
        import winsound

        if not callable(winsound.PlaySound):
            raise RuntimeError("Windows 음향 API를 사용할 수 없습니다")
        result["음향 API"] = True
        result["BLPAPI"] = blpapi.version()
        result["BLPAPI_C++"] = blpapi.cpp_sdk_version()
        result["SQLite"] = sqlite3.sqlite_version
        root = tk.Tk()
        root.withdraw()
        result["Tk"] = root.tk.call("info", "patchlevel")
        with wave.open(str(asset_path("desk_alert.wav")), "rb") as source:
            assert source.getnframes() > 0
        result["경보음"] = "파일 해석 성공 · 실제 음향 재생은 별도 확인"
        result["성공"] = True
    except Exception:
        result["오류"] = traceback.format_exc()
    finally:
        if root is not None:
            root.destroy()
    Path(report).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0 if result["성공"] else 1
