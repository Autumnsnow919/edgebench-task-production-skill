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
import topify_opencode as runtime
import topify_seed
import topify_supervisor as ledger
import topify_web


class WorkforceRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.campaign = self.root / "campaign"
        topify_seed.initialize_campaign(
            self.campaign, Path(__file__).resolve().parents[1] / "examples/campaign-config.json")
        ledger.initialize(self.campaign)
        self.employee, _ = ledger.hire(self.campaign, "producer-live", "producer", "opencode-task")
        self.session_id = "ses_12345678901234567890"

    def response(self, path):
        if path == "/api/session/active":
            return {"data": {self.session_id: {"type": "running"}}}
        return {"data": {"id": self.session_id, "agent": "edgebench-producer",
                         "location": {"directory": self.employee["job_root"]},
                         "title": "Production task", "time": {"updated": 1000}}}

    def bind(self):
        ledger.dispatch(self.campaign, self.employee["employee_id"])
        with patch.object(runtime, "_get", side_effect=self.response):
            ledger.bind_session(self.campaign, self.employee["employee_id"], self.session_id)
        self.employee = ledger._employee(self.campaign, self.employee["employee_id"])

    def test_unbound_does_not_assume_running_or_call_runtime(self):
        with patch.object(runtime, "_get") as get:
            value = runtime.employee_runtime(self.employee)
        self.assertEqual(value["status"], "unbound")
        self.assertFalse(value["can_chat"])
        get.assert_not_called()

    def test_real_binding_uses_employee_directory_not_campaign_title(self):
        self.bind()
        with patch.object(runtime, "_get", side_effect=self.response):
            value = runtime.employee_runtime(self.employee)
        self.assertEqual(value["status"], "running")
        self.assertTrue(value["can_chat"])
        self.assertEqual(value["directory"], self.employee["job_root"])

    def test_binding_pointing_elsewhere_is_not_chat_capable(self):
        self.bind()
        def mismatch(path):
            value = self.response(path)
            value["data"]["location"]["directory"] = str(self.root / "someone-else")
            return value
        with patch.object(runtime, "_get", side_effect=mismatch):
            value = runtime.employee_runtime(self.employee)
        self.assertEqual(value["status"], "unavailable")
        self.assertFalse(value["can_chat"])

    def start_server(self):
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

    def test_unbound_chat_post_is_rejected_without_starting_any_model(self):
        base = self.start_server()
        request = Request(base + "/api/employee-session-message", method="POST",
                          headers={"Content-Type": "application/json"},
                          data=json.dumps({"supervisor_id": "default",
                                           "employee_id": self.employee["employee_id"],
                                           "text": "hello"}).encode())
        with patch.object(runtime, "send_message") as send:
            with self.assertRaises(HTTPError) as caught:
                urlopen(request, timeout=5)
            self.assertEqual(caught.exception.code, 409)
            caught.exception.close()
            send.assert_not_called()

    def test_bound_chat_uses_actual_session_and_job_root(self):
        self.bind()
        base = self.start_server()
        request = Request(base + "/api/employee-session-message", method="POST",
                          headers={"Content-Type": "application/json"},
                          data=json.dumps({"supervisor_id": "default",
                                           "employee_id": self.employee["employee_id"],
                                           "text": "show evidence"}).encode())
        with patch.object(runtime, "_get", side_effect=self.response), \
                patch.object(runtime, "send_message", return_value={"status": "sent"}) as send:
            with urlopen(request, timeout=5) as response:
                self.assertEqual(response.status, 201)
                json.load(response)
        send.assert_called_once_with(self.employee["job_root"], self.session_id, "show evidence")


if __name__ == "__main__":
    unittest.main()
