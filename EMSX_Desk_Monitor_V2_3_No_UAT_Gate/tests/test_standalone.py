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
            version=lambda: "모의 Python SDK", cpp_sdk_version=lambda: "모의 C++ SDK"
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
            root.destroy.assert_called_once()
