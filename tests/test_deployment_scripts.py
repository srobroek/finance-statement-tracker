import unittest
from pathlib import Path


class DeploymentScriptTests(unittest.TestCase):
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
        script = Path("deploy/actual-poc/backup.sh").read_text(encoding="utf-8")
        self.assertNotIn("docker compose", script)
        self.assertIn("docker pause finance-actual-poc", script)
        self.assertIn("docker pause finance-cashback-control", script)
        self.assertNotIn("finance-actual-ingestion", script)
        self.assertNotIn("ingestion-data", script)
        self.assertIn("sha256sum finance-data.tar.gz > SHA256SUMS", script)
        self.assertIn("sha256sum -c SHA256SUMS", script)
        self.assertIn('python3 "${VERIFY_SCRIPT}"', script)
        self.assertIn("--write-receipt", script)

        service = Path("deploy/actual-poc/finance-backup.service").read_text(
            encoding="utf-8"
        )
        self.assertIn("KillMode=process", service)

    def test_health_monitor_repairs_only_owned_services(self) -> None:
        script = Path("deploy/finance-monitor/finance-health-monitor.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("flock -n 9", script)
        self.assertIn("finance-actual-poc actual", script)
        self.assertIn("finance-actual-poc actual-proxy", script)
        self.assertIn("finance-cashback cashback-control", script)
        self.assertNotIn("finance-ingestion", script)
        self.assertNotIn("finance-actual-ingestion", script)
        self.assertIn("--pull never", script)
        self.assertNotIn("docker compose down", script)
        self.assertNotIn("docker compose pull", script)
        self.assertIn("backup_stale", script)
        self.assertIn("backup_unverified", script)

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
        self.assertIn('"uv.lock"', workflow)
        self.assertIn("uv sync --frozen --extra statements --extra test", workflow)
        self.assertIn("uv run --frozen python -m unittest", workflow)

    def test_global_ci_uses_the_reviewed_uv_lock(self) -> None:
        workflow = Path(".github/workflows/validate.yml").read_text(encoding="utf-8")
        self.assertIn("astral-sh/setup-uv@v6", workflow)
        self.assertIn('version: "0.12.5"', workflow)
        self.assertIn("uv sync --frozen --extra statements --extra test", workflow)
        self.assertIn("uv run --frozen python -m unittest", workflow)
        self.assertNotIn("pip install", workflow)

    def test_cashback_stale_window_allows_daily_morning_ingestion(self) -> None:
        compose = Path("deploy/cashback/compose.yaml").read_text(encoding="utf-8")
        self.assertIn('CASHBACK_STALE_AFTER_MINUTES: "1560"', compose)

    def test_cashback_browser_access_uses_private_origin_contract(self) -> None:
        compose = Path("deploy/cashback/compose.yaml").read_text(encoding="utf-8")
        self.assertIn('"127.0.0.1:5010:5010"', compose)
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


if __name__ == "__main__":
    unittest.main()
