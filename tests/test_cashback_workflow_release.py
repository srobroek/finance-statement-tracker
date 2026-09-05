from __future__ import annotations

import unittest
from pathlib import Path


class CashbackWorkflowReleaseTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]
    WORKFLOW = ROOT / ".github" / "workflows" / "cashback-image.yml"

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = cls.WORKFLOW.read_text(encoding="utf-8")
        cls.trigger = cls.text.split("\npermissions:", 1)[0]
        cls.publish = cls.text.split("\n  publish:\n", 1)[1].split(
            "\n  deploy:\n", 1
        )[0]
        cls.deploy = cls.text.split("\n  deploy:\n", 1)[1]

    def test_pull_requests_run_validation_without_publishing(self) -> None:
        self.assertIn("\n  pull_request:\n", self.trigger)
        pull_request = self.trigger.split("\n  push:\n", 1)[0]
        self.assertIn("    branches:\n      - main\n", pull_request)
        self.assertIn('      - "apps/cashback-control/**"\n', pull_request)
        self.assertIn('      - "uv.lock"\n', pull_request)

        self.assertIn(
            "if: github.event_name == 'push' || (github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main')",
            self.publish,
        )
        self.assertNotIn("pull_request", self.publish)
        self.assertIn("          push: true\n", self.publish)

    def test_manual_production_deploy_is_opt_in_on_main(self) -> None:
        dispatch = self.trigger.split("  workflow_dispatch:\n", 1)[1]
        self.assertIn("    inputs:\n", dispatch)
        self.assertRegex(dispatch, r"(?m)^      deploy:\n")
        self.assertRegex(dispatch, r"(?m)^        required: true\n")
        self.assertRegex(dispatch, r"(?m)^        default: false\n")
        self.assertRegex(dispatch, r"(?m)^        type: boolean\n")

        self.assertIn(
            "if: github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main' && inputs.deploy == true",
            self.deploy,
        )
        self.assertNotIn("if: github.ref == 'refs/heads/main'\n", self.deploy)

    def test_publish_and_deploy_permissions_are_scoped(self) -> None:
        self.assertIn("permissions:\n  contents: read\n  packages: read\n", self.text)
        self.assertIn("    permissions:\n      contents: read\n      packages: write\n", self.publish)
        self.assertIn("    permissions:\n      contents: read\n      packages: read\n", self.deploy)

    def test_deploy_uses_one_docker_image_store_and_digest_identity(self) -> None:
        self.assertNotIn("podman", self.deploy.casefold())
        self.assertNotIn("{{.Digest}}", self.deploy)
        self.assertIn("{{range .RepoDigests}}{{println .}}{{end}}", self.deploy)
        self.assertIn('mapfile -t rollback_candidates < <(', self.deploy)
        self.assertIn('rollback_image_ref="${rollback_candidates[0]}"', self.deploy)
        self.assertIn('awk -v expected="$IMAGE_REF"', self.deploy)
        self.assertIn("$0 == expected", self.deploy)
        self.assertIn("found != 1", self.deploy)
        self.assertIn(
            "sudo docker compose -p finance-cashback up -d --pull never --force-recreate cashback-control",
            self.deploy,
        )

    def test_deploy_does_not_remove_an_unrelated_container(self) -> None:
        self.assertNotRegex(self.deploy, r"(?:docker|podman)\s+rm\s+-f")
        self.assertNotIn("docker compose down", self.deploy)
        self.assertIn("finance-cashback-control", self.deploy)
        self.assertIn("deploy/cashback/predeploy-backup.py", self.deploy)

    def test_verified_backup_precedes_all_configuration_mutations(self) -> None:
        backup, install = self.deploy.split("      - name: Install matching deployment configuration after verified backup", 1)
        self.assertIn("sudo docker stop finance-cashback-control", backup)
        self.assertIn("sudo python3 deploy/cashback/predeploy-backup.py", backup)
        self.assertNotIn("sudo install -m", backup)
        self.assertNotIn("sudo tee", backup)
        self.assertIn("CASHBACK_ROLLBACK_BACKUP/verification.json", install)
        self.assertNotIn("sanitize-cashback-backup.py", self.deploy)
        self.assertNotIn("sqlite3.connect", self.deploy)

    def test_image_ref_is_digest_pinned_before_pull_and_readback(self) -> None:
        self.assertIn(
            'if [[ ! "$PUBLISHED_IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]; then',
            self.deploy,
        )
        self.assertIn('image_ref="${IMAGE_NAME}@${PUBLISHED_IMAGE_DIGEST}"', self.deploy)
        self.assertIn(
            "\n".join(
                [
                    'sudo env DOCKER_CONFIG="$auth_dir" REGISTRY_AUTH_FILE="$auth_dir/auth.json" \\',
                    '            docker pull "$IMAGE_REF"',
                ]
            ),
            self.deploy,
        )
        self.assertIn(
            "\n".join(
                [
                    'sudo env DOCKER_CONFIG="$auth_dir" REGISTRY_AUTH_FILE="$auth_dir/auth.json" \\',
                    '              docker logout ghcr.io >/dev/null 2>&1 || true',
                ]
            ),
            self.deploy,
        )
        self.assertIn('image="$(sudo docker inspect finance-cashback-control --format \'{{.Config.Image}}\')"', self.deploy)
        self.assertIn('test "$image" = "$IMAGE_REF"', self.deploy)
        self.assertIn('test "$resolved" = "$IMAGE_REF"', self.deploy)


if __name__ == "__main__":
    unittest.main()
