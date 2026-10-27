"""CMD 호환 형식·패키지 출처·오프라인 설치·실패 종료 코드를 검증한다."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import windows_build as builder


class TestWindowsBuild(unittest.TestCase):
    def test_batch_launchers_are_ascii_crlf_without_python_inline_code(self) -> None:
        for name in (
            "build_windows.bat",
            "prepare_offline.bat",
            "download_dependencies.bat",
        ):
            with self.subTest(name=name):
                content = (builder.ROOT / name).read_bytes()
                content.decode("ascii")
                self.assertTrue(content.endswith(b"\r\n"))
                self.assertEqual(content.count(b"\n"), content.count(b"\r\n"))
                self.assertNotIn(b" -c ", content)
                self.assertIn(b"windows_build.py", content)
        self.assertIn(
            "*.bat -text", (builder.ROOT / ".gitattributes").read_text(encoding="utf-8")
        )

    def test_download_separates_official_bloomberg_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "requirements.txt").write_text(
                "blpapi>=3.0\npyinstaller\n", encoding="utf-8"
            )
            with (
                patch.object(builder, "ROOT", root),
                patch.object(builder, "run") as run,
                redirect_stdout(io.StringIO()),
            ):
                builder.download()
            calls = [call.args[0] for call in run.call_args_list]
            self.assertEqual(len(calls), 2)
            self.assertIn("pyinstaller", calls[0])
            self.assertNotIn("--index-url", calls[0])
            self.assertIn(builder.BLOOMBERG_INDEX, calls[1])
            self.assertIn("blpapi>=3.0", calls[1])
            self.assertTrue((root / "wheelhouse").is_dir())

    def test_prepare_never_uses_an_online_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "wheelhouse").mkdir()
            (root / "wheelhouse" / "mock.whl").write_bytes(b"")
            with (
                patch.object(builder, "ROOT", root),
                patch.object(builder, "run") as run,
                redirect_stdout(io.StringIO()),
            ):
                builder.prepare()
            command = run.call_args_list[0].args[0]
            self.assertIn("--no-index", command)
            self.assertIn("--only-binary=:all:", command)
            self.assertIn("--disable-pip-version-check", command)
            self.assertEqual(run.call_args_list[1].args[0][-1], "check")

    def test_child_failure_preserves_nonzero_exit_code(self) -> None:
        with (
            patch.object(sys, "argv", ["windows_build.py", "prepare"]),
            patch.object(sys, "platform", "win32"),
            patch.object(builder.struct, "calcsize", return_value=8),
            patch.object(
                builder,
                "prepare",
                side_effect=subprocess.CalledProcessError(7, ["모의 명령"]),
            ),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(builder.main(), 7)

    def test_wrong_platform_rejected_before_install_or_build(self) -> None:
        with (
            patch.object(sys, "argv", ["windows_build.py", "build"]),
            patch.object(sys, "platform", "linux"),
            patch.object(builder, "build") as build,
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(builder.main(), 1)
            build.assert_not_called()

    def test_child_python_reads_korean_file_in_utf8_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "한글.txt"
            path.write_text("한글 검증", encoding="utf-8")
            command = builder.python_arguments(
                "-c",
                "import sys; from pathlib import Path; assert sys.flags.utf8_mode == 1; "
                "print(Path(sys.argv[1]).read_text())",
                str(path),
            )
            result = subprocess.run(
                command, check=True, capture_output=True, text=True, encoding="utf-8"
            )
            self.assertEqual(result.stdout.strip(), "한글 검증")

    def test_utf8_bom_requirements_comment_is_not_a_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "requirements.txt").write_text(
                "# 한글 주석\nblpapi\npyinstaller\n", encoding="utf-8-sig"
            )
            with (
                patch.object(builder, "ROOT", root),
                patch.object(builder, "run") as run,
                redirect_stdout(io.StringIO()),
            ):
                builder.download()
            commands = [call.args[0] for call in run.call_args_list]
            self.assertEqual(len(commands), 2)
            self.assertIn("pyinstaller", commands[0])
            self.assertIn("blpapi", commands[1])
            self.assertFalse(
                any("주석" in part for command in commands for part in command)
            )

    def test_nonzero_self_test_displays_underlying_report_error(self) -> None:
        import json
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "검사.json"

            def process(*args: object, **kwargs: object) -> SimpleNamespace:
                report.write_text(
                    json.dumps(
                        {
                            "성공": False,
                            "독립실행": True,
                            "단계": "BLPAPI 가져오기",
                            "오류": "모의 DLL 오류 원문",
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                return SimpleNamespace(returncode=1)

            stderr = io.StringIO()
            with (
                patch.object(builder.subprocess, "run", side_effect=process),
                redirect_stdout(io.StringIO()),
                redirect_stderr(stderr),
            ):
                with self.assertRaisesRegex(RuntimeError, "종료 코드=1"):
                    builder.check_executable(Path(tmp) / "app.exe", report)
            self.assertIn("모의 DLL 오류 원문", stderr.getvalue())

    def test_old_report_is_not_reused_after_startup_failure(self) -> None:
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "검사.json"
            report.write_text('{"성공":true,"독립실행":true}', encoding="utf-8")
            with patch.object(
                builder.subprocess, "run", return_value=SimpleNamespace(returncode=1)
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "보고서가 생성되지 않았습니다"
                ):
                    builder.check_executable(Path(tmp) / "app.exe", report)
            self.assertFalse(report.exists())

    def test_successful_exit_still_requires_successful_frozen_report(self) -> None:
        import json
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "검사.json"

            def process(*args: object, **kwargs: object) -> SimpleNamespace:
                report.write_text(
                    json.dumps({"성공": True, "독립실행": False}), encoding="utf-8"
                )
                return SimpleNamespace(returncode=0)

            with (
                patch.object(builder.subprocess, "run", side_effect=process),
                redirect_stdout(io.StringIO()),
            ):
                with self.assertRaisesRegex(RuntimeError, "독립 실행 검사 실패"):
                    builder.check_executable(Path(tmp) / "app.exe", report)
