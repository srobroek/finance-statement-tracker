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


if __name__ == "__main__":
    unittest.main()
