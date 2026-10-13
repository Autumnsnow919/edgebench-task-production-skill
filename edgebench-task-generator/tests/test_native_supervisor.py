"""Isolated control-plane fixtures, not formal task/production evidence."""

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
import topify_opencode as runtime
import topify_seed
import topify_supervisor as ledger
import topify_web


class NativeSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.campaign = self.root / "campaign"
        topify_seed.initialize_campaign(self.campaign, Path(__file__).resolve().parents[1] / "examples/campaign-config.json")
        ledger.initialize(self.campaign)
        self.employee, _ = ledger.hire(self.campaign, "producer-native", "producer", "opencode-task")
        self.sid = "ses_12345678901234567890"
        self.fid = "frm_native123"
        self.mid = "msg_native123"
        manifest = common.load_json(self.campaign / "supervision/manifest.json")
        self.manifest = {**manifest, "supervisor_session_id": self.sid,
                         "supervisor_session_directory": str(self.campaign)}
        common.write_latest_json(self.campaign / "supervision/manifest.json", self.manifest)
        self.form = {"id": self.fid, "sessionID": self.sid, "title": "fixture owner question",
                     "metadata": {"kind": "question", "tool": {"messageID": self.mid, "id": "call_native"}},
                     "state": {"status": "pending"},
                     "fields": [{"key": "q0", "type": "string", "options": [{"value": "接受", "label": "接受"}], "custom": False}]}
        self.tool_status = "running"

    def response(self, path):
        if path == "/api/session/active":
            return {"data": {}}
        if path == f"/api/session/{self.sid}":
            return {"data": {"id": self.sid, "agent": "edgebench-supervisor", "outcome": "interrupted",
                             "location": {"directory": str(self.campaign)}, "title": "fixture supervisor"}}
        if path == f"/api/session/{self.sid}/form/{self.fid}":
            return {"data": self.form}
        if path == f"/api/session/{self.sid}/message/{self.mid}":
            return {"data": {"id": self.mid, "type": "assistant", "agent": "edgebench-supervisor",
                             "content": [{"type": "tool", "name": "question", "id": "call_native",
                                          "state": {"status": self.tool_status}}]}}
        raise AssertionError(f"Unexpected API call: {path}")

    def server(self):
        state = topify_web.ConsoleState(self.campaign, None, None, auth_enabled=False)
        server = topify_web.ConsoleServer(("127.0.0.1", 0), state)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def close():
            server.shutdown()
            server.server_close()
            thread.join(3)
        self.addCleanup(close)
        return f"http://127.0.0.1:{server.server_port}"

    def post(self, base, path, payload):
        request = Request(base + path, method="POST", data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=5) as response:
            return json.load(response)

    def test_interrupted_supervisor_is_not_running_because_other_sessions_are_active(self):
        with patch.object(runtime, "_get", side_effect=self.response):
            value = runtime.supervisor_runtime(self.root, self.campaign, self.manifest, {"ses_someone_else": {}})
        self.assertEqual(value["status"], "interrupted")
        self.assertTrue(value["can_chat"])

    def test_unbound_supervisor_does_not_query_or_start_an_agent(self):
        with patch.object(runtime, "_get") as get, patch.object(runtime, "_post") as post:
            value = runtime.supervisor_runtime(self.root, self.campaign, {})
        self.assertFalse(value["can_chat"])
        get.assert_not_called()
        post.assert_not_called()

    def test_reply_requires_actual_running_question_tool(self):
        with patch.object(runtime, "_get", side_effect=self.response), patch.object(runtime, "_post", return_value={}) as post:
            value = runtime.reply_form(self.root, self.sid, self.fid, {"q0": "接受"})
        post.assert_called_once_with(f"/api/session/{self.sid}/form/{self.fid}/reply", {"answer": {"q0": "接受"}})
        self.assertEqual(value["consumer"]["tool_id"], "call_native")
        self.assertEqual(value["execution_status"], "awaiting-agent-action")

    def test_detached_api_form_cannot_authorize_production(self):
        self.form["metadata"] = {"edgebench": {"kind": "owner-decision"}}
        with patch.object(runtime, "_get", side_effect=self.response), patch.object(runtime, "_post") as post:
            with self.assertRaisesRegex(common.PipelineError, "没有正在等待"):
                runtime.reply_form(self.root, self.sid, self.fid, {"q0": "接受"})
        post.assert_not_called()

    def test_finished_tool_rejects_stale_form(self):
        self.tool_status = "completed"
        with patch.object(runtime, "_get", side_effect=self.response), patch.object(runtime, "_post") as post:
            with self.assertRaises(runtime.FormSettledError):
                runtime.reply_form(self.root, self.sid, self.fid, {"q0": "接受"})
        post.assert_not_called()

    def test_owner_message_reaches_native_prompt_but_does_not_claim_execution(self):
        base = self.server()
        with patch.object(runtime, "_get", side_effect=self.response), patch.object(runtime, "_post", return_value={"data": {"id": "msg_admitted"}}) as post:
            value = self.post(base, "/api/message", {"supervisor_id": "default", "target_type": "supervisor",
                                                     "target_id": "default", "text": "仅汇报方案，不试跑"})
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], f"/api/session/{self.sid}/prompt")
        self.assertTrue(post.call_args.args[1]["resume"])
        self.assertEqual(value["inbox_id"], "msg_admitted")
        self.assertEqual(value["execution_status"], "awaiting-agent-action")
        self.assertEqual(ledger._employee(self.campaign, self.employee["employee_id"])["employment_status"], "active")

    def test_review_request_does_not_create_a_form_or_change_employee_state(self):
        with patch.object(runtime, "_get", side_effect=self.response), patch.object(runtime, "_post", return_value={"data": {"id": "msg_review"}}) as post:
            value, code = ledger.create_owner_form(self.campaign, self.employee["employee_id"])
        self.assertEqual(code, 0)
        self.assertEqual(post.call_args.args[0], f"/api/session/{self.sid}/prompt")
        self.assertNotIn("form_id", value)
        self.assertEqual(ledger._employee(self.campaign, self.employee["employee_id"])["employment_status"], "active")

    def test_supervisor_cannot_recursively_request_review_from_itself(self):
        with patch.dict(ledger.os.environ, {"OPENCODE_SESSION_ID": self.sid}), patch.object(runtime, "_post") as post:
            with self.assertRaisesRegex(common.PipelineError, "不要给自己递归"):
                ledger.create_owner_form(self.campaign, self.employee["employee_id"])
        post.assert_not_called()

    def test_http_native_reply_has_receipt_without_generic_state_mapping(self):
        base = self.server()
        with patch.object(runtime, "_get", side_effect=self.response), patch.object(runtime, "_post", return_value={}), patch.object(ledger, "owner_decision") as generic:
            value = self.post(base, "/api/opencode-form-reply", {"supervisor_id": "default", "session_id": self.sid,
                                                                "form_id": self.fid, "answer": {"q0": "接受"}})
        generic.assert_not_called()
        self.assertEqual(value["reply_receipt"]["execution_status"], "awaiting-agent-action")
        receipts = list((self.campaign / "supervision/native-interactions").glob("**/reply.json"))
        self.assertEqual(len(receipts), 1)

    def test_generic_web_approval_is_disabled(self):
        base = self.server()
        with patch.object(ledger, "owner_decision") as generic:
            with self.assertRaises(HTTPError) as caught:
                self.post(base, "/api/owner-decision", {"supervisor_id": "default", "employee_id": self.employee["employee_id"],
                                                        "decision": "accept_exception", "note": ""})
            self.assertEqual(caught.exception.code, 409)
            caught.exception.close()
        generic.assert_not_called()

    def test_child_completion_is_not_a_rollout_approval(self):
        child, _ = ledger.hire(self.campaign, "native-child", "prototype-worker", "opencode-task", parent_id=self.employee["employee_id"])
        ledger._save_employee(self.campaign, {**child, "employment_status": "rollout-pending"})
        with self.assertRaisesRegex(common.PipelineError, "子任务"):
            ledger.owner_decision(self.campaign, child["employee_id"], "approve_rollout", "")

    def test_ordinary_blocker_cannot_be_accepted_as_a_qc_exception(self):
        ledger._save_employee(self.campaign, {**self.employee, "employment_status": "needs-owner-decision"})
        with self.assertRaisesRegex(common.PipelineError, "没有具体 QC"):
            ledger.owner_decision(self.campaign, self.employee["employee_id"], "accept_exception", "")

    def test_scoped_approval_can_omit_note_but_remains_an_authorization_receipt(self):
        ledger._save_employee(self.campaign, {**self.employee, "employment_status": "rollout-pending",
                                              "hardening_revision": 2, "last_qc_decision": {"decision": "pass"}})
        result, code = ledger.owner_decision(self.campaign, self.employee["employee_id"], "approve_rollout", "")
        self.assertEqual(code, 0)
        self.assertEqual(result["employee"]["employment_status"], "rollout-authorized")
        self.assertNotEqual(result["employee"]["employment_status"], "rollout-running")

    def test_native_source_is_checked_before_form_is_shown_as_answerable(self):
        with patch.object(runtime, "_get", side_effect=self.response):
            value = runtime._forms_from_responses(self.root, [{"data": [self.form]}])
        self.assertTrue(value["forms"][0]["can_reply"])
        self.tool_status = "completed"
        with patch.object(runtime, "_get", side_effect=self.response):
            value = runtime._forms_from_responses(self.root, [{"data": [self.form]}])
        self.assertFalse(value["forms"][0]["can_reply"])

    def test_legacy_form_sync_does_not_change_state(self):
        ledger._save_employee(self.campaign, {**self.employee, "owner_form": {"form_id": self.fid}})
        with patch.object(runtime, "_get") as get:
            with self.assertRaisesRegex(common.PipelineError, "脱离 Agent"):
                ledger.sync_owner_form(self.campaign, self.employee["employee_id"])
        get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
