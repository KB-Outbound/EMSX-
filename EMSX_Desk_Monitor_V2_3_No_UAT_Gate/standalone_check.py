"""Bloomberg 연결 없이 배포 파일의 네이티브 모듈과 자산을 검사한다."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import traceback

from resource_paths import asset_path


def run_check(report: str) -> int:
    result: dict[str, object] = {
        "성공": False,
        "독립실행": bool(getattr(sys, "frozen", False)),
    }
    target = Path(report)
    root = None

    def phase(name: str) -> None:
        """네이티브 코드가 비정상 종료되어도 마지막 검사 단계를 남긴다."""
        result["단계"] = name
        target.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    try:
        phase("BLPAPI 가져오기")
        import blpapi

        result["BLPAPI"] = blpapi.version()
        phase("BLPAPI C++ 네이티브 객체 검사")
        options = blpapi.SessionOptions()
        options.setServerHost("localhost")
        options.setServerPort(8194)
        del options
        result["네이티브 API"] = True
        phase("BLPAPI C++ 버전 조회")
        version_function = getattr(blpapi, "cpp_sdk_version", None)
        if callable(version_function):
            try:
                result["BLPAPI_C++"] = version_function()
            except Exception:
                result["BLPAPI_C++"] = "버전 조회 불가 · 네이티브 객체 검사는 성공"
                result["버전 조회 경고"] = traceback.format_exc()
        else:
            result["BLPAPI_C++"] = "버전 조회 함수 미제공 · 네이티브 객체 검사는 성공"
        phase("SQLite 가져오기")
        import sqlite3

        result["SQLite"] = sqlite3.sqlite_version
        phase("Windows 음향 API 가져오기")
        import winsound

        if not callable(winsound.PlaySound):
            raise RuntimeError("Windows 음향 API를 사용할 수 없습니다")
        result["음향 API"] = True
        phase("Tk 초기화")
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        result["Tk"] = root.tk.call("info", "patchlevel")
        phase("경보음 파일 해석")
        import wave

        with wave.open(str(asset_path("desk_alert.wav")), "rb") as source:
            if source.getnframes() <= 0:
                raise RuntimeError("경보음 데이터가 없습니다")
        result["경보음"] = "파일 해석 성공 · 실제 음향 재생은 별도 확인"
        phase("시장 캘린더 해석")
        from market_sessions import MarketCalendar

        calendar = MarketCalendar.load()
        result["시장 캘린더"] = f"{len(calendar.markets)}개 시장 · {calendar.source}"
        phase("Tk 종료")
        root.destroy()
        root = None
        result["성공"] = True
        phase("완료")
    except Exception:
        result["오류"] = traceback.format_exc()
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                result["성공"] = False
                result["오류"] = str(result.get("오류", "")) + traceback.format_exc()
        target.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 0 if result["성공"] else 1
