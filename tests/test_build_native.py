import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import build_native


class NativeBuildPreflightTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "native").mkdir()
        self.include = self.root / "include"
        self.include.mkdir()
        self.header = self.include / "Python.h"
        self.header.touch()
        self.library = self.root / "libpython.so"
        self.library.touch()
        self.configuration = {
            "PYTHONFRAMEWORK": "",
            "LIBDIR": str(self.root),
            "LDLIBRARY": self.library.name,
            "LIBS": "-ldl",
            "SYSLIBS": "-lm",
        }
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(build_native, "ROOT", self.root))
        self.stack.enter_context(patch.object(build_native.sys, "platform", "linux"))
        self.stack.enter_context(patch.dict(build_native.os.environ, {"CC": "clang"}))
        self.stack.enter_context(patch.object(build_native.sysconfig, "get_path", return_value=str(self.include)))
        self.stack.enter_context(patch.object(build_native.sysconfig, "get_config_var", side_effect=self.configuration.get))
        self.which = self.stack.enter_context(patch.object(build_native.shutil, "which", side_effect=lambda name: "/tools/" + name))
        self.run = self.stack.enter_context(patch.object(build_native.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stack.enter_context(contextlib.redirect_stderr(io.StringIO()))

    def assert_preflight_failure(self, message):
        with self.assertRaisesRegex(SystemExit, message):
            build_native.main(["--skip-stress"])
        self.run.assert_not_called()
        self.assertFalse((self.root / "bin").exists())
        self.assertFalse((self.root / "native" / "build_report.json").exists())

    def test_windows_rejected_before_checking_tools_or_creating_output(self):
        with patch.object(build_native.sys, "platform", "win32"):
            self.assert_preflight_failure("Linux.*macOS")
        self.which.assert_not_called()

    def test_missing_compiler_explains_cc_override(self):
        self.which.side_effect = lambda name: None if name == "clang" else "/tools/" + name
        self.assert_preflight_failure("C11 compiler.*CC")

    def test_empty_cc_is_rejected_without_creating_output(self):
        with patch.dict(build_native.os.environ, {"CC": ""}):
            self.assert_preflight_failure("CC")

    def test_malformed_cc_has_an_actionable_error(self):
        with patch.dict(build_native.os.environ, {"CC": "\"unfinished"}):
            self.assert_preflight_failure("CC")

    def test_missing_python_header_fails_before_compilation(self):
        self.header.unlink()
        self.assert_preflight_failure("CPython.*Python.h")

    def test_missing_python_library_fails_before_compilation(self):
        self.library.unlink()
        self.assert_preflight_failure("CPython.*libpython")

    def test_missing_embedding_configuration_has_an_actionable_error(self):
        self.configuration["LIBDIR"] = None
        self.assert_preflight_failure("CPython.*development")

    def test_missing_file_utility_fails_before_compilation(self):
        self.which.side_effect = lambda name: None if name == "file" else "/tools/" + name
        self.assert_preflight_failure("file.*utility")

    def test_cc_override_and_flags_are_preserved(self):
        with patch.dict(build_native.os.environ, {"CC": "gcc -pipe"}):
            self.assertEqual(build_native.main(["--skip-stress"]), 0)
        commands = [call.args[0] for call in self.run.call_args_list]
        self.assertTrue(all(command[:2] == ["gcc", "-pipe"] for command in commands[:-1]))
        self.assertEqual(commands[-1][0], "file")
        self.assertTrue(any(str(self.library) in command for command in commands))
        report = json.loads((self.root / "native" / "build_report.json").read_text())
        self.assertEqual(report["status"], "passed")

    def test_macos_framework_library_and_otool_are_supported(self):
        self.configuration.update(PYTHONFRAMEWORK="Python", PYTHONFRAMEWORKPREFIX=str(self.root), LIBDIR=None)
        with patch.object(build_native.sys, "platform", "darwin"):
            self.assertEqual(build_native.main(["--skip-stress"]), 0)
        commands = [call.args[0] for call in self.run.call_args_list]
        self.assertTrue(any(str(self.library) in command for command in commands))
        self.assertEqual(commands[-1][:2], ["otool", "-L"])

    def test_missing_macos_otool_fails_before_compilation(self):
        self.which.side_effect = lambda name: None if name == "otool" else "/tools/" + name
        with patch.object(build_native.sys, "platform", "darwin"):
            self.assert_preflight_failure("otool.*Command Line Tools")

    def test_compilation_errors_propagate_and_preserve_failed_report(self):
        self.run.side_effect = [
            subprocess.CompletedProcess([], 0, "compiler version", ""),
            subprocess.CompletedProcess([], 7, "", "invalid C source"),
        ]
        with self.assertRaisesRegex(RuntimeError, "command failed with status 7"):
            build_native.main(["--skip-stress"])
        self.assertEqual(self.run.call_count, 2)
        report = json.loads((self.root / "native" / "build_report.json").read_text())
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["commands"][-1]["stderr"], "invalid C source")


if __name__ == "__main__":
    unittest.main()
