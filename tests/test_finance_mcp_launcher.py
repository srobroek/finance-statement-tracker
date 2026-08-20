from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "deploy" / "finance-runtime" / "launch-codex-finance-mcp.sh"
TRUSTED_INSTALLS = Path.home() / ".local" / "share" / "mise" / "installs"


def executable(path: Path, source: str) -> Path:
    path.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(source), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@contextmanager
def trusted_binaries(prefix: str = "finance-mcp-test-"):
    with tempfile.TemporaryDirectory(dir=TRUSTED_INSTALLS, prefix=prefix) as directory:
        bin_dir = Path(directory) / "bin"
        bin_dir.mkdir()
        yield bin_dir


class FinanceMcpLauncherTests(unittest.TestCase):
    def test_fake_op_cli_state_machine_receives_reference_template(self):
        with tempfile.TemporaryDirectory(dir="/dev/shm") as directory, trusted_binaries() as trusted:
            root = Path(directory)
            env_path_log = root / "env-path"
            argv_log = root / "argv"
            child_seen = root / "child-env"
            fake_codex = executable(
                trusted / "codex",
                f"""
                import os, pathlib
                pathlib.Path(r'{child_seen}').write_text(os.environ.get('FINANCE_N8N_MCP_BEARER', 'missing'))
                """,
            )
            fake_op = executable(
                trusted / "op",
                f"""
                import os, pathlib, subprocess, sys
                if sys.argv[1:2] != ['run'] or not sys.argv[2].startswith('--env-file=') or sys.argv[3] != '--':
                    raise SystemExit(11)
                env_file = pathlib.Path(sys.argv[2].split('=', 1)[1])
                pathlib.Path(r'{env_path_log}').write_text(str(env_file))
                pathlib.Path(r'{argv_log}').write_text(' '.join(sys.argv[1:]))
                line = env_file.read_text().strip()
                if line != 'FINANCE_N8N_MCP_BEARER=op://FinanceRuntime/Finance Statement Tracker Runtime/finance_n8n_mcp_bearer':
                    raise SystemExit(12)
                child_env = os.environ.copy()
                child_env['FINANCE_N8N_MCP_BEARER'] = 'child-only-value'
                raise SystemExit(subprocess.run(sys.argv[4:], env=child_env, check=False).returncode)
                """,
            )
            environment = {
                "PATH": os.environ["PATH"],
                "HOME": os.environ.get("HOME", "/tmp"),
                "OP_BIN": str(fake_op),
                "CODEX_BIN": str(fake_codex),
                "FINANCE_MCP_LAUNCH_TMPDIR": "/dev/shm",
            }
            result = subprocess.run(
                [str(LAUNCHER), "codex", "--model", "approved"],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(child_seen.read_text(), "child-only-value")
            self.assertNotIn("child-only-value", result.stdout + result.stderr)
            self.assertNotIn("child-only-value", argv_log.read_text())
            self.assertFalse(Path(env_path_log.read_text()).exists())

    def test_failure_also_removes_reference_template(self):
        with tempfile.TemporaryDirectory(dir="/dev/shm") as directory, trusted_binaries() as trusted:
            root = Path(directory)
            env_path_log = root / "env-path"
            fake_codex = executable(trusted / "codex", """\nraise SystemExit(17)\n""")
            fake_op = executable(
                trusted / "op",
                f"""
                import pathlib, subprocess, sys
                env_file = pathlib.Path(sys.argv[2].split('=', 1)[1])
                pathlib.Path(r'{env_path_log}').write_text(str(env_file))
                raise SystemExit(subprocess.run(sys.argv[4:], check=False).returncode)
                """,
            )
            environment = {
                "PATH": os.environ["PATH"],
                "HOME": os.environ.get("HOME", "/tmp"),
                "OP_BIN": str(fake_op),
                "CODEX_BIN": str(fake_codex),
                "FINANCE_MCP_LAUNCH_TMPDIR": "/dev/shm",
            }
            result = subprocess.run([str(LAUNCHER), "codex"], env=environment, text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 17)
            self.assertFalse(Path(env_path_log.read_text()).exists())
            self.assertNotIn("child-only-value", result.stdout + result.stderr)

    def test_windows_op_exe_path_with_spaces_preserves_help_metadata_without_values(self):
        with tempfile.TemporaryDirectory(dir="/dev/shm") as directory, trusted_binaries(prefix="finance mcp test-") as trusted:
            root = Path(directory)
            env_path_log = root / "env-path"
            op_argv_log = root / "op-argv"
            codex_argv_log = root / "codex-argv"
            fake_codex = executable(
                trusted / "codex",
                f"""
                import pathlib, sys
                pathlib.Path(r'{codex_argv_log}').write_text(' '.join(sys.argv[1:]))
                """,
            )
            fake_op = executable(
                trusted / "op.exe",
                f"""
                import pathlib, subprocess, sys
                if sys.argv[1:2] != ['run'] or not sys.argv[2].startswith('--env-file=') or sys.argv[3] != '--':
                    raise SystemExit(11)
                env_file = pathlib.Path(sys.argv[2].split('=', 1)[1])
                pathlib.Path(r'{env_path_log}').write_text(str(env_file))
                pathlib.Path(r'{op_argv_log}').write_text(' '.join(sys.argv[1:4]))
                raise SystemExit(subprocess.run(sys.argv[4:], check=False).returncode)
                """,
            )
            environment = {
                "PATH": os.environ["PATH"],
                "HOME": os.environ.get("HOME", "/tmp"),
                "OP_BIN": str(fake_op),
                "CODEX_BIN": str(fake_codex),
                "FINANCE_MCP_LAUNCH_TMPDIR": "/dev/shm",
            }
            result = subprocess.run(
                [str(LAUNCHER), "codex", "--help"],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(codex_argv_log.read_text(), "--help")
            self.assertIn("run --env-file=", op_argv_log.read_text())
            self.assertNotIn("op://", op_argv_log.read_text())
            self.assertFalse(Path(env_path_log.read_text()).exists())
            self.assertNotIn("child-only-value", result.stdout + result.stderr)

    def test_signed_out_op_failure_removes_reference_template(self):
        with tempfile.TemporaryDirectory(dir="/dev/shm") as directory, trusted_binaries() as trusted:
            root = Path(directory)
            env_path_log = root / "env-path"
            fake_codex = executable(trusted / "codex", "raise SystemExit(19)")
            fake_op = executable(
                trusted / "op.exe",
                f"""
                import pathlib, sys
                if sys.argv[1:2] != ['run'] or not sys.argv[2].startswith('--env-file='):
                    raise SystemExit(11)
                pathlib.Path(r'{env_path_log}').write_text(sys.argv[2].split('=', 1)[1])
                raise SystemExit(64)
                """,
            )
            environment = {
                "PATH": os.environ["PATH"],
                "HOME": os.environ.get("HOME", "/tmp"),
                "OP_BIN": str(fake_op),
                "CODEX_BIN": str(fake_codex),
                "FINANCE_MCP_LAUNCH_TMPDIR": "/dev/shm",
            }
            result = subprocess.run([str(LAUNCHER), "codex"], env=environment, text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 64)
            self.assertFalse(Path(env_path_log.read_text()).exists())
            self.assertNotIn("child-only-value", result.stdout + result.stderr)

    def test_arbitrary_op_executable_is_rejected_before_spawn(self):
        with tempfile.TemporaryDirectory(dir="/dev/shm") as directory:
            root = Path(directory)
            invoked = root / "invoked"
            fake_op = executable(
                root / "arbitrary-executable",
                f"""
                import pathlib
                pathlib.Path(r'{invoked}').touch()
                """,
            )
            environment = {
                "PATH": os.environ["PATH"],
                "HOME": os.environ.get("HOME", "/tmp"),
                "OP_BIN": str(fake_op),
                "FINANCE_MCP_LAUNCH_TMPDIR": "/dev/shm",
            }
            result = subprocess.run([str(LAUNCHER), "codex"], env=environment, text=True, capture_output=True, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("OP_BIN", result.stderr)
            self.assertFalse(invoked.exists())

    def test_same_basename_op_impostor_is_rejected_before_spawn(self):
        with tempfile.TemporaryDirectory(dir="/dev/shm") as directory:
            root = Path(directory)
            invoked = root / "invoked"
            fake_op = executable(
                root / "op",
                f"""
                import pathlib
                pathlib.Path(r'{invoked}').touch()
                """,
            )
            environment = {
                "PATH": os.environ["PATH"],
                "HOME": os.environ.get("HOME", "/tmp"),
                "OP_BIN": str(fake_op),
                "FINANCE_MCP_LAUNCH_TMPDIR": "/dev/shm",
            }
            result = subprocess.run([str(LAUNCHER), "codex"], env=environment, text=True, capture_output=True, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("OP_BIN", result.stderr)
            self.assertFalse(invoked.exists())

    def test_same_basename_op_exe_impostor_is_rejected_before_spawn(self):
        with tempfile.TemporaryDirectory(dir="/dev/shm") as directory:
            root = Path(directory)
            invoked = root / "invoked"
            fake_op = executable(
                root / "op.exe",
                f"""
                import pathlib
                pathlib.Path(r'{invoked}').touch()
                """,
            )
            environment = {
                "PATH": os.environ["PATH"],
                "HOME": os.environ.get("HOME", "/tmp"),
                "OP_BIN": str(fake_op),
                "FINANCE_MCP_LAUNCH_TMPDIR": "/dev/shm",
            }
            result = subprocess.run([str(LAUNCHER), "codex"], env=environment, text=True, capture_output=True, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("OP_BIN", result.stderr)
            self.assertFalse(invoked.exists())

    def test_same_basename_codex_impostor_is_rejected_before_spawn(self):
        with tempfile.TemporaryDirectory(dir="/dev/shm") as directory, trusted_binaries() as trusted:
            root = Path(directory)
            invoked = root / "invoked"
            fake_op = executable(
                trusted / "op",
                f"""
                import pathlib
                pathlib.Path(r'{invoked}').touch()
                """,
            )
            for basename in ("codex", "codex-cli"):
                fake_codex = executable(root / basename, "raise SystemExit(19)")
                environment = {
                    "PATH": os.environ["PATH"],
                    "HOME": os.environ.get("HOME", "/tmp"),
                    "OP_BIN": str(fake_op),
                    "CODEX_BIN": str(fake_codex),
                    "FINANCE_MCP_LAUNCH_TMPDIR": "/dev/shm",
                }
                result = subprocess.run([str(LAUNCHER), "codex"], env=environment, text=True, capture_output=True, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("CODEX_BIN", result.stderr)
                self.assertFalse(invoked.exists())

    def test_explicit_host_windows_op_path_is_allowed_before_codex_validation(self):
        windows_op = Path("/mnt/c/Users/USER/AppData/Local/Microsoft/WinGet/Links/op.exe")
        if not windows_op.is_file():
            self.skipTest("host Windows 1Password CLI path is unavailable")
        with tempfile.TemporaryDirectory(dir="/dev/shm") as directory:
            fake_codex = executable(Path(directory) / "codex", "raise SystemExit(19)")
            environment = {
                "PATH": os.environ["PATH"],
                "HOME": os.environ.get("HOME", "/tmp"),
                "OP_BIN": str(windows_op),
                "CODEX_BIN": str(fake_codex),
                "FINANCE_MCP_LAUNCH_TMPDIR": "/dev/shm",
            }
            result = subprocess.run([str(LAUNCHER), "codex"], env=environment, text=True, capture_output=True, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("CODEX_BIN", result.stderr)

    def test_parent_secret_and_session_variables_are_rejected(self):
        base = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp")}
        for variable in ("FINANCE_N8N_MCP_BEARER", "OP_SESSION_test", "DB_PASSWORD"):
            environment = {**base, variable: "parent-only-value"}
            result = subprocess.run([str(LAUNCHER), "codex"], env=environment, text=True, capture_output=True, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("parent-only-value", result.stdout + result.stderr)

    def test_non_codex_child_is_rejected(self):
        environment = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp")}
        result = subprocess.run([str(LAUNCHER), "python3"], env=environment, text=True, capture_output=True, check=False)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
