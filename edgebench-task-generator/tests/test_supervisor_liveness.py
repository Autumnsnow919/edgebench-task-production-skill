"""Worker-to-supervisor heartbeat/watchdog control-plane fixtures."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import topify_common as common
import topify_opencode as runtime
import topify_seed
import topify_supervisor as ledger


class SupervisorLivenessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.campaign = self.root / "campaign"
        topify_seed.initialize_campaign(
            self.campaign, Path(__file__).resolve().parents[1] / "examples/campaign-config.json")
        ledger.initialize(self.campaign)
        self.employee, _ = ledger.hire(self.campaign, "producer-live", "producer", "opencode-task")
        self.supervisor_sid = "ses_supervisor1234567890123456"
        self.employee_sid = "ses_employee1234567890123456"
        manifest = common.load_json(self.campaign / "supervision/manifest.json")
        common.write_latest_json(self.campaign / "supervision/manifest.json", {
            **manifest, "supervisor_session_id": self.supervisor_sid,
            "supervisor_session_directory": str(self.campaign),
        })
        self.employee = {**self.employee, "employment_status": "running",
                         "session_id": self.employee_sid,
                         "session_binding": {"session_id": self.employee_sid,
                                              "directory": self.employee["job_root"]}}
        ledger._save_employee(self.campaign, self.employee)

    def response(self, path):
        if path == "/api/session/active":
            return {"data": {}}
        raise AssertionError(path)

    def heartbeat_file(self, status="heartbeat", summary="worker is checking the pinned source"):
        path = Path(self.employee["job_root"]) / "heartbeat.json"
        path.write_text(json.dumps({"employee_id": self.employee["employee_id"], "status": status,
                                    "summary": summary, "stage": "seed-discovery", "revision_index": 0}))
        return path

    def test_employee_report_admits_update_to_bound_supervisor(self):
        report = self.heartbeat_file()
        with patch.object(runtime, "supervisor_runtime", return_value={"can_chat": True, "session_id": self.supervisor_sid}), \
             patch.object(runtime, "send_message", return_value={"status": "sent", "response": {"data": {"id": "msg_heartbeat"}}}) as send:
            value, code = ledger.report(self.campaign, self.employee["employee_id"], report)
        self.assertEqual(code, 0)
        self.assertEqual(value["supervisor_delivery"]["status"], "admitted")
        self.assertTrue(send.called)
        employee = ledger._employee(self.campaign, self.employee["employee_id"])
        self.assertEqual(employee["last_supervisor_delivery"]["inbox_id"], "msg_heartbeat")
        events = (self.campaign / "supervision/events.jsonl").read_text()
        self.assertIn("supervisor_update_delivered", events)

    def test_supervisor_directive_reaches_employee_actual_session(self):
        with patch.object(runtime, "employee_runtime", return_value={"status": "idle", "session_id": self.employee_sid, "can_chat": True}), \
             patch.object(runtime, "send_message", return_value={"status": "sent", "response": {"data": {"id": "msg_directive"}}}) as send:
            value, code = ledger.send_message(
                self.campaign, "employee", self.employee["employee_id"],
                "请恢复心跳并报告当前阻塞。", sender_type="supervisor", sender_id="supervisor")
        self.assertEqual(code, 0)
        self.assertEqual(value["employee_delivery"]["status"], "admitted")
        send.assert_called_once_with(
            self.employee["job_root"], self.employee_sid,
            "Supervisor directive from supervisor; ledger message " + value["message_id"] + ".\n"
            "Acknowledge this directive in your actual session, report what you did and send the next heartbeat.\n\n"
            "请恢复心跳并报告当前阻塞。"
        )

    def test_heartbeat_is_throttled_but_blocked_report_is_delivered(self):
        first = self.heartbeat_file()
        responses = {"data": {"id": "msg_heartbeat"}}
        with patch.object(runtime, "supervisor_runtime", return_value={"can_chat": True, "session_id": self.supervisor_sid}), \
             patch.object(runtime, "send_message", return_value={"status": "sent", "response": responses}) as send:
            ledger.report(self.campaign, self.employee["employee_id"], first)
            ledger.report(self.campaign, self.employee["employee_id"], first)
        self.assertEqual(send.call_count, 1)
        blocked = self.heartbeat_file("blocked", "waiting for an owner-approved source decision")
        with patch.object(runtime, "supervisor_runtime", return_value={"can_chat": True, "session_id": self.supervisor_sid}), \
             patch.object(runtime, "send_message", return_value={"status": "sent", "response": responses}) as send:
            ledger.report(self.campaign, self.employee["employee_id"], blocked)
        self.assertEqual(send.call_count, 1)

    def test_watch_detects_idle_employee_and_notifies_supervisor_without_firing(self):
        old = {**self.employee, "last_report": {"report_status": "heartbeat",
               "report_path": str(self.root / "old.json"), "report_sha256": "old", "recorded_at": "2020-01-01T00:00:00Z"}}
        ledger._save_employee(self.campaign, old)
        with patch.object(runtime, "employee_runtime", return_value={"status": "idle", "session_id": self.employee_sid,
                                                                       "can_chat": True, "outcome": "succeeded"}), \
             patch.object(runtime, "supervisor_runtime", return_value={"can_chat": True, "session_id": self.supervisor_sid}), \
             patch.object(runtime, "send_message", return_value={"status": "sent", "response": {"data": {"id": "msg_watch"}}}) as send:
            value, code = ledger.watch(self.campaign, active={})
        self.assertEqual(code, 0)
        self.assertEqual(value["incident_count"], 1)
        self.assertEqual(value["status"], "admitted")
        self.assertEqual(send.call_count, 1)
        self.assertEqual(ledger._employee(self.campaign, self.employee["employee_id"])["employment_status"], "running")
        watch = common.load_json(self.campaign / "supervision/watch/producer-live.json")
        self.assertEqual(watch["health"]["status"], "stalled")
        self.assertFalse(watch["automatic_fire_allowed"])

    def test_watch_alert_is_deduplicated_until_a_new_report(self):
        old = {**self.employee, "last_report": {"report_status": "heartbeat",
               "report_path": str(self.root / "old.json"), "report_sha256": "old", "recorded_at": "2020-01-01T00:00:00Z"}}
        ledger._save_employee(self.campaign, old)
        with patch.object(runtime, "employee_runtime", return_value={"status": "idle", "session_id": self.employee_sid, "can_chat": True}), \
             patch.object(runtime, "supervisor_runtime", return_value={"can_chat": True, "session_id": self.supervisor_sid}), \
             patch.object(runtime, "send_message", return_value={"status": "sent", "response": {"data": {"id": "msg_watch"}}}) as send:
            ledger.watch(self.campaign, active={})
            ledger.watch(self.campaign, active={})
        self.assertEqual(send.call_count, 1)

    def test_health_does_not_call_waiting_owner_phase_stalled(self):
        waiting = {**self.employee, "employment_status": "awaiting-approval",
                   "last_report": {"report_status": "completed", "recorded_at": "2020-01-01T00:00:00Z"}}
        health = ledger.employee_health(waiting, {"status": "idle"}, stale_after=30)
        self.assertFalse(health["attention"])
        self.assertEqual(health["status"], "waiting")


if __name__ == "__main__":
    unittest.main()
