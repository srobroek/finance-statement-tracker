import unittest
from pathlib import Path


class DeploymentScriptTests(unittest.TestCase):
    def test_all_operator_ingestion_wrappers_use_the_guarded_worker(self) -> None:
        wrappers = {
            "statement": Path("scripts/ingest-statement-to-actual.ps1"),
            "capture": Path("scripts/ingest-browser-capture.ps1"),
            "export": Path("scripts/ingest-browser-export.ps1"),
        }
        for name, path in wrappers.items():
            with self.subTest(wrapper=name):
                script = path.read_text(encoding="utf-8")
                self.assertIn("push-actual-ingestion-job.ps1", script)
                self.assertIn("[string]$ActualMode = 'STAGE'", script)
                self.assertIn("AIHandoffComplete = $AIHandoffComplete", script)
                self.assertIn("EvidenceLinksPath = $EvidenceLinksPath", script)
                self.assertNotIn("actualctl.mjs", script)
                self.assertNotIn("Read-Host", script)
                self.assertNotIn("[switch]$Commit", script)

        statement = wrappers["statement"].read_text(encoding="utf-8")
        self.assertIn("Outlook statements require -SourceMessageId", statement)
        self.assertIn("Outlook statements require -SourceAttachmentId", statement)
        self.assertFalse(Path("integrations/actual/import.mjs").exists())
        bridge_files = list(Path("integrations/actual").glob("*.mjs"))
        direct_importers = [
            path.name
            for path in bridge_files
            if "actual.importTransactions" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(direct_importers, ["actualctl.mjs"])

    def test_actual_ingestion_upload_is_private_and_readable_by_worker(self) -> None:
        script = Path("scripts/push-actual-ingestion-job.ps1").read_text(encoding="utf-8")
        self.assertIn("install -o 10002 -g 10002 -m 0600", script)
        self.assertNotIn("install -m 0600 '$remoteTemporary'", script)
        self.assertIn("source_message_id = $SourceMessageId", script)
        self.assertIn("[string]$EvidenceLinksPath", script)
        self.assertIn("$job.evidence_links = @($evidenceLinks)", script)
        self.assertIn("source_attachment_id = $SourceAttachmentId", script)
        self.assertIn("source_filename = [IO.Path]::GetFileName($resolvedInput)", script)
        self.assertIn("$job.ai_responses = @($aiResponses)", script)
        self.assertIn("ai_handoff_complete = $AIHandoffComplete.IsPresent", script)
        self.assertIn("$response = @($payload | & ssh", script)
        self.assertIn("Actual ingestion job failed with exit code ${remoteExitCode}: $detail", script)
        self.assertIn("ConvertTo-Json -Depth 20 -Compress", script)

        dockerfile = Path("apps/actual-ingestion/Dockerfile").read_text(encoding="utf-8")
        workflow = Path(".github/workflows/actual-ingestion-image.yml").read_text(encoding="utf-8")
        self.assertIn("ARG FINANCE_PIPELINE_REVISION", dockerfile)
        self.assertIn("FINANCE_PIPELINE_REVISION=${FINANCE_PIPELINE_REVISION}", dockerfile)
        self.assertIn("FINANCE_PIPELINE_REVISION=${{ github.sha }}", workflow)

    def test_ingestion_job_can_be_resumed_without_exposing_the_token(self) -> None:
        script = Path("scripts/get-actual-ingestion-job.ps1").read_text(encoding="utf-8")

        self.assertIn("^[0-9a-f]{24}$", script)
        self.assertIn("submit_local.py --job-id $JobId", script)
        self.assertIn("[switch]$AIHandoffOnly", script)
        self.assertIn("$job.ai_handoff | ConvertTo-Json -Depth 30", script)
        self.assertIn("deployment.local.json", script)
        self.assertIn("FINANCE_DEPLOYMENT_CONFIG", script)
        self.assertNotIn("FINANCE_INGEST_TOKEN", script)

    def test_deployment_helpers_support_ignored_local_and_environment_overrides(self) -> None:
        actual_script = Path("scripts/push-actual-ingestion-job.ps1").read_text(encoding="utf-8")
        cashback_script = Path("scripts/invoke-cashback-endpoint.ps1").read_text(encoding="utf-8")
        for script in (actual_script, cashback_script):
            self.assertIn("deployment.local.json", script)
            self.assertIn("FINANCE_DEPLOYMENT_CONFIG", script)
        tracked = Path("config/deployment.json").read_text(encoding="utf-8")
        self.assertNotIn("172.20.10.20", tracked)

    def test_backup_quiesces_existing_independent_containers_without_recreation(self) -> None:
        script = Path("deploy/actual-poc/backup.sh").read_text(encoding="utf-8")
        self.assertNotIn("docker compose", script)
        self.assertNotIn("docker-compose", script)
        self.assertNotIn("docker start", script)
        self.assertNotIn("docker stop", script)
        self.assertIn("docker pause finance-actual-poc", script)
        self.assertIn("docker pause finance-cashback-control", script)
        self.assertIn("docker pause finance-actual-ingestion", script)
        self.assertIn("docker unpause finance-actual-poc", script)
        self.assertIn('"${payload}/ingestion-data/"', script)
        self.assertIn('"${CASHBACK_STACK_DIR}/compose.yaml"', script)
        self.assertIn("sha256sum finance-data.tar.gz > SHA256SUMS", script)
        self.assertNotIn('sha256sum "${working}/finance-data.tar.gz"', script)
        self.assertIn("sha256sum -c SHA256SUMS", script)

        service = Path("deploy/actual-poc/finance-backup.service").read_text(encoding="utf-8")
        self.assertIn("KillMode=process", service)

    def test_health_monitor_repairs_only_independent_owned_services(self) -> None:
        script = Path("deploy/finance-monitor/finance-health-monitor.sh").read_text(encoding="utf-8")
        self.assertIn('flock -n 9', script)
        self.assertIn('finance-actual-poc actual', script)
        self.assertIn('finance-actual-poc actual-proxy', script)
        self.assertIn('finance-cashback cashback-control', script)
        self.assertIn('finance-ingestion actual-ingestion', script)
        self.assertIn('--pull never', script)
        self.assertNotIn('docker compose down', script)
        self.assertNotIn('docker compose pull', script)
        self.assertNotIn('rm -f', script)
        self.assertIn('backup_stale', script)

        timer = Path("deploy/finance-monitor/finance-health-monitor.timer").read_text(encoding="utf-8")
        self.assertIn("OnUnitActiveSec=5m", timer)
        service = Path("deploy/finance-monitor/finance-health-monitor.service").read_text(encoding="utf-8")
        self.assertIn("KillMode=process", service)

    def test_self_hosted_deploys_fetch_exact_sha_without_checkout_action(self) -> None:
        for path in (
            Path(".github/workflows/actual-ingestion-image.yml"),
            Path(".github/workflows/cashback-image.yml"),
        ):
            workflow = path.read_text(encoding="utf-8")
            deploy = workflow.split("\n  deploy:\n", 1)[1]
            self.assertNotIn("uses: actions/checkout", deploy)
            self.assertIn("Fetch exact deployment source", deploy)
            self.assertIn('fetch --no-tags --depth 1 origin "$GITHUB_SHA"', deploy)
            self.assertIn('test "$(git -C "$source_dir" rev-parse HEAD)" = "$GITHUB_SHA"', deploy)
            self.assertIn("$RUNNER_TEMP/finance-deploy-", deploy)
            self.assertIn('cd "$DEPLOY_SOURCE"', deploy)

    def test_cashback_stale_window_allows_daily_morning_ingestion(self) -> None:
        compose = Path("deploy/cashback/compose.yaml").read_text(encoding="utf-8")

        self.assertIn('CASHBACK_STALE_AFTER_MINUTES: "1560"', compose)


if __name__ == "__main__":
    unittest.main()
