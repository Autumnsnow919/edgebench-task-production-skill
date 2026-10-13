"""Control-plane fixtures: QC failure count is not an automatic firing policy."""

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
import topify_seed
import topify_supervisor as ledger
import topify_web


class QCOwnerPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.campaign = Path(self.temp.name) / "campaign"
        topify_seed.initialize_campaign(self.campaign, Path(__file__).resolve().parents[1] / "examples/campaign-config.json")
        ledger.initialize(self.campaign)
        self.producer, _ = ledger.hire(self.campaign, "producer-review", "producer", "opencode-task")
        self.qc, _ = ledger.hire(self.campaign, "qc-review", "qc", "opencode-task")
        self.child, _ = ledger.hire(self.campaign, "review-child", "seed-worker", "opencode-task", parent_id="producer-review")
        ledger._save_employee(self.campaign, {**self.producer, "hardening_revision": 2, "employment_status": "waiting-qc"})
        self.owner_sid = "ses_owner1234567890123456789"
        manifest = common.load_json(self.campaign / "supervision/manifest.json")
        common.write_latest_json(self.campaign / "supervision/manifest.json", {**manifest,
                               "supervisor_session_id":self.owner_sid, "supervisor_session_directory":str(self.campaign)})

    def owner_proof(self, decision, *, answer=None, producer_id="producer-review"):
        # Mock the durable result of an authenticated native Web question. This
        # is a fixture, not evidence that a production owner actually approved.
        qc = ledger._employee(self.campaign, "producer-review")["last_qc_decision"]
        scope = {"producer_id":producer_id, "qc_decision_sha256":qc["decision_sha256"],
                 "qc_report_sha256":qc["report_sha256"], "answer_key":"q0"}
        receipt = {"schema_version":1, "source":"fixture-native-answer", "form_id":"frm_fixture",
                   "session_id":self.owner_sid, "status":"answered", "consumer":{"status":"waiting"},
                   "qc_owner_scopes":[scope], "answer":{"q0":answer or ledger.QC_OWNER_CHOICES[decision]}}
        receipt["reply_sha256"] = common.canonical_sha256(receipt)
        _, attempt = common.next_attempt(self.campaign / "supervision/native-interactions/fixture")
        path = attempt / "reply.json"
        common.write_new_json(path, receipt)
        return path

    def decide(self, round_number, decision="repair_required", severity="major"):
        path = Path(self.qc["job_root"]) / f"qc-{round_number}.json"
        path.write_text(json.dumps({"producer_id":"producer-review", "qc_employee_id":"qc-review", "round":round_number,
                                    "decision":decision, "severity":severity,
                                    "q1_q7":{"Q1":"pass", "Q3":"incomplete"},
                                    "repair_closed":["trusted compilation fixed"],
                                    "findings":[{"id":"arm-build", "title":"Native ARM receipt missing", "status":"incomplete",
                                                 "requirement":"native validation", "evidence":"no build receipt yet",
                                                 "impact":"parity is unverified", "severity":severity}]}))
        value, code = ledger.qc_decision(self.campaign, "producer-review", "qc-review", round_number,
                                        decision, severity, path, "fixture actual failure vs missing evidence")
        self.assertEqual(code, 0)
        return value, path

    def qc2_failure(self, decision="repair_required", severity="major"):
        self.decide(1)
        return self.decide(2, decision, severity)

    def test_first_failure_requires_repair_and_never_fires(self):
        with patch.object(ledger, "fire") as fire:
            result, _ = self.decide(1, "fail", "critical")
        self.assertEqual(result["next_employee_state"], "repair-required")
        fire.assert_not_called()
        with self.assertRaisesRegex(common.PipelineError, "不是自动淘汰"):
            ledger.fire(self.campaign, "producer-review", "QC failed")

    def test_second_failure_requires_owner_even_for_minor_findings(self):
        self.decide(1)
        with patch.object(ledger, "fire") as fire:
            result, _ = self.decide(2, "fail", "minor")
        self.assertEqual(result["next_employee_state"], "needs-owner-decision")
        fire.assert_not_called()
        self.assertNotEqual(ledger._employee(self.campaign, "review-child")["employment_status"], "fired")

    def test_second_critical_failure_also_requires_owner(self):
        result, _ = self.qc2_failure("fail", "critical")
        self.assertEqual(result["next_employee_state"], "needs-owner-decision")
        with self.assertRaisesRegex(common.PipelineError, "必须交负责人"):
            ledger.fire(self.campaign, "producer-review", "second critical QC failure")

    def test_pass_with_exception_still_requires_owner(self):
        result, _ = self.qc2_failure("pass_with_exception", "info")
        self.assertEqual(result["next_employee_state"], "needs-owner-decision")

    def test_report_brief_keeps_incomplete_distinct_from_fail_and_lists_closed_repairs(self):
        result, _ = self.qc2_failure()
        board, _ = ledger.dashboard(self.campaign, "producer-review")
        brief = board["employees"][0]["qc_owner_review"]
        self.assertEqual(brief["report_integrity"], "verified")
        self.assertEqual(brief["quality_checks"]["Q3"], "incomplete")
        self.assertEqual(brief["findings"][0]["status"], "incomplete")
        self.assertEqual(brief["resolved_items"], ["trusted compilation fixed"])
        self.assertEqual(brief["qc_decision_sha256"], result["decision_sha256"])
        self.assertFalse(brief["automatic_fire_allowed"])
        self.assertIsNotNone(board["alerts"][0]["owner_review"])

    def test_acceptance_without_reason_preserves_original_qc_and_report(self):
        result, path = self.qc2_failure()
        original = path.read_bytes()
        accepted, _ = ledger.qc_resolve(self.campaign, "producer-review", "accept_exception", evidence=self.owner_proof("accept_exception"))
        self.assertEqual(accepted["resolution"]["note"], "")
        self.assertEqual(accepted["resolution"]["qc_decision_sha256"], result["decision_sha256"])
        self.assertEqual(accepted["employee"]["last_qc_decision"]["decision"], "repair_required")
        self.assertEqual(accepted["employee"]["employment_status"], "rollout-pending")
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(ledger._unresolved_qc_failure(accepted["employee"]))

    def test_changed_report_cannot_reuse_old_exception_approval(self):
        _, path = self.qc2_failure()
        path.write_text("{}")
        with self.assertRaisesRegex(common.PipelineError, "报告已变更"):
            ledger.qc_resolve(self.campaign, "producer-review", "accept_exception", evidence=self.owner_proof("accept_exception"))
        board, _ = ledger.dashboard(self.campaign, "producer-review")
        self.assertEqual(board["employees"][0]["qc_owner_review"]["report_integrity"], "changed")

    def test_round_cannot_be_reset_to_hide_qc2_failure(self):
        self.qc2_failure()
        with self.assertRaisesRegex(common.PipelineError, "轮次必须连续"):
            self.decide(1, "pass")
        self.assertEqual(ledger._employee(self.campaign, "producer-review")["employment_status"], "needs-owner-decision")

    def test_extra_review_requires_owner_and_authorizes_only_one_numbered_round(self):
        self.qc2_failure()
        with self.assertRaisesRegex(common.PipelineError, "负责人明确批准"):
            self.decide(3)
        authorized, _ = ledger.qc_resolve(self.campaign, "producer-review", "continue_repair", evidence=self.owner_proof("continue_repair"))
        self.assertEqual(authorized["resolution"]["next_qc_round"], 3)
        self.assertEqual(authorized["employee"]["employment_status"], "repair-required")
        with self.assertRaisesRegex(common.PipelineError, "不是自动淘汰"):
            ledger.fire(self.campaign, "producer-review", "owner wanted repair, not termination")
        third, _ = self.decide(3)
        self.assertEqual(third["next_employee_state"], "needs-owner-decision")
        with self.assertRaisesRegex(common.PipelineError, "负责人明确批准"):
            self.decide(4)

    def test_heartbeat_and_completion_cannot_release_owner_review_hold(self):
        self.qc2_failure()
        for status in ("heartbeat", "completed"):
            report = Path(self.producer["job_root"]) / f"{status}.json"
            report.write_text(json.dumps({"status":status, "summary":"stage-only fixture", "stage":"seed-proposal"}))
            ledger.report(self.campaign, "producer-review", report)
            self.assertEqual(ledger._employee(self.campaign, "producer-review")["employment_status"], "needs-owner-decision")

    def test_only_explicit_owner_termination_archives_tree(self):
        self.qc2_failure()
        resolution, _ = ledger.qc_resolve(self.campaign, "producer-review", "fire", "owner chose termination after review",
                                        evidence=self.owner_proof("fire"))
        self.assertEqual(resolution["resolution"]["authority"], "owner")
        self.assertEqual(ledger._employee(self.campaign, "producer-review")["employment_status"], "fired")
        self.assertEqual(ledger._employee(self.campaign, "review-child")["employment_status"], "fired")
        self.assertTrue(Path(self.producer["job_root"]).exists())

    def test_supervisor_note_alone_cannot_be_used_as_owner_approval(self):
        self.qc2_failure()
        for decision in ledger.QC_OWNER_CHOICES:
            with self.assertRaisesRegex(common.PipelineError, "真实 question 回答回执"):
                ledger.qc_resolve(self.campaign, "producer-review", decision, "supervisor thinks owner agrees")
        self.assertEqual(ledger._employee(self.campaign, "producer-review")["employment_status"], "needs-owner-decision")

    def test_answer_for_other_employee_or_choice_cannot_be_reused(self):
        self.qc2_failure()
        other = self.owner_proof("fire", producer_id="someone-else")
        wrong_choice = self.owner_proof("continue_repair")
        for evidence in (other, wrong_choice):
            with self.assertRaisesRegex(common.PipelineError, "没有授权此员工"):
                ledger.qc_resolve(self.campaign, "producer-review", "fire", "fixture", evidence)

    def test_completed_native_question_can_be_recorded_without_web(self):
        result, _ = self.qc2_failure()
        text = f'QC-2 producer-review，凭证 {result["decision_sha256"]}：请选择下一步。'
        message = {"id":"msg_actual", "agent":"edgebench-supervisor", "content":[
            {"type":"tool", "name":"question", "id":"call_actual", "state":{"status":"completed",
             "input":{"questions":[{"question":text}]},
             "content":[{"type":"text", "text":f'User has answered your questions: "{text}"="继续返修复检". You can now continue.'}]}}]}
        with patch.object(ledger.topify_opencode, "_visible_session"), patch.object(ledger.topify_opencode, "_get", return_value={"data":[message]}):
            recorded, code = ledger.record_qc_owner_answer(self.campaign, "producer-review", "continue_repair")
        self.assertEqual(code,0)
        self.assertEqual(recorded["source"], "opencode-question-tool-result")
        resolution, _ = ledger.qc_resolve(self.campaign, "producer-review", "continue_repair", evidence=recorded["evidence_path"])
        self.assertEqual(resolution["employee"]["employment_status"], "repair-required")
        with patch.object(ledger.topify_opencode, "_visible_session"), patch.object(ledger.topify_opencode, "_get", return_value={"data":[message]}):
            with self.assertRaises(common.PipelineError):
                ledger.record_qc_owner_answer(self.campaign, "producer-review", "fire")

    def test_supervisor_token_cannot_accept_or_terminate_or_extend_qc(self):
        self.qc2_failure()
        state = topify_web.ConsoleState(self.campaign, "owner-test", "supervisor-test")
        server = topify_web.ConsoleServer(("127.0.0.1",0), state)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            for decision in ("fire", "accept_exception", "continue_repair"):
                body = json.dumps({"supervisor_id":"default", "producer_id":"producer-review", "decision":decision, "note":"fixture"}).encode()
                request = Request(f"http://127.0.0.1:{server.server_port}/api/qc-resolve", data=body,
                                  headers={"Authorization":"Bearer supervisor-test", "Content-Type":"application/json"}, method="POST")
                with self.assertRaises(HTTPError) as caught:
                    urlopen(request, timeout=5)
                self.assertEqual(caught.exception.code,403)
                caught.exception.close()
            self.assertEqual(ledger._employee(self.campaign, "producer-review")["employment_status"], "needs-owner-decision")
        finally:
            server.shutdown();server.server_close();thread.join(3)


if __name__ == "__main__":
    unittest.main()
