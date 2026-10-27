"""자체 검사는 UI 모듈보다 먼저 시작하여 초기 가져오기 오류도 보고한다."""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        parser = argparse.ArgumentParser(description="독립 실행 패키지 검사")
        parser.add_argument("--self-test", required=True, metavar="보고서")
        parser.add_argument(
            "--demo", action="store_true", help="자체 검사에서는 무시합니다"
        )
        args = parser.parse_args()
        from standalone_check import run_check

        return run_check(args.self_test)
    from emsx_desk_monitor import main as ui_main

    ui_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
