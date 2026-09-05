import errno
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class DeploymentScriptTests(unittest.TestCase):
    def _root_bash_command(self, script: Path, environment: dict[str, str]) -> list[str]:
        """Run root-only fixtures wherever the runner permits it.

        Local sandboxes and hosted runners often disable user namespaces.  A
        passwordless sudo path keeps these checks running on ordinary CI; when
        neither mechanism is available, only the root-dependent fixture is
        reported as unavailable instead of producing a misleading assertion.
        """
        if os.geteuid() == 0:
            return ["bash", str(script)]
        if shutil.which("unshare"):
            probe = subprocess.run(
                ["unshare", "-Ur", "true"],
                capture_output=True,
                check=False,
            )
            if probe.returncode == 0:
                return ["unshare", "-Ur", "bash", str(script)]
        if shutil.which("sudo"):
            probe = subprocess.run(
                ["sudo", "-n", "true"],
                capture_output=True,
                check=False,
            )
            if probe.returncode == 0:
                # sudo's secure_path replaces PATH even with -E. Restore the
                # fixture's tool directory after elevation so these tests
                # cannot accidentally invoke the host's real Docker client.
                return ["sudo", "-n", "-E", "env", f"PATH={environment['PATH']}", "bash", str(script)]
        raise unittest.SkipTest(
            "root-only fixture unavailable: user namespaces and passwordless sudo are disabled"
        )

    def _run_root_fixture(
        self, script: Path, root: Path, environment: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        command = self._root_bash_command(script, environment)
        try:
            return subprocess.run(
                command, cwd=Path.cwd(), env=environment,
                text=True, capture_output=True, check=False,
            )
        finally:
            if command[0] == "sudo":
                # The real root-only script correctly creates private files.
                # Return this disposable fixture to the test user for readback
                # assertions and TemporaryDirectory cleanup.
                subprocess.run(
                    ["sudo", "-n", "chown", "-R", "--no-dereference", "--",
                     f"{os.getuid()}:{os.getgid()}", str(root)],
                    check=True, capture_output=True, text=True,
                )

    def _run_render_env_fixture(
        self,
        bootstrap: str | bytes,
        *,
        mode: int = 0o600,
        symlink: bool = False,
        owner: int | None = None,
        sudo_uid: int | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], str, str]:
        script = Path("deploy/finance-runtime/render-env.sh")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            runtime.mkdir()
            bootstrap_target = root / "bootstrap-target"
            if isinstance(bootstrap, bytes):
                bootstrap_target.write_bytes(bootstrap)
            else:
                bootstrap_target.write_text(bootstrap, encoding="utf-8")
            bootstrap_target.chmod(mode)
            bootstrap_file = root / "bootstrap"
            if symlink:
                bootstrap_file.symlink_to(bootstrap_target)
            else:
                bootstrap_file = bootstrap_target
            if owner is not None:
                try:
                    os.chown(bootstrap_target, owner, owner)
                except OSError as exc:
                    if exc.errno in {errno.EPERM, errno.EINVAL, errno.ENOSYS}:
                        self.skipTest(f"foreign-owner fixture unavailable: {exc}")
                    raise
            (runtime / ".env.tpl").write_text("APP_ENV=fixture\n", encoding="utf-8")
            bin_dir = root / "bin"
            bin_dir.mkdir()
            token_capture = root / "token"
            (bin_dir / "op").write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "printf '%s' \"${OP_SERVICE_ACCOUNT_TOKEN-}\" > \"${TOKEN_CAPTURE}\"\n"
                "out=\"\"\n"
                "while (($#)); do\n"
                "  if [[ \"$1\" == --out-file ]]; then out=\"$2\"; shift 2; else shift; fi\n"
                "done\n"
                "printf '%s\\n' 'APP_ENV=fixture' > \"${out}\"\n",
                encoding="utf-8",
            )
            (bin_dir / "op").chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{bin_dir}:{environment['PATH']}",
                    "FINANCE_RUNTIME_DIR": str(runtime),
                    "FINANCE_OP_BOOTSTRAP_FILE": str(bootstrap_file),
                    "TOKEN_CAPTURE": str(token_capture),
                }
            )
            if sudo_uid is not None:
                environment["SUDO_UID"] = str(sudo_uid)
            result = subprocess.run(
                ["bash", str(script)],
                cwd=Path.cwd(),
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            rendered = (
                (runtime / ".env").read_text(encoding="utf-8")
                if (runtime / ".env").exists()
                else ""
            )
            captured = (
                token_capture.read_text(encoding="utf-8")
                if token_capture.exists()
                else ""
            )
            return result, rendered, captured

    def test_render_env_parses_literal_token_and_preserves_render_contract(self) -> None:
        for assignment in (
            "OP_SERVICE_ACCOUNT_TOKEN=ops_fixture-token\n",
            "OP_SERVICE_ACCOUNT_TOKEN='ops_fixture-token'\n",
            'OP_SERVICE_ACCOUNT_TOKEN="ops_fixture-token"\n',
        ):
            with self.subTest(assignment=assignment):
                result, rendered, captured = self._run_render_env_fixture(assignment)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(rendered, "APP_ENV=fixture\n")
                self.assertEqual(captured, "ops_fixture-token")
                self.assertNotIn("ops_fixture-token", result.stdout + result.stderr)

    def test_render_env_rejects_shell_content_and_extra_assignments(self) -> None:
        for bootstrap in (
            "OP_SERVICE_ACCOUNT_TOKEN=$(printf injected)\n",
            "OP_SERVICE_ACCOUNT_TOKEN=${TOKEN}\n",
            "OP_SERVICE_ACCOUNT_TOKEN=ops_fixture-token\nOTHER=value\n",
            "OP_SERVICE_ACCOUNT_TOKEN=ops_fixture-token\nOP_SERVICE_ACCOUNT_TOKEN=other\n",
            "OP_SERVICE_ACCOUNT_TOKEN='ops_fixture-token\n",
            "OP_SERVICE_ACCOUNT_TOKEN=ops_fixture-token\r\n",
            b"OP_SERVICE_ACCOUNT_TOKEN=ops_fixture-token\x00\n",
        ):
            with self.subTest(bootstrap=bootstrap):
                result, rendered, captured = self._run_render_env_fixture(bootstrap)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(rendered, "")
                self.assertEqual(captured, "")
                self.assertNotIn("ops_fixture-token", result.stdout + result.stderr)

    def test_render_env_rejects_symlink_unsafe_mode_and_owner(self) -> None:
        for kwargs in (
            {"symlink": True},
            {"mode": 0o640},
        ):
            with self.subTest(kwargs=kwargs):
                result, rendered, captured = self._run_render_env_fixture(
                    "OP_SERVICE_ACCOUNT_TOKEN=ops_fixture-token\n", **kwargs
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(rendered, "")
                self.assertEqual(captured, "")
        if os.geteuid() == 0:
            result, rendered, captured = self._run_render_env_fixture(
                "OP_SERVICE_ACCOUNT_TOKEN=ops_fixture-token\n", sudo_uid=65534
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(rendered, "APP_ENV=fixture\n")
            self.assertEqual(captured, "ops_fixture-token")
            result, rendered, captured = self._run_render_env_fixture(
                "OP_SERVICE_ACCOUNT_TOKEN=ops_fixture-token\n",
                owner=65533,
                sudo_uid=65534,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(rendered, "")
            self.assertEqual(captured, "")
            result, rendered, captured = self._run_render_env_fixture(
                "OP_SERVICE_ACCOUNT_TOKEN=ops_fixture-token\n",
                owner=65534,
                sudo_uid=65534,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(rendered, "APP_ENV=fixture\n")
            self.assertEqual(captured, "ops_fixture-token")

    def _run_backup_fixture(
        self, probe_mode: str, docker_mode: str = "normal"
    ) -> subprocess.CompletedProcess[str]:
        script = Path("deploy/actual/backup.sh")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            actual_stack = root / "actual"
            cashback_stack = root / "cashback"
            actual_data = actual_stack / "data"
            cashback_data = actual_stack / "cashback-data"
            backup_root = root / "backups"
            actual_data.mkdir(parents=True)
            cashback_data.mkdir(parents=True)
            cashback_stack.mkdir()
            (actual_stack / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
            (cashback_stack / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
            (actual_data / "actual.json").write_text("{}\n", encoding="utf-8")
            (cashback_data / "cashback.json").write_text("{}\n", encoding="utf-8")
            verify = actual_stack / "verify-backup.py"
            verify.write_text("raise SystemExit(0)\n", encoding="utf-8")
            sanitizer = actual_stack / "sanitize-cashback-backup.py"
            sanitizer.write_text("raise SystemExit(0)\n", encoding="utf-8")

            (bin_dir / "readlink").write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "path=\"${@: -1}\"\n"
                "case \"${path}\" in\n"
                f"  {actual_stack}) printf '%s\\n' /opt/stacks/finance-actual ;;\n"
                f"  {cashback_stack}) printf '%s\\n' /opt/stacks/finance-cashback ;;\n"
                f"  {backup_root}) printf '%s\\n' /opt/backups/finance-actual ;;\n"
                "  *) printf '%s\\n' \"${path}\" ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            fd_log = root / "docker-fd.log"
            (bin_dir / "docker").write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "state=\"${STUB_STATE}\"\n"
                "log=\"${STUB_LOG}\"\n"
                "name=\"${@: -1}\"\n"
                "if [[ -e /proc/self/fd/9 ]]; then printf 'inherited\\n' >> \"${STUB_FD_LOG}\"; else printf 'closed\\n' >> \"${STUB_FD_LOG}\"; fi\n"
                "case \"${1}\" in\n"
                "  inspect)\n"
                "    if [[ \"${DOCKER_MODE}\" == inspect_error && \"${3}\" == *'.State.Status'* && \"${name}\" == finance-actual ]]; then exit 1; fi\n"
                "    if [[ \"${DOCKER_MODE}\" == unexpected_state && \"${3}\" == *'.State.Status'* && \"${name}\" == finance-actual ]]; then printf 'mystery\\n'; exit 0; fi\n"
                "    if [[ \"${3}\" == *'.State.Paused'* ]]; then\n"
                "      [[ -e \"${state}/${name}.paused\" ]] && printf 'true\\n' || printf 'false\\n'\n"
                "    elif [[ -e \"${state}/${name}.paused\" ]]; then\n"
                "      printf 'paused\\n'\n"
                "    else\n"
                "      printf 'running\\n'\n"
                "    fi\n"
                "    ;;\n"
                "  pause) printf 'pause:%s\\n' \"${2}\" >> \"${log}\"; touch \"${state}/${2}.paused\"; if [[ \"${DOCKER_MODE}\" == partial_pause_failure && \"${2}\" == finance-actual ]]; then exit 1; fi ;;\n"
                "  unpause) printf 'unpause:%s\\n' \"${2}\" >> \"${log}\"; rm -f \"${state}/${2}.paused\" ;;\n"
                "  exec)\n"
                "    case \"${PROBE_MODE}\" in\n"
                "      success) exit 0 ;;\n"
                "      missing_probe) printf \"python: can't open file 'apps/cashback-control/probe_health.py': No such file or directory\\n\" >&2; exit 1 ;;\n"
                "      runtime_exec_failed) printf 'Error response from daemon: OCI runtime exec failed\\n' >&2; exit 1 ;;\n"
                "      probe_unhealthy) exit 1 ;;\n"
                "    esac\n"
                "    ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            (bin_dir / "curl").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            (bin_dir / "sleep").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            for executable in ("readlink", "docker", "curl", "sleep"):
                (bin_dir / executable).chmod(0o755)
            state = root / "state"
            state.mkdir()
            log = root / "docker.log"
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{bin_dir}:{environment['PATH']}",
                    "FINANCE_ACTUAL_STACK_DIR": str(actual_stack),
                    "FINANCE_CASHBACK_STACK_DIR": str(cashback_stack),
                    "FINANCE_BACKUP_ROOT": str(backup_root),
                    "FINANCE_ACTUAL_DATA_DIR": str(actual_data),
                    "FINANCE_CASHBACK_DATA_DIR": str(cashback_data),
                    "FINANCE_BACKUP_VERIFY_SCRIPT": str(verify),
                    "FINANCE_CASHBACK_BACKUP_SANITIZE_SCRIPT": str(sanitizer),
                    "STUB_STATE": str(state),
                    "STUB_LOG": str(log),
                    "STUB_FD_LOG": str(fd_log),
                    "PROBE_MODE": probe_mode,
                    "DOCKER_MODE": docker_mode,
                }
            )
            result = self._run_root_fixture(script, root, environment)
            self.assertIn(
                probe_mode,
                ("success", "missing_probe", "runtime_exec_failed", "probe_unhealthy"),
            )
            if docker_mode == "normal" and probe_mode == "success":
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(list(backup_root.glob("20??????T??????Z"))), 1)
                self.assertFalse(list(backup_root.glob(".*.incomplete")))
            elif docker_mode == "normal":
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(list(backup_root.glob(".*.incomplete"))), 1)
                self.assertIn(f'"reason":"{probe_mode}"', result.stderr)
                self.assertEqual(
                    result.stderr.count('"event":"backup_resume_unhealthy"'), 1
                )
            elif docker_mode in ("inspect_error", "unexpected_state"):
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f'"reason":"{docker_mode}"', result.stderr)
                docker_log = log.read_text(encoding="utf-8") if log.exists() else ""
                self.assertNotIn("pause:", docker_log)
                self.assertNotIn("unpause:", docker_log)
                self.assertFalse(list(backup_root.glob(".*.incomplete")))
            elif docker_mode == "partial_pause_failure":
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('"reason":"pause_failed"', result.stderr)
                self.assertEqual(
                    log.read_text(encoding="utf-8").splitlines(),
                    [
                        "pause:finance-actual-proxy",
                        "pause:finance-actual",
                        "unpause:finance-actual",
                        "unpause:finance-actual-proxy",
                    ],
                )
                self.assertFalse((state / "finance-actual.paused").exists())
                self.assertFalse((state / "finance-actual-proxy.paused").exists())
            if docker_mode == "normal":
                self.assertEqual(
                    result.stderr.count('"event":"backup_resume_unhealthy"'),
                    0 if probe_mode == "success" else 1,
                )
                self.assertEqual(log.read_text(encoding="utf-8").count("unpause:"), 3)
            fd_observations = fd_log.read_text(encoding="utf-8")
            self.assertIn("closed\n", fd_observations)
            self.assertNotIn("inherited\n", fd_observations)
            return result

    def test_backup_stubbed_runtime_promotes_or_retains_redacted_failure(self) -> None:
        for mode in ("success", "missing_probe", "runtime_exec_failed", "probe_unhealthy"):
            with self.subTest(mode=mode):
                self._run_backup_fixture(mode)

    def test_backup_container_inspection_is_strict_and_fail_closed(self) -> None:
        for mode in ("inspect_error", "unexpected_state"):
            with self.subTest(mode=mode):
                self._run_backup_fixture("success", docker_mode=mode)

    def test_backup_partial_pause_failure_is_recovered(self) -> None:
        self._run_backup_fixture("success", docker_mode="partial_pause_failure")

    def test_backup_resume_helpers_are_exactly_once_and_state_aware(self) -> None:
        script = Path("deploy/actual/backup.sh").read_text(encoding="utf-8")
        helpers = script[
            script.index("declare -A paused_services=()"):script.index("actual_state=")
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            state = root / "state"
            state.mkdir()
            log = root / "docker.log"
            docker = bin_dir / "docker"
            docker.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "name=\"${@: -1}\"\n"
                "case \"${1}\" in\n"
                "  inspect)\n"
                "    if [[ \"${STUB_MODE}\" == inspect_error ]]; then exit 1; fi\n"
                "    if [[ \"${3}\" == *'.State.Paused'* ]]; then\n"
                "      [[ -e \"${STUB_STATE}/${name}.paused\" ]] && echo true || echo false\n"
                "    elif [[ -e \"${STUB_STATE}/${name}.paused\" ]]; then echo paused; else echo running; fi\n"
                "    ;;\n"
                "  unpause) echo \"${2}\" >> \"${STUB_LOG}\"; rm -f \"${STUB_STATE}/${2}.paused\" ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            docker.chmod(0o755)
            harness = (
                "set -euo pipefail\n"
                f"export PATH={bin_dir}:$PATH STUB_STATE={state} STUB_LOG={log} STUB_MODE=normal\n"
                f"{helpers}\n"
                "touch \"${STUB_STATE}/finance-actual.paused\"\n"
                "paused_services[finance-actual]=paused\n"
                "resume_services\n"
                "resume_services\n"
                "[[ $(wc -l < \"${STUB_LOG}\") -eq 1 ]]\n"
                "paused_services[finance-cashback-control]=paused\n"
                "resume_services\n"
                "[[ $(wc -l < \"${STUB_LOG}\") -eq 1 ]]\n"
            )
            result = subprocess.run(
                ["bash", "-c", harness],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_backup_resume_retains_unknown_ownership_for_uncertain_states(self) -> None:
        script = Path("deploy/actual/backup.sh").read_text(encoding="utf-8")
        helpers = script[
            script.index("declare -A paused_services=()"):script.index("actual_state=")
        ]
        for mode, expected_reason in (
            ("inspect_error", "inspect_error"),
            ("exited", "container_not_running"),
            ("partial_unpause", "unpause_failed"),
        ):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                bin_dir = root / "bin"
                bin_dir.mkdir()
                log = root / "docker.log"
                docker = bin_dir / "docker"
                docker.write_text(
                    "#!/usr/bin/env bash\n"
                    "set -euo pipefail\n"
                    "name=\"${@: -1}\"\n"
                    "case \"${1}\" in\n"
                    "  inspect)\n"
                    "    case \"${STUB_MODE}\" in\n"
                    "      inspect_error) exit 1 ;;\n"
                    "      exited) echo exited ;;\n"
                    "      partial_unpause) echo paused ;;\n"
                    "    esac\n"
                    "    ;;\n"
                    "  unpause)\n"
                    "    echo \"unpause:${2}\" >> \"${STUB_LOG}\"\n"
                    "    [[ \"${STUB_MODE}\" != partial_unpause ]]\n"
                    "    ;;\n"
                    "esac\n",
                    encoding="utf-8",
                )
                docker.chmod(0o755)
                harness = (
                    "set -euo pipefail\n"
                    f"export PATH={bin_dir}:$PATH STUB_MODE={mode} STUB_LOG={log}\n"
                    f"{helpers}\n"
                    "paused_services[finance-actual]=paused\n"
                    "if resume_services; then exit 1; fi\n"
                    "[[ \"${paused_services[finance-actual]}\" == unknown ]]\n"
                )
                result = subprocess.run(
                    ["bash", "-c", harness],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f'"reason":"{expected_reason}"', result.stderr)
                if mode == "partial_unpause":
                    self.assertEqual(
                        log.read_text(encoding="utf-8").splitlines(),
                        ["unpause:finance-actual"],
                    )
                else:
                    self.assertFalse(log.exists())

    def test_legacy_ingestion_bridge_is_absent(self) -> None:
        for path in (
            Path("finance_tracker/ingestion_jobs.py"),
            Path(".github/workflows/actual-ingestion-image.yml"),
            Path("scripts/push-actual-ingestion-job.ps1"),
            Path("scripts/get-actual-ingestion-job.ps1"),
        ):
            self.assertFalse(path.exists(), str(path))
        self.assertFalse(any(Path("apps/actual-ingestion").glob("**/*")))
        self.assertFalse(any(Path("deploy/ingestion").glob("**/*")))

    def test_deployment_config_contains_no_host_specific_bridge(self) -> None:
        cashback_script = Path("scripts/invoke-cashback-endpoint.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("deployment.local.json", cashback_script)
        self.assertIn("FINANCE_DEPLOYMENT_CONFIG", cashback_script)
        tracked = Path("config/deployment.json").read_text(encoding="utf-8")
        self.assertNotIn("172.20.10.20", tracked)
        self.assertNotIn("actual_ingestion", tracked)

    def test_backup_quiesces_only_authoritative_data_services(self) -> None:
        script = Path("deploy/actual/backup.sh").read_text(encoding="utf-8")
        self.assertNotIn("docker compose", script)
        self.assertIn("pause_service finance-actual", script)
        self.assertIn("pause_service finance-cashback-control", script)
        self.assertNotIn("finance-actual-ingestion", script)
        self.assertNotIn("ingestion-data", script)
        self.assertIn("sha256sum finance-data.tar.gz > SHA256SUMS", script)
        self.assertIn("sha256sum -c SHA256SUMS", script)
        self.assertIn('python3 "${VERIFY_SCRIPT}"', script)
        self.assertIn('python3 "${SANITIZE_SCRIPT}"', script)
        self.assertIn("docker exec finance-cashback-control python apps/cashback-control/probe_health.py", script)
        self.assertNotIn("CASHBACK_INGEST_TOKEN", script)
        self.assertIn('"schema_version":4', script)
        self.assertIn("--exclude='pre-deploy-*.sqlite3*'", script)
        self.assertIn("find \"${payload}\" -type f", script)
        self.assertIn(
            '"excluded_paths":["cashback-data/pre-deploy-*.sqlite3*"]',
            script,
        )
        self.assertIn("excluded_data", script)
        self.assertIn("--write-receipt", script)

        service = Path("deploy/actual/finance-backup.service").read_text(
            encoding="utf-8"
        )
        self.assertIn("KillMode=process", service)

    def test_actual_runtime_identity_uses_current_storage_contracts(self) -> None:
        compose = Path("deploy/actual/compose.yaml").read_text(encoding="utf-8")
        backup = Path("deploy/actual/backup.sh").read_text(encoding="utf-8")

        self.assertIn("container_name: finance-actual\n", compose)
        self.assertIn('"actual":"finance-actual"', backup)
        self.assertIn("/opt/stacks/finance-actual", backup)
        self.assertIn("/opt/backups/finance-actual", backup)

    def test_authored_deployment_files_use_current_actual_identity(self) -> None:
        # Build the superseded name without embedding it in the source so a
        # repository-wide stale-identity scan can remain a useful guard.
        old_suffix = "".join(("p", "o", "c"))
        old_project = "-".join(("finance", "actual", old_suffix))
        old_stack = f"/opt/stacks/{old_project}"
        old_backup = f"/opt/backups/{old_project}"
        authored_roots = (Path(".github"), Path("deploy"), Path("config"), Path("docs"))
        authored_files = [
            path
            for root in authored_roots
            for path in root.rglob("*")
            if path.is_file()
            and path.suffix.lower()
            in {".json", ".md", ".mjs", ".py", ".sh", ".txt", ".yml", ".yaml"}
        ]
        authored_files.extend((Path("README.md"), Path("AGENTS.md")))
        for path in authored_files:
            with self.subTest(path=path):
                contents = path.read_text(encoding="utf-8")
                self.assertNotIn(old_project, contents)
                self.assertNotIn(old_stack, contents)
                self.assertNotIn(old_backup, contents)

    def test_finance_services_follow_podman_boot_restart(self) -> None:
        expected_ordering = {
            Path("deploy/actual/finance-backup.service"):
                "After=podman-restart.service",
            Path("deploy/finance-monitor/finance-health-monitor.service"):
                "After=podman-restart.service network-online.target",
        }

        for path, expected_after in expected_ordering.items():
            with self.subTest(path=path):
                service = path.read_text(encoding="utf-8")
                after_lines = [
                    line for line in service.splitlines() if line.startswith("After=")
                ]
                self.assertEqual(after_lines, [expected_after])
                self.assertNotIn("docker.service", service)

    def test_health_monitor_repairs_only_owned_services(self) -> None:
        script = Path("deploy/finance-monitor/finance-health-monitor.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("flock -n 9", script)
        self.assertIn(
            'recover_container finance-actual "${ACTUAL_STACK_DIR}" finance-actual actual',
            script,
        )
        self.assertIn(
            'ensure_service finance-actual-proxy "${ACTUAL_STACK_DIR}" finance-actual actual-proxy',
            script,
        )
        self.assertIn("finance-cashback cashback-control", script)
        self.assertNotIn("finance-ingestion", script)
        self.assertNotIn("finance-actual-ingestion", script)
        self.assertIn("--pull never", script)
        self.assertNotIn("docker compose down", script)
        self.assertNotIn("docker compose pull", script)
        probe_body = script.split("probe() {", 1)[1].split("probe_twice()", 1)[0]
        self.assertIn("docker exec \"${container}\" python apps/cashback-control/probe_health.py", probe_body)
        self.assertNotIn("5010/api/health", probe_body)
        self.assertIn("finance-cashback-control || failed=1", script)
        self.assertEqual(script.count('docker restart "${name}"'), 1)
        self.assertIn("backup_stale", script)
        self.assertIn("backup_unverified", script)

    def test_health_monitor_docker_children_cannot_observe_backup_lock_fd(self) -> None:
        script = Path("deploy/finance-monitor/finance-health-monitor.sh")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            actual_stack = root / "actual"
            cashback_stack = root / "cashback"
            backup_root = root / "backups"
            actual_stack.mkdir()
            cashback_stack.mkdir()
            backup = backup_root / "20260824T000000Z"
            backup.mkdir(parents=True)
            (backup / "verification.json").write_text(
                '{"status":"ok","backup":"20260824T000000Z"}\n',
                encoding="utf-8",
            )
            fd_log = root / "docker-fd.log"
            (bin_dir / "readlink").write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "path=\"${@: -1}\"\n"
                "case \"${path}\" in\n"
                f"  {actual_stack}) printf '%s\\n' /opt/stacks/finance-actual ;;\n"
                f"  {cashback_stack}) printf '%s\\n' /opt/stacks/finance-cashback ;;\n"
                f"  {backup_root}) printf '%s\\n' /opt/backups/finance-actual ;;\n"
                "  *) printf '%s\\n' \"${path}\" ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            (bin_dir / "docker").write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "if [[ -e /proc/self/fd/9 ]]; then printf 'inherited\\n' >> \"${STUB_FD_LOG}\"; else printf 'closed\\n' >> \"${STUB_FD_LOG}\"; fi\n"
                "case \"${1}\" in\n"
                "  inspect) printf 'running\\n' ;;\n"
                "  exec) printf '{\"status\":\"ok\"}\\n' ;;\n"
                "  restart|compose) ;;\n"
                "  *) exit 1 ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            (bin_dir / "curl").write_text(
                "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8"
            )
            for executable in ("readlink", "docker", "curl"):
                (bin_dir / executable).chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{bin_dir}:{environment['PATH']}",
                    "FINANCE_ACTUAL_STACK_DIR": str(actual_stack),
                    "FINANCE_CASHBACK_STACK_DIR": str(cashback_stack),
                    "FINANCE_BACKUP_ROOT": str(backup_root),
                    "STUB_FD_LOG": str(fd_log),
                }
            )
            result = self._run_root_fixture(script, root, environment)
            self.assertEqual(result.returncode, 0, result.stderr)
            fd_observations = fd_log.read_text(encoding="utf-8")
            self.assertIn("closed\n", fd_observations)
            self.assertNotIn("inherited\n", fd_observations)

    def test_cashback_deploy_fetches_exact_sha_without_checkout_action(self) -> None:
        workflow = Path(".github/workflows/cashback-image.yml").read_text(
            encoding="utf-8"
        )
        deploy = workflow.split("\n  deploy:\n", 1)[1]
        self.assertNotIn("uses: actions/checkout", deploy)
        self.assertIn("Fetch exact deployment source", deploy)
        self.assertIn('fetch --no-tags --depth 1 origin "$GITHUB_SHA"', deploy)
        self.assertIn('test "$(git -C "$source_dir" rev-parse HEAD)" = "$GITHUB_SHA"', deploy)

    def test_cashback_build_and_ci_use_the_reviewed_uv_lock(self) -> None:
        dockerfile = Path("apps/cashback-control/Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY pyproject.toml uv.lock README.md ./", dockerfile)
        self.assertIn("uv sync --frozen --no-dev --no-cache", dockerfile)
        self.assertNotIn("pip install", dockerfile)
        workflow = Path(".github/workflows/cashback-image.yml").read_text(encoding="utf-8")
        runner = Path("scripts/run-validation.sh").read_text(encoding="utf-8")
        self.assertIn('"uv.lock"', workflow)
        self.assertIn("scripts/run-validation.sh", workflow)
        self.assertIn("uv sync --frozen --extra statements --extra test", runner)
        self.assertIn("uv run --frozen python -m unittest", runner)
        self.assertIn("actual-session-offline-integration.mjs", runner)
        self.assertNotIn("pip install", workflow + runner)

    def test_promotion_workflows_run_checks_without_promoting_from_pull_requests(self) -> None:
        phase1 = Path(".github/workflows/phase1-finance-artifacts.yml").read_text(
            encoding="utf-8"
        )
        phase1_triggers = phase1.split("\npermissions:", 1)[0]
        self.assertIn("\n  pull_request:", phase1_triggers)
        self.assertNotIn('"codex/**"', phase1_triggers)
        self.assertIn("branches: [main]", phase1_triggers)
        self.assertIn("  workflow_dispatch:\n", phase1_triggers)
        self.assertIn('".github/workflows/phase1-finance-artifacts.yml"', phase1_triggers)

        cashback = Path(".github/workflows/cashback-image.yml").read_text(encoding="utf-8")
        cashback_triggers = cashback.split("\npermissions:", 1)[0]
        self.assertIn("\n  pull_request:", cashback_triggers)
        self.assertNotIn("codex/**", cashback_triggers)
        self.assertIn("      - main\n", cashback_triggers)
        self.assertIn('      - "v*"\n', cashback_triggers)
        self.assertIn("  workflow_dispatch:\n", cashback_triggers)
        self.assertIn('      - "uv.lock"\n', cashback_triggers)
        self.assertIn(
            "if: github.event_name == 'push' || (github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main')",
            cashback,
        )
        self.assertIn(
            "if: github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main' && inputs.deploy == true",
            cashback,
        )

    def test_global_ci_uses_the_reviewed_uv_lock(self) -> None:
        workflow = Path(".github/workflows/validate.yml").read_text(encoding="utf-8")
        runner = Path("scripts/run-validation.sh").read_text(encoding="utf-8")
        self.assertIn("astral-sh/setup-uv@v6", workflow)
        self.assertIn('version: "0.12.5"', workflow)
        self.assertIn("scripts/run-validation.sh", workflow)
        self.assertIn("uv sync --frozen --extra statements --extra test", runner)
        self.assertIn("uv run --frozen python -m unittest", runner)
        self.assertNotIn("pip install", workflow + runner)

    def test_cashback_stale_window_allows_daily_morning_ingestion(self) -> None:
        compose = Path("deploy/cashback/compose.yaml").read_text(encoding="utf-8")
        self.assertIn('CASHBACK_STALE_AFTER_MINUTES: "1560"', compose)
        self.assertIn(
            'test: ["CMD", "python", "apps/cashback-control/probe_health.py"]',
            compose,
        )
        self.assertNotIn("CASHBACK_INGEST_TOKEN']}),", compose)

    def test_cashback_browser_access_uses_private_origin_contract(self) -> None:
        compose = Path("deploy/cashback/compose.yaml").read_text(encoding="utf-8")
        self.assertIn('"172.20.10.20:5010:5010"', compose)
        self.assertNotIn('"127.0.0.1:5010:5010"', compose)
        self.assertIn(
            "    networks:\n"
            "      finance-runtime:\n"
            "        aliases:\n"
            "          - cashback\n",
            compose,
        )
        self.assertIn("networks:\n  finance-runtime:\n    external: true\n", compose)
        environment = Path("deploy/finance-runtime/finance.env.tpl").read_text(encoding="utf-8")
        for name in (
            "CASHBACK_ACCESS_ISSUER",
            "CASHBACK_ACCESS_AUDIENCE",
            "CASHBACK_ACCESS_JWKS_URL",
        ):
            self.assertIn(name, environment)
        readme = Path("apps/cashback-control/README.md").read_text(encoding="utf-8")
        self.assertIn("Cf-Access-Jwt-Assertion", readme)
        self.assertIn("exactly equals `CASHBACK_PUBLIC_URL`", readme)

    def test_cashback_deployment_uses_container_local_probes_and_installs_sanitizer(self) -> None:
        workflow = Path(".github/workflows/cashback-image.yml").read_text(encoding="utf-8")
        self.assertIn("apps/cashback-control/probe_health.py", workflow)
        self.assertIn("deploy/actual/sanitize-cashback-backup.py", workflow)
        self.assertIn("sudo install -m 0750 deploy/actual/sanitize-cashback-backup.py", workflow)
        self.assertIn("sudo test -x \"$sanitizer\"", workflow)
        self.assertIn("/var/lib/cashback-control/cashback-dashboard.json", workflow)
        self.assertNotIn("curl -fsS http://127.0.0.1:5010/api/health", workflow)
        self.assertNotIn("curl -fsS http://127.0.0.1:5010/api/dashboard", workflow)

        probe = Path("apps/cashback-control/probe_health.py").read_text(encoding="utf-8")
        self.assertIn("os.environ.get(\"CASHBACK_INGEST_TOKEN\", \"\")", probe)
        self.assertNotIn("print(token", probe)

    def test_cashback_deployment_pins_published_digest_and_retains_rollback(self) -> None:
        workflow = Path(".github/workflows/cashback-image.yml").read_text(encoding="utf-8")
        publish = workflow.split("\n  publish:\n", 1)[1].split("\n  deploy:\n", 1)[0]
        deploy = workflow.split("\n  deploy:\n", 1)[1]
        compose = Path("deploy/cashback/compose.yaml").read_text(encoding="utf-8")

        self.assertIn("image_digest: ${{ steps.publish.outputs.digest }}", publish)
        self.assertIn("id: publish", publish)
        self.assertIn(
            "PUBLISHED_IMAGE_DIGEST: ${{ needs.publish.outputs.image_digest }}",
            deploy,
        )
        self.assertIn('image_ref="${IMAGE_NAME}@${PUBLISHED_IMAGE_DIGEST}"', deploy)
        self.assertIn('printf \'IMAGE_REF=%s\\n\' "$image_ref" >> "$GITHUB_ENV"', deploy)
        self.assertIn(
            "\n".join(
                [
                    'sudo env DOCKER_CONFIG="$auth_dir" REGISTRY_AUTH_FILE="$auth_dir/auth.json" \\',
                    '            docker pull "$IMAGE_REF"',
                ]
            ),
            deploy,
        )
        self.assertIn(
            "\n".join(
                [
                    'sudo env DOCKER_CONFIG="$auth_dir" REGISTRY_AUTH_FILE="$auth_dir/auth.json" \\',
                    '            docker login',
                ]
            ),
            deploy,
        )
        self.assertIn(
            "\n".join(
                [
                    'sudo env DOCKER_CONFIG="$auth_dir" REGISTRY_AUTH_FILE="$auth_dir/auth.json" \\',
                    '              docker logout ghcr.io >/dev/null 2>&1 || true',
                ]
            ),
            deploy,
        )
        self.assertIn('trap cleanup_auth EXIT', deploy)
        self.assertIn('test "$image" = "$IMAGE_REF"', deploy)
        self.assertIn("{{range .RepoDigests}}{{println .}}{{end}}", deploy)
        self.assertIn('awk -v expected="$IMAGE_REF"', deploy)
        self.assertIn("$0 == expected", deploy)
        self.assertIn("found != 1", deploy)
        self.assertNotIn("{{index .RepoDigests 0}}", deploy)
        self.assertIn('test "$resolved" = "$IMAGE_REF"', deploy)
        self.assertIn('"$stack/compose.yaml.pre-${stamp}"', deploy)
        self.assertIn('"$stack/.env.rollback-${stamp}"', deploy)
        self.assertIn('running_image_id="$(sudo docker inspect finance-cashback-control --format \'{{.Image}}\')"', deploy)
        self.assertIn("mapfile -t rollback_candidates", deploy)
        self.assertIn('awk -v prefix="${IMAGE_NAME}@sha256:"', deploy)
        self.assertIn('rollback_image_ref="${rollback_candidates[0]}"', deploy)
        self.assertIn('rollback_image_ref', deploy)
        self.assertIn('CASHBACK_IMAGE=%s\\n', deploy)
        self.assertNotIn("$IMAGE_NAME:main", deploy)

        expected = (
            "ghcr.io/srobroek/finance-statement-tracker-cashback-control@sha256:"
            + "b" * 64
        )
        candidates = "\n".join(
            (
                "ghcr.io/srobroek/finance-statement-tracker-cashback-control@sha256:"
                + "a" * 64,
                expected,
                "ghcr.io/srobroek/finance-statement-tracker-cashback-control@sha256:"
                + "c" * 64,
                "ghcr.io/other/cashback@sha256:" + "d" * 64,
            )
        )
        selected = subprocess.run(
            [
                "awk",
                "-v",
                f"expected={expected}",
                '$0 == expected { found += 1 } END { if (found != 1) exit 1; print expected }',
            ],
            input=candidates,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(selected.returncode, 0, selected.stderr)
        self.assertEqual(selected.stdout.strip(), expected)

        self.assertIn(
            "image: ${CASHBACK_IMAGE:?CASHBACK_IMAGE must be the published immutable image reference}",
            compose,
        )
        self.assertIn("pull_policy: never", compose)
        self.assertNotIn(":main", compose)


if __name__ == "__main__":
    unittest.main()
