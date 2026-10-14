import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import topify_secretary


class SecretaryMailboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "supervision/employees").mkdir(parents=True)
        self.employee = {"employee_id": "producer-0001", "parent_id": "supervisor",
                         "role": "producer", "task_id": "physics/task-1",
                         "employment_status": "running"}
        (self.root / "supervision/employees/producer-0001.json").write_text(
            json.dumps(self.employee), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_reports_are_held_then_sent_as_one_digest(self):
        report = {"status": "heartbeat", "summary": "working", "employee_id": "producer-0001"}
        receipt = {"attempt": 1, "report_status": "heartbeat", "report_path": "/tmp/report.json",
                   "report_sha256": "a" * 64, "recorded_at": "2026-10-14T00:00:00+00:00"}
        topify_secretary.ingest(self.root, self.employee, report, receipt)
        self.assertEqual(topify_secretary.status(self.root)["pending_count"], 1)
        sent = []
        result = topify_secretary.flush(self.root, lambda *args: sent.append(args) or {"status": "admitted"}, force=True)
        self.assertEqual(result["status"], "admitted")
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][1:], ("secretary", "secretary-digest"))
        self.assertEqual(topify_secretary.status(self.root)["pending_count"], 0)

    def test_archived_employee_report_is_not_forwarded_as_live_work(self):
        self.employee["employment_status"] = "fired"
        (self.root / "supervision/employees/producer-0001.json").write_text(
            json.dumps(self.employee), encoding="utf-8")
        report = {"status": "completed", "summary": "discarded", "employee_id": "producer-0001"}
        receipt = {"attempt": 1, "report_status": "completed", "report_path": "/tmp/report.json",
                   "report_sha256": "b" * 64, "recorded_at": "2026-10-14T00:00:00+00:00"}
        topify_secretary.ingest(self.root, self.employee, report, receipt)
        sent = []
        topify_secretary.flush(self.root, lambda *args: sent.append(args) or {"status": "admitted"}, force=True)
        self.assertIn("archived_or_discarded=1", sent[0][0])
        self.assertIn("do not reopen", sent[0][0])


if __name__ == "__main__":
    unittest.main()
