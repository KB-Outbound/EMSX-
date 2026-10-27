"""앱을 가져오기 전에 번들 내부 Bloomberg DLL 검색 경로를 등록한다."""

import os
from pathlib import Path
import sys

# 핸들을 유지해야 등록한 DLL 디렉터리가 실행 중 계속 유효하다.
_emsx_dll_handles = []
if sys.platform == "win32" and hasattr(sys, "_MEIPASS"):
    root = Path(sys._MEIPASS)
    for directory in (root, root / "blpapi", root / "bloomberg"):
        if directory.is_dir():
            _emsx_dll_handles.append(os.add_dll_directory(str(directory)))
