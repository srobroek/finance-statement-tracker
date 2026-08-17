import unittest
from pathlib import Path


class DeploymentScriptTests(unittest.TestCase):
    def test_actual_ingestion_upload_is_private_and_readable_by_worker(self) -> None:
        script = Path("scripts/push-actual-ingestion-job.ps1").read_text(encoding="utf-8")
        self.assertIn("install -o 10002 -g 10002 -m 0600", script)
        self.assertNotIn("install -m 0600 '$remoteTemporary'", script)
        self.assertIn("source_message_id = $SourceMessageId", script)
        self.assertIn("source_attachment_id = $SourceAttachmentId", script)
        self.assertIn("source_filename = [IO.Path]::GetFileName($resolvedInput)", script)
        self.assertIn("$job.ai_responses = @($aiResponses)", script)
        self.assertIn("ai_handoff_complete = $AIHandoffComplete.IsPresent", script)
        self.assertIn("ConvertTo-Json -Depth 20 -Compress", script)

    def test_deployment_helpers_support_ignored_local_and_environment_overrides(self) -> None:
        actual_script = Path("scripts/push-actual-ingestion-job.ps1").read_text(encoding="utf-8")
        cashback_script = Path("scripts/invoke-cashback-endpoint.ps1").read_text(encoding="utf-8")
        for script in (actual_script, cashback_script):
            self.assertIn("deployment.local.json", script)
            self.assertIn("FINANCE_DEPLOYMENT_CONFIG", script)
        tracked = Path("config/deployment.json").read_text(encoding="utf-8")
        self.assertNotIn("172.20.10.20", tracked)

    def test_backup_restarts_existing_independent_containers_without_compose_pull(self) -> None:
        script = Path("deploy/actual-poc/backup.sh").read_text(encoding="utf-8")
        self.assertNotIn("docker compose", script)
        self.assertNotIn("docker-compose", script)
        self.assertIn("docker start finance-actual-poc", script)
        self.assertIn("docker start finance-cashback-control", script)
        self.assertIn("docker start finance-actual-ingestion", script)
        self.assertIn('"${payload}/ingestion-data/"', script)
        self.assertIn('"${CASHBACK_STACK_DIR}/compose.yaml"', script)
        self.assertIn("sha256sum -c SHA256SUMS", script)


if __name__ == "__main__":
    unittest.main()
