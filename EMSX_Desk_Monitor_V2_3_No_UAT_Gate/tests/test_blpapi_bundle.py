"""SDK의 실제 파일 검색 규칙과 PyInstaller ABI 이름 보존을 검증한다."""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest

from blpapi_bundle import ffiutils_entry, preserve_ffiutils


class TestBlpapiBundle(unittest.TestCase):
    def test_sdk_glob_ignores_normalized_name_but_finds_preserved_abi_name(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "installed" / "blpapi"
            package.mkdir(parents=True)
            source = package / "ffiutils.cp313-win_amd64.pyd"
            source.write_bytes(b"original")
            sdk = SimpleNamespace(
                __file__=str(package / "internals.py"),
                libffastcalls=SimpleNamespace(_name=str(source)),
            )
            entry = ffiutils_entry(sdk)
            bundle = root / "bundle"
            target = bundle / "blpapi"
            target.mkdir(parents=True)
            (target / "ffiutils.pyd").write_bytes(b"normalized")
            self.assertEqual(list(target.glob("ffiutils.*.pyd")), [])
            shutil.copyfile(entry[1], bundle / entry[0])
            found = list(target.glob("ffiutils.*.pyd"))
            self.assertEqual([path.name for path in found], [source.name])
            self.assertEqual(found[0].read_bytes(), source.read_bytes())
            self.assertEqual(entry[2], "BINARY")

    def test_loaded_helper_is_selected_when_multiple_candidates_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for version in ("cp312", "cp313"):
                (root / f"ffiutils.{version}-win_amd64.pyd").write_bytes(b"test")
            selected = root / "ffiutils.cp313-win_amd64.pyd"
            sdk = SimpleNamespace(
                __file__=str(root / "internals.py"),
                libffastcalls=SimpleNamespace(_name=str(selected)),
            )
            self.assertEqual(ffiutils_entry(sdk)[1], str(selected.resolve()))

    def test_ambiguous_or_missing_helper_is_not_guessed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sdk = SimpleNamespace(__file__=str(root / "internals.py"))
            with self.assertRaises(RuntimeError):
                ffiutils_entry(sdk)
            for version in ("cp312", "cp313"):
                (root / f"ffiutils.{version}-win_amd64.pyd").write_bytes(b"test")
            with self.assertRaises(RuntimeError):
                ffiutils_entry(sdk)

    def test_analysis_keeps_original_name_as_binary_and_preserves_other_extensions(
        self,
    ) -> None:
        original = (
            "blpapi/ffiutils.cp313-win_amd64.pyd",
            "/source/ffiutils.cp313-win_amd64.pyd",
            "BINARY",
        )
        entries = [
            ("blpapi/ffiutils.pyd", original[1], "EXTENSION"),
            ("blpapi\\ffiutils.cp313-win_amd64.pyd", original[1], "EXTENSION"),
            ("_tkinter.pyd", "/source/_tkinter.pyd", "EXTENSION"),
        ]
        result = preserve_ffiutils(entries, original)
        self.assertIn(original, result)
        self.assertIn(entries[0], result)
        self.assertIn(entries[2], result)
        self.assertEqual(
            sum(item[0].replace("\\", "/") == original[0] for item in result), 1
        )
