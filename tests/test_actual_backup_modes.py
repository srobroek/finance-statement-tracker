import errno
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
BACKUP = ROOT / "deploy/actual-poc/backup.sh"
SERVICE = ROOT / "deploy/actual-poc/finance-backup.service"


class ActualBackupModeTests(unittest.TestCase):
    def _private_helpers(self) -> str:
        source = BACKUP.read_text(encoding="utf-8")
        start = source.index("fail() {")
        end = source.index("resolved() {")
        return source[start:end]

    def _run_helper(self, script: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", script],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_backup_and_systemd_start_private(self) -> None:
        source = BACKUP.read_text(encoding="utf-8")
        self.assertIn("set -euo pipefail\numask 0077", source)
        service = SERVICE.read_text(encoding="utf-8")
        self.assertIn("UMask=0077", service)
        self.assertIn('install -m "${PRIVATE_FILE_MODE}"', source)
        self.assertIn('ensure_private_dir "${BACKUP_ROOT}" "backup_root"', source)
        self.assertIn('ensure_private_file "${working}/finance-data.tar.gz" "backup_archive"', source)
        self.assertIn('ensure_private_file "${working}/SHA256SUMS" "backup_checksums"', source)
        self.assertIn('ensure_private_file "${working}/manifest.json" "backup_manifest"', source)

    def test_existing_private_targets_are_tightened_and_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "backup"
            directory.mkdir(mode=0o755)
            artifact = directory / "manifest.json"
            artifact.write_text("{}\n", encoding="utf-8")
            artifact.chmod(0o644)
            result = self._run_helper(
                "set -euo pipefail\n"
                "PRIVATE_DIR_MODE=700 PRIVATE_FILE_MODE=600 PRIVATE_OWNER_UID=$(id -u)\n"
                f"{self._private_helpers()}\n"
                f"ensure_private_dir {directory!s} backup_root\n"
                f"ensure_private_file {artifact!s} backup_manifest\n"
                f"[[ $(stat -c '%a' {directory!s}) == 700 ]]\n"
                f"[[ $(stat -c '%a' {artifact!s}) == 600 ]]\n"
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_symlink_and_foreign_owner_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir(mode=0o700)
            link = root / "link"
            link.symlink_to(target, target_is_directory=True)
            result = self._run_helper(
                "set -euo pipefail\n"
                "PRIVATE_DIR_MODE=700 PRIVATE_FILE_MODE=600 PRIVATE_OWNER_UID=$(id -u)\n"
                f"{self._private_helpers()}\n"
                f"ensure_private_dir {link!s} backup_root\n"
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('"reason":"backup_root_symlink"', result.stderr)

            if os.geteuid() == 0:
                foreign = root / "foreign"
                foreign.write_text("private\n", encoding="utf-8")
                try:
                    os.chown(foreign, 65534, 65534)
                except OSError as exc:
                    if exc.errno in {errno.EINVAL, errno.EPERM, errno.ENOSYS}:
                        self.skipTest(f"foreign-owner fixture unavailable: {exc}")
                    raise
                result = self._run_helper(
                    "set -euo pipefail\n"
                    "PRIVATE_DIR_MODE=700 PRIVATE_FILE_MODE=600 PRIVATE_OWNER_UID=$(id -u)\n"
                    f"{self._private_helpers()}\n"
                    f"ensure_private_file {foreign!s} backup_manifest\n"
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('"reason":"backup_manifest_owner"', result.stderr)


if __name__ == "__main__":
    unittest.main()
