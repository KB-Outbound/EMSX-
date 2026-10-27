"""SDK가 실제 로드한 ffiutils의 ABI 파일명을 PyInstaller 번들에 보존한다."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def ffiutils_entry(internals: Any) -> tuple[str, str, str]:
    """SDK의 ffiutils.*.pyd 검색 규칙에 맞는 원본 파일을 선택한다."""
    package = Path(internals.__file__).resolve().parent
    loaded = getattr(getattr(internals, "libffastcalls", None), "_name", None)
    if loaded is not None:
        source = Path(os.fsdecode(loaded))
        if not source.is_absolute():
            source = package / source
        source = source.resolve()
    else:
        candidates = list(package.glob("ffiutils.*.pyd"))
        if len(candidates) != 1:
            raise RuntimeError(
                "로드된 ffiutils를 확인할 수 없습니다. 정상 SDK와 ABI 파일명을 확인하십시오"
            )
        source = candidates[0].resolve()
    if not source.is_file() or source.parent != package:
        raise RuntimeError(
            "설치된 blpapi 패키지에서 실제 ffiutils 파일을 찾지 못했습니다"
        )
    name = source.name.lower()
    if (
        not name.startswith("ffiutils.")
        or name == "ffiutils.pyd"
        or not name.endswith(".pyd")
    ):
        raise RuntimeError("SDK 검색 규칙 ffiutils.*.pyd에 맞지 않는 파일명입니다")
    # EXTENSION이면 파일명이 다시 정규화될 수 있으므로 명시적 BINARY로 보존한다.
    return f"blpapi/{source.name}", str(source), "BINARY"


def preserve_ffiutils(
    binaries: list[tuple[str, str, str]], entry: tuple[str, str, str]
) -> list[tuple[str, str, str]]:
    """Analysis 이후에 원본 이름을 넣어 정규화된 모듈명과 별도로 유지한다."""
    destination = entry[0].replace("\\", "/").casefold()
    return [
        item
        for item in binaries
        if item[0].replace("\\", "/").casefold() != destination
    ] + [entry]
