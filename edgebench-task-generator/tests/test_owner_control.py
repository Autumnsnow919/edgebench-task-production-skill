"""Isolated owner-control fixtures; no production models or processes."""

import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import topify_common as common
import topify_control as control
import topify_opencode as runtime
import topify_seed
import topify_supervisor as ledger
import topify_web


class OwnerControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "campaign"
        topify_seed.initialize_campaign(self.root, Path(__file__).resolve().parents[1] / "examples/campaign-config.json")
        ledger.initialize(self.root)
        self.employee, _ = ledger.hire(self.root, "producer-control", "producer", "opencode-task")
        self.sid = "ses_supervisor1234567890123456"
        self.worker_sid = "ses_worker1234567890123456"
        manifest = common.load_json(self.root / "supervision/manifest.json")
        common.write_latest_json(self.root / "supervision/manifest.json", {**manifest,
            "supervisor_session_id": self.sid, "supervisor_session_directory": str(self.root)})
        self.employee = {**self.employee, "session_id": self.worker_sid, "employment_status": "running",
                         "last_report": {"recorded_at": "2020-01-01T00:00:00Z", "report_sha256": "unchanged"}}
        ledger._save_employee(self.root, self.employee)
        self.report = Path(self.employee["job_root"]) / "candidate-review.json"
        self.report.write_text(json.dumps({"status": "review-needed", "reason": "generic control saturates"}))
        getter = patch.object(runtime, "_get", return_value={"data": {"id": self.sid}})
        patcher = patch.object(runtime, "_patch")
        getter.start(); patcher.start()
        self.addCleanup(getter.stop); self.addCleanup(patcher.stop)

    def pause(self):
        with patch.object(runtime, "supervisor_runtime", return_value={"can_chat": True}), \
                patch.object(runtime, "_get", side_effect=lambda p: {"data":{"id":self.sid}} if p == f"/api/session/{self.sid}" else {"data":[]}), \
                patch.object(runtime, "_post", return_value={"interrupted": True}):
            return control.pause(self.root, "owner takes control")

    def test_pause_stops_only_bound_supervisor_and_parks_only_adapter_worker_messages(self):
        worker = {"id": "msg_workerfixture", "type": "user", "delivery": "steer", "payload": {
            "text": f"watchdog update from supervisor-watchdog; ledger message fixture.\nCampaign: {self.root}\nalert"}}
        _, attempt = common.next_attempt(self.root / "supervision/session-deliveries")
        common.write_new_json(attempt / "delivery.json", {"session_id":self.sid,"sender_type":"watchdog","inbox_id":worker["id"]})
        owner = {"id": "msg_ownerfixture", "type": "user", "delivery": "steer", "payload": {"text": "汇报具体问题"}}
        other = {**worker, "id": "msg_otherfixture", "payload": {"text": worker["payload"]["text"].replace(str(self.root), "/other/campaign")}}
        def get(path):
            if path == f"/api/session/{self.sid}":
                return {"data": {"id": self.sid}}
            return {"data": [worker, owner, other]} if path.endswith("/inbox") else {"data": []}
        with patch.object(runtime, "supervisor_runtime", return_value={"can_chat": True}), \
                patch.object(runtime, "_get", side_effect=get), \
                patch.object(runtime, "_post", return_value={"interrupted": True}) as post, \
                patch.object(runtime, "_patch") as park, patch.object(runtime, "_delete") as cancel:
            value = control.pause(self.root, "owner takes control")
        post.assert_called_once_with(f"/api/session/{self.sid}/interrupt?resume=false", {})
        self.assertEqual(park.call_count, 2)
        self.assertEqual(park.call_args_list[0].args, (f"/api/session/{self.sid}", {"permissions": control.OWNER_ONLY_PERMISSIONS}))
        self.assertEqual(park.call_args_list[1].args, (f"/api/session/{self.sid}/inbox/msg_workerfixture", {"delivery": "queue"}))
        cancel.assert_called_once_with(f"/api/session/{self.sid}/inbox/msg_workerfixture")
        originals = list((self.root / "supervision/held-inbox").glob("attempt-*/original.json"))
        self.assertEqual(common.load_json(originals[0]), worker)
        self.assertFalse(value["worker_channel_open"])
        self.assertEqual(value["runtime"]["parked"], [worker])
        self.assertNotEqual(ledger._employee(self.root, self.employee["employee_id"])["employment_status"], "fired")

    def test_pause_preserves_existing_owner_question_instead_of_cancelling_it(self):
        with patch.object(runtime, "supervisor_runtime", return_value={"can_chat": True}), \
                patch.object(runtime, "_get", side_effect=lambda p: {"data":{"id":self.sid}} if p == f"/api/session/{self.sid}" else {"data": [{"id": "frm_fixture"}]} if p.endswith("/form") else {"data": []}), \
                patch.object(runtime, "_post") as post, patch.object(runtime, "_delete") as delete:
            value = control.pause(self.root, "owner pause")
        post.assert_not_called()
        delete.assert_not_called()
        self.assertIsNone(value["runtime"]["interrupt"])
        self.assertFalse(value["worker_channel_open"])

    def test_reports_are_retained_while_upward_delivery_and_watch_are_closed(self):
        self.pause()
        before = (self.root / "supervision/events.jsonl").read_text()
        with patch.object(runtime, "_get") as get, patch.object(runtime, "send_message") as send:
            watched, _ = ledger.watch(self.root)
            checkpoint = Path(self.employee["job_root"]) / "heartbeat.json"
            checkpoint.write_text(json.dumps({"employee_id": self.employee["employee_id"], "status": "heartbeat", "summary": "real checkpoint"}))
            report, _ = ledger.report(self.root, self.employee["employee_id"], checkpoint)
        self.assertEqual(watched["status"], "held-owner")
        self.assertEqual(report["supervisor_delivery"]["status"], "held-owner")
        send.assert_not_called()
        get.assert_not_called()
        self.assertEqual(ledger._employee(self.root, self.employee["employee_id"])["last_report"]["report_sha256"], common.sha256_file(checkpoint))
        self.assertNotEqual((self.root / "supervision/events.jsonl").read_text(), before)

    def test_pause_blocks_directives_hiring_dispatch_and_does_not_erase_approvals(self):
        self.pause()
        with patch.object(runtime, "send_message") as send:
            for fn in (
                lambda: ledger.send_message(self.root, "employee", self.employee["employee_id"], "stand by", sender_type="supervisor", sender_id="supervisor"),
                lambda: ledger.dispatch(self.root, self.employee["employee_id"]),
                lambda: ledger.hire(self.root, "another-producer", "producer", "opencode-task"),
            ):
                with self.assertRaisesRegex(common.PipelineError, "闸门已关闭"):
                    fn()
        send.assert_not_called()
        self.assertFalse((self.root / "supervision/employees/another-producer.json").exists())

    def test_owner_message_still_reaches_supervisor_without_reopening_worker_channel(self):
        self.pause()
        with patch.object(runtime, "supervisor_runtime", return_value={"can_chat": True, "session_id": self.sid}), \
                patch.object(runtime, "send_message", return_value={"response": {"data": {"id": "msg_owner"}}}) as send:
            value, _ = ledger.send_supervisor_message(self.root, "请具体说明候选问题，不要联系下属。")
        send.assert_called_once()
        self.assertEqual(value["inbox_id"], "msg_owner")
        self.assertFalse(control.state(self.root)["worker_channel_open"])

    def test_waiting_native_question_automatically_closes_channel_and_resume_does_not_override_it(self):
        self.pause()
        control.sync_forms(self.root, [{"form_id": "frm_fixture", "can_reply": True}])
        value = control.resume(self.root)
        self.assertFalse(value["owner_paused"])
        self.assertFalse(value["worker_channel_open"])
        control.sync_forms(self.root, [])
        self.assertTrue(control.state(self.root)["worker_channel_open"])

    def test_missing_initialized_gate_fails_closed_instead_of_resuming_by_default(self):
        self.pause()
        (self.root / "supervision/control.json").unlink()
        value = control.state(self.root)
        self.assertFalse(value["worker_channel_open"])
        self.assertTrue(value["owner_paused"])
        self.assertIn("文件缺失", value["reason"])
        restored = control.resume(self.root)
        self.assertTrue(restored["worker_channel_open"])
        self.assertFalse(restored["permissions_guarded"])

    def test_native_permissions_restore_exact_prior_rules_not_a_permissive_default(self):
        original = [{"action": "shell", "resource": "safe-command", "effect": "ask"}]
        with patch.object(runtime, "supervisor_runtime", return_value={"can_chat": True}), \
                patch.object(runtime, "_get", side_effect=lambda p: {"data":{"id":self.sid,"permissions":original}} if p == f"/api/session/{self.sid}" else {"data":[]}), \
                patch.object(runtime, "_post", return_value={"interrupted":False}), \
                patch.object(runtime, "_patch") as apply:
            paused = control.pause(self.root, "owner pause")
            self.assertTrue(paused["permissions_guarded"])
            restored = control.resume(self.root)
        self.assertTrue(restored["worker_channel_open"])
        self.assertEqual(apply.call_args_list[0].args, (f"/api/session/{self.sid}", {"permissions":control.OWNER_ONLY_PERMISSIONS}))
        self.assertEqual(apply.call_args_list[-1].args, (f"/api/session/{self.sid}", {"permissions":original}))

    def test_default_permission_restore_uses_empty_overrides_not_null_noop(self):
        self.pause()
        with patch.object(runtime, "_patch") as apply:
            value = control.resume(self.root)
        self.assertTrue(value["worker_channel_open"])
        apply.assert_called_once_with(f"/api/session/{self.sid}", {"permissions":[]})

    def answer_request(self, req, label):
        text = f"{req['employee_id']} request_id={req['request_id']} request_sha256={req['request_sha256']} 如何处置？"
        questions = [{"question": text}]
        consumer = {"message_id": "msg_actual", "tool_id": "call_actual", "questions": questions}
        reply = {"status": "answered", "session_id": self.sid, "consumer": consumer, "answer": {"q0": label}}
        reply["reply_sha256"] = common.canonical_sha256(reply)
        _, attempt = common.next_attempt(self.root / "supervision/native-interactions/frm_actual")
        path = attempt / "reply.json"
        common.write_new_json(path, reply)
        actual = {"data": {"content": [{"type": "tool", "name": "question", "id": "call_actual", "state": {
            "status": "completed", "input": {"questions": questions}, "content": [
                {"text": f'User has answered your questions: "{text}"="{label}". You can continue.'}]}}]}}
        with patch.object(runtime, "_get", return_value=actual):
            return control.resolve_native(self.root, consumer, reply["answer"], path)

    def test_candidate_closure_requires_actual_owner_decision_and_retains_evidence(self):
        with self.assertRaisesRegex(common.PipelineError, "没有负责人"):
            ledger.candidate_disposition(self.root, self.employee["employee_id"], "close_candidate")
        req = control.request(self.root, self.employee["employee_id"], "close_candidate", self.report, "generic control saturates")
        self.assertFalse(control.state(self.root)["worker_channel_open"])
        self.assertEqual(ledger._employee(self.root, self.employee["employee_id"])["employment_status"], "needs-owner-decision")
        self.answer_request(req, "批准关闭该候选")
        value, _ = ledger.candidate_disposition(self.root, self.employee["employee_id"], "close_candidate")
        self.assertEqual(value["employment_status"], "standby")
        self.assertTrue(self.report.exists())
        self.assertFalse(ledger.employee_health(value, {"status": "idle"})["attention"])
        with self.assertRaises(common.PipelineError):
            ledger.candidate_disposition(self.root, self.employee["employee_id"], "close_candidate")

    def test_rejected_closure_does_not_authorize_standby_or_fire(self):
        req = control.request(self.root, self.employee["employee_id"], "close_candidate", self.report, "review needed")
        self.answer_request(req, "保留候选继续工作")
        with self.assertRaises(common.PipelineError):
            ledger.candidate_disposition(self.root, self.employee["employee_id"], "close_candidate")
        with self.assertRaises(common.PipelineError):
            ledger.fire(self.root, self.employee["employee_id"], "supervisor wants to close")

    def test_supplement_evidence_choice_keeps_owner_gate_closed(self):
        req = control.request(self.root, self.employee["employee_id"], "close_candidate", self.report, "needs evidence")
        self.answer_request(req, "补充证据再决定")
        self.assertIn(req["request_id"], control.state(self.root)["pending_requests"])
        self.assertFalse(control.resume(self.root)["worker_channel_open"])

    def test_subject_change_invalidates_old_disposition(self):
        req = control.request(self.root, self.employee["employee_id"], "standby", self.report, "cannot continue this subject")
        self.answer_request(req, "批准该员工待命")
        employee = ledger._employee(self.root, self.employee["employee_id"])
        ledger._save_employee(self.root, {**employee, "seed_id": "different/seed/identity"})
        with self.assertRaises(common.PipelineError):
            ledger.candidate_disposition(self.root, self.employee["employee_id"], "standby")

    def test_existing_explicit_reseed_instruction_is_preserved_without_reasking(self):
        employee, _ = ledger.hire(self.root, "producer-0007", "producer", "opencode-task")
        report = Path(employee["job_root"]) / "rejected-review.json"
        report.write_text('{"mode":"fixture-review"}')
        message = {"id":"msg_owner_reseed", "type":"user", "text":"把3和4继续，7，8换种子"}
        # OpenCode IDs have no underscores after the prefix.
        message["id"] = "msg_ownerreseed"
        with patch.object(runtime, "_visible_session", return_value={"directory":str(self.root)}), \
                patch.object(runtime, "_get", return_value={"data":message}):
            value = control.record_reseed_chat(self.root, employee["employee_id"], report, self.sid, message["id"])
            control.require_disposition(self.root, employee["employee_id"], "replace_seed")
        self.assertEqual(value["source"], "opencode-owner-chat")
        self.assertFalse(value["rollout_authorized"])
        self.assertFalse(value["fire_authorized"])
        self.assertEqual(control._reseed_ids(message["text"]), {"producer-0007","producer-0008"})
        for text in ("7和8不换种子", "7和8换种子吗", "employee update: 7和8换种子"):
            with self.assertRaises(common.PipelineError):
                control._reseed_ids(text)

    def test_idle_running_flip_does_not_generate_another_identical_incident(self):
        with patch.object(runtime, "employee_runtime", side_effect=[{"status": "idle"}, {"status": "running"}]), \
                patch.object(ledger, "_deliver_to_supervisor", return_value={"status": "admitted"}) as deliver:
            ledger.watch(self.root, active={})
            second, _ = ledger.watch(self.root, active={self.worker_sid: {}})
        self.assertEqual(deliver.call_count, 0)
        self.assertGreaterEqual(len(list((self.root / "supervision/secretary/inbox").glob("*.json"))), 1)
        self.assertEqual(second["incident_count"], 0)

    def test_supervisor_token_cannot_pause_or_resume_owner_gate(self):
        self.pause()
        state = topify_web.ConsoleState(self.root, "owner-test", "supervisor-test")
        server = topify_web.ConsoleServer(("127.0.0.1", 0), state)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            for action in ("pause", "resume"):
                request = Request(f"http://127.0.0.1:{server.server_port}/api/owner-control", method="POST",
                    headers={"Authorization": "Bearer supervisor-test", "Content-Type": "application/json"},
                    data=json.dumps({"supervisor_id": "default", "action": action}).encode())
                with self.assertRaises(HTTPError) as caught:
                    urlopen(request, timeout=5)
                self.assertEqual(caught.exception.code, 403); caught.exception.close()
            self.assertTrue(control.state(self.root)["owner_paused"])
        finally:
            server.shutdown(); server.server_close(); thread.join(3)


if __name__ == "__main__":
    unittest.main()
