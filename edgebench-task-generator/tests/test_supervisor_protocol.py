import json
import os
from pathlib import Path
import tempfile
import unittest

import sys


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import topify_seed  # noqa: E402
import topify_supervisor  # noqa: E402
import topify_web  # noqa: E402


class SupervisorProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        config = Path(__file__).resolve().parents[1] / "examples/campaign-config.json"
        self.campaign = self.root / "campaign"
        topify_seed.initialize_campaign(self.campaign, config)
        topify_supervisor.initialize(self.campaign, mode="dual")

    def tearDown(self):
        self.temp.cleanup()

    def test_employee_dispatch_and_report(self):
        producer, code = topify_supervisor.hire(
            self.campaign,
            "producer-001",
            "producer",
            "opencode-task",
            seed_id="systems-software-engineering/cpython/lead-001",
        )
        self.assertEqual(code, 0)
        self.assertEqual(producer["employment_status"], "active")
        dispatch, code = topify_supervisor.dispatch(self.campaign, "producer-001")
        self.assertEqual(code, 0)
        self.assertEqual(dispatch["status"], "dispatched")
        report_path = self.root / "worker.json"
        report_path.write_text(json.dumps({
            "status": "completed",
            "summary": "candidate complete",
        }))
        receipt, code = topify_supervisor.report(self.campaign, "producer-001", report_path)
        self.assertEqual(code, 0)
        self.assertEqual(receipt["report_status"], "completed")

    def test_child_request_is_not_dispatch(self):
        topify_supervisor.hire(
            self.campaign, "producer-001", "producer", "opencode-task",
            seed_id="systems-software-engineering/cpython/lead-001",
        )
        request, code = topify_supervisor.child_request(
            self.campaign, "producer-001", "producer-001-seed-01", "producer",
            "verify pinned source", "orca-cli",
        )
        self.assertEqual(code, 0)
        self.assertEqual(request["status"], "pending-supervisor-dispatch")
        self.assertFalse((self.campaign / "supervision/employees/producer-001-seed-01.json").exists())

    def test_early_upper_bound_is_not_infrastructure_failure(self):
        profile_path = Path(__file__).resolve().parents[1] / "references/profiles/edgebench-v1.json"
        profile = json.loads(profile_path.read_text())
        result = topify_supervisor.evaluate_rollout(profile, {
            "status": "observing",
            "elapsed_seconds": 3600,
            "trajectory": [{"elapsed_seconds": 3600, "score": 80, "valid": True}],
        })
        self.assertEqual(result["status"], "EARLY_TOO_EASY")
        self.assertEqual(result["action"], "fire_and_replace_seed")

        infra = topify_supervisor.evaluate_rollout(profile, {
            "status": "infra_failure",
            "elapsed_seconds": 3600,
            "signals": ["provider_failure"],
            "trajectory": [],
        })
        self.assertEqual(infra["status"], "INFRA_FAILURE")
        self.assertEqual(infra["action"], "retry_same_frozen_task")

    def test_dashboard_exposes_capacity_and_worker_summary(self):
        topify_supervisor.hire(
            self.campaign, "producer-001", "producer", "opencode-task",
            seed_id="systems-software-engineering/cpython/lead-001",
        )
        report_path = self.root / "worker.json"
        report_path.write_text(json.dumps({
            "status": "heartbeat",
            "summary": "currently measuring the executable prototype",
        }))
        topify_supervisor.report(self.campaign, "producer-001", report_path)
        dashboard, code = topify_supervisor.dashboard(self.campaign)
        self.assertEqual(code, 0)
        self.assertEqual(dashboard["capacity"]["active"], 1)
        self.assertEqual(dashboard["employees"][0]["report"]["status"], "heartbeat")
        self.assertIn("measuring", dashboard["employees"][0]["report"]["summary"])

    def test_web_token_files_are_owner_only(self):
        owner = self.root / "owner.token"
        supervisor = self.root / "supervisor.token"
        owner.write_text("owner-secret\n")
        supervisor.write_text("supervisor-secret\n")
        os.chmod(owner, 0o600)
        os.chmod(supervisor, 0o600)
        self.assertEqual(topify_web._read_token_file(owner, "owner"), "owner-secret")
        state = topify_web.ConsoleState(
            self.campaign, topify_web._read_token_file(owner, "owner"),
            topify_web._read_token_file(supervisor, "supervisor"),
        )
        self.assertEqual(state.role("owner-secret"), "owner")
        self.assertEqual(state.role("supervisor-secret"), "supervisor")


if __name__ == "__main__":
    unittest.main()
