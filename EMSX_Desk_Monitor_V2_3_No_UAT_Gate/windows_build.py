"""Windows 빌드·오프라인 설치를 배치 인코딩과 무관하게 실행한다."""

from __future__ import annotations

import argparse
import importlib
import json
import re
from pathlib import Path
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
BLOOMBERG_INDEX = "https://blpapi.bloomberg.com/repository/releases/python/simple"
BINARY_NAME = "KB증권 해외주식영업부 EMSX 모니터.exe"


def run(arguments: list[str], *, stdout: object = None) -> None:
    """셸 문자열을 조합하지 않고 현재 Python과 정확한 인수 목록을 사용한다."""
    subprocess.run(arguments, cwd=ROOT, check=True, stdout=stdout)


def python_arguments(*arguments: str) -> list[str]:
    """자식 Python에도 UTF-8 모드를 명시하여 Windows 로캘과 분리한다."""
    return [sys.executable, "-X", "utf8", *arguments]


def pip_arguments(*arguments: str) -> list[str]:
    return python_arguments("-m", "pip", "--disable-pip-version-check", *arguments)


def download() -> None:
    """PyInstaller와 Bloomberg 공식 wheel을 연결 가능한 준비 PC에서만 내려받는다."""
    wheelhouse = ROOT / "wheelhouse"
    wheelhouse.mkdir(exist_ok=True)
    requirements = [
        line.strip()
        for line in (ROOT / "requirements.txt")
        .read_text(encoding="utf-8-sig")
        .splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    bloomberg = [
        item
        for item in requirements
        if re.match(r"^blpapi(?:\s|[\[<>=!~;@]|$)", item, re.IGNORECASE)
    ]
    others = [item for item in requirements if item not in bloomberg]
    if others:
        run(
            pip_arguments(
                "download", "--only-binary=:all:", "--dest", str(wheelhouse), *others
            )
        )
    if bloomberg:
        run(
            pip_arguments(
                "download",
                "--only-binary=:all:",
                "--dest",
                str(wheelhouse),
                "--index-url",
                BLOOMBERG_INDEX,
                *bloomberg,
            )
        )
    print("다운로드 완료. 이 wheelhouse와 같은 Python 버전·x64 환경에서 설치하십시오.")


def prepare() -> None:
    """외부 인덱스 없이 반입된 wheel만 설치하고 의존성 일관성을 확인한다."""
    wheelhouse = ROOT / "wheelhouse"
    if not wheelhouse.is_dir() or not any(wheelhouse.glob("*.whl")):
        raise RuntimeError("wheelhouse에 승인된 Windows x64 wheel을 준비하십시오")
    run(
        pip_arguments(
            "install",
            "--no-index",
            "--only-binary=:all:",
            "--find-links",
            str(wheelhouse),
            "-r",
            str(ROOT / "requirements.txt"),
        )
    )
    run(pip_arguments("check"))
    print("오프라인 의존성 설치·검사 완료")


def check_executable(executable: Path, report: Path) -> dict[str, object]:
    """비정상 종료라도 보고서를 먼저 읽어 원래 예외와 단계를 출력한다."""
    report.unlink(missing_ok=True)
    try:
        process = subprocess.run(
            [str(executable), "--self-test", str(report)],
            cwd=ROOT,
            check=False,
            timeout=180,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"자체 검사 시간 초과. 검사 보고서·오류 창을 확인하십시오: {report}"
        ) from exc
    result: dict[str, object] = {}
    if report.is_file():
        try:
            result = json.loads(report.read_text(encoding="utf-8-sig"))
            if not isinstance(result, dict):
                raise ValueError("보고서가 객체 형식이 아닙니다")
        except (ValueError, OSError) as exc:
            raise RuntimeError(f"자체 검사 보고서 해석 실패: {report}: {exc}") from exc
        print(f"자체 검사 단계: {result.get('단계', '이전 버전 보고서')}")
        if result.get("오류"):
            print(str(result["오류"]), file=sys.stderr)
    else:
        raise RuntimeError(
            f"자체 검사 보고서가 생성되지 않았습니다. 종료 코드={process.returncode}. "
            "실행 파일의 시작 오류 창과 DLL·런타임 훅을 확인하십시오"
        )
    if (
        process.returncode != 0
        or result.get("성공") is not True
        or result.get("독립실행") is not True
    ):
        raise RuntimeError(
            f"독립 실행 검사 실패: 종료 코드={process.returncode}, 보고서={report}"
        )
    return result


def build() -> None:
    """사전 검사부터 완성 파일 자체 검사까지 오류를 즉시 전달한다."""
    for name in ("tkinter", "blpapi", "PyInstaller"):
        importlib.import_module(name)
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    root.destroy()
    run(python_arguments("-m", "unittest", "discover", "-s", str(ROOT / "tests"), "-q"))
    run(
        python_arguments(
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            str(ROOT / "EMSX_Monitor.spec"),
        )
    )
    output = ROOT / "dist"
    executable = output / BINARY_NAME
    if not executable.is_file():
        raise RuntimeError("실행 파일 출력이 없습니다")
    report = output / "패키지_검증.json"
    check_executable(executable, report)
    with (output / "빌드_패키지_목록.txt").open("w", encoding="utf-8") as stream:
        run(pip_arguments("freeze"), stdout=stream)
    print(f"빌드·독립 실행 검사 완료: {executable}")


def main() -> int:
    parser = argparse.ArgumentParser(description="오프라인 Windows 패키징 도구")
    parser.add_argument(
        "action",
        choices=("download", "prepare", "build"),
        help="다운로드·오프라인 설치·빌드 중 수행할 작업",
    )
    args = parser.parse_args()
    if sys.platform != "win32" or struct.calcsize("P") != 8:
        print(
            "오류: 대상과 호환되는 Windows x64 Python을 사용하십시오.", file=sys.stderr
        )
        return 1
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    print(
        f"사용 Python: {sys.executable}\n버전: {sys.version.split()[0]}\n프로젝트: {ROOT}",
        flush=True,
    )
    try:
        {"download": download, "prepare": prepare, "build": build}[args.action]()
        return 0
    except subprocess.CalledProcessError as exc:
        print(
            f"명령 실행 실패: 종료 코드 {exc.returncode}. 위의 원본 오류를 확인하십시오.",
            file=sys.stderr,
        )
        return exc.returncode if exc.returncode > 0 else 1
    except Exception as exc:
        print(f"오류: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
