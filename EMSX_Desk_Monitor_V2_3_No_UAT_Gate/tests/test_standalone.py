"""독립 실행 자산 경로와 자체 검사 보고서의 성공·실패 구분을 검증한다."""

import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from resource_paths import asset_path
from standalone_check import run_check


class TestStandalone(unittest.TestCase):
    def test_frozen_asset_root(self) -> None:
        with patch.object(sys, "_MEIPASS", "/tmp/emsx-bundle", create=True):
            self.assertEqual(
                asset_path("desk_alert.wav"),
                Path("/tmp/emsx-bundle/assets/desk_alert.wav"),
            )
        self.assertTrue(asset_path("desk_alert.wav").is_file())

    def test_native_dependency_failure_produces_failed_report(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(sys.modules, {"blpapi": None}),
        ):
            target = Path(tmp) / "보고서.json"
            self.assertEqual(run_check(str(target)), 1)
            report = json.loads(target.read_text(encoding="utf-8"))
            self.assertFalse(report["성공"])
            self.assertIn("오류", report)

    def test_success_does_not_connect_or_play_sound(self) -> None:
        api = SimpleNamespace(
            version=lambda: "모의 Python SDK",
            cpp_sdk_version=lambda: "모의 C++ SDK",
            SessionOptions=Mock(return_value=Mock()),
        )
        root = Mock()
        root.tk.call.return_value = "8.6"
        sound = Mock()
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(sys.modules, {"blpapi": api, "winsound": sound}),
            patch("tkinter.Tk", return_value=root),
        ):
            target = Path(tmp) / "보고서.json"
            self.assertEqual(run_check(str(target)), 0)
            self.assertTrue(json.loads(target.read_text(encoding="utf-8"))["성공"])
            sound.PlaySound.assert_not_called()
            api.SessionOptions.assert_called_once()
            root.destroy.assert_called_once()

    def test_native_api_failure_is_reported_as_failure(self) -> None:
        api = SimpleNamespace(
            version=lambda: "모의 SDK",
            SessionOptions=Mock(side_effect=OSError("모의 네이티브 DLL 오류")),
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(sys.modules, {"blpapi": api}),
        ):
            target = Path(tmp) / "검사.json"
            self.assertEqual(run_check(str(target)), 1)
            report = json.loads(target.read_text(encoding="utf-8"))
            self.assertFalse(report["성공"])
            self.assertEqual(report["단계"], "BLPAPI C++ 네이티브 객체 검사")
            self.assertIn("모의 네이티브 DLL 오류", report["오류"])

    def test_version_metadata_is_optional_after_native_api_success(self) -> None:
        api = SimpleNamespace(
            version=lambda: "모의 SDK", SessionOptions=Mock(return_value=Mock())
        )
        root = Mock()
        root.tk.call.return_value = "8.6"
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(sys.modules, {"blpapi": api, "winsound": Mock()}),
            patch("tkinter.Tk", return_value=root),
        ):
            target = Path(tmp) / "검사.json"
            self.assertEqual(run_check(str(target)), 0)
            report = json.loads(target.read_text(encoding="utf-8"))
            self.assertTrue(report["네이티브 API"])
            self.assertIn("함수 미제공", report["BLPAPI_C++"])
            self.assertEqual(report["단계"], "완료")

    def test_frozen_entry_self_test_does_not_import_ui(self) -> None:
        import emsx_entry

        check = SimpleNamespace(run_check=Mock(return_value=1))
        with (
            patch.dict(
                sys.modules, {"emsx_desk_monitor": None, "standalone_check": check}
            ),
            patch.object(sys, "argv", ["emsx_entry.py", "--self-test", "검사.json"]),
        ):
            self.assertEqual(emsx_entry.main(), 1)
        check.run_check.assert_called_once_with("검사.json")

    def test_frozen_entry_preserves_demo_arguments(self) -> None:
        import emsx_entry

        ui = SimpleNamespace(main=Mock())
        with (
            patch.dict(sys.modules, {"emsx_desk_monitor": ui}),
            patch.object(sys, "argv", ["emsx_entry.py", "--demo"]),
        ):
            self.assertEqual(emsx_entry.main(), 0)
            self.assertEqual(sys.argv[1:], ["--demo"])
        ui.main.assert_called_once()
