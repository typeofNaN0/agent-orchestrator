import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import router
import test_router


class AdaptiveTests(unittest.TestCase):
    setUp = test_router.RouterTests.setUp
    task = test_router.RouterTests.task
    plan = test_router.RouterTests.plan
    submit = test_router.RouterTests.submit
    def register_check(self, name="answer", code="from pathlib import Path; assert Path('answer.txt').read_text() == 'correct'"):
        self.config["checks"][name] = {"argv": [sys.executable, "-c", code], "timeout_seconds": 2}
        return name

    def test_live_checkpoint_escalates_and_preserves_edits(self):
        run_id = self.submit(self.plan([self.task(task="LIVE_ESCALATE")]))
        started = time.monotonic()
        result = router.run_pipeline(self.store, run_id)
        self.assertEqual(result["status"], "completed")
        worker, reviewer = self.store.sessions(run_id)
        attempts = self.store.attempts(worker["id"])
        self.assertEqual([a["effort"] for a in attempts], ["medium", "high"])
        self.assertNotEqual(attempts[0]["thread_id"], attempts[1]["thread_id"])
        self.assertEqual(attempts[0]["failure_kind"], "reasoning")
        self.assertEqual(self.store.latest_checkpoint(attempts[0]["id"])["completed"], ["saved partial work"])
        self.assertLess(time.monotonic() - started, 5)
        self.assertTrue((Path(reviewer["workspace"]) / "preserved.txt").exists())
        self.assertTrue((Path(reviewer["workspace"]) / "finished.txt").exists())

    def test_same_thread_continuation_opt_in(self):
        self.config["adaptive"]["reuse_sidekick_threads"] = True
        run_id = self.submit(self.plan([self.task(task="YIELD_ONCE")]))
        router.run_pipeline(self.store, run_id)
        worker, reviewer = self.store.sessions(run_id)
        attempts = self.store.attempts(worker["id"])
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]["thread_id"], attempts[1]["thread_id"])
        self.assertEqual(attempts[1]["resumed"], 1)
        self.assertNotEqual(reviewer["thread_id"], attempts[0]["thread_id"])

    def test_fresh_continuation_default(self):
        run_id = self.submit(self.plan([self.task(task="YIELD_ONCE")]))
        router.run_pipeline(self.store, run_id)
        attempts = self.store.attempts(self.store.sessions(run_id)[0]["id"])
        self.assertEqual(len(attempts), 2)
        self.assertNotEqual(attempts[0]["thread_id"], attempts[1]["thread_id"])
        self.assertEqual(attempts[0]["model"], attempts[1]["model"])

    def test_verification_overrides_false_success_then_repairs(self):
        task = self.task(task="VERIFY_FIX", checks=[self.register_check()])
        run_id = self.submit(self.plan([task]))
        result = router.run_pipeline(self.store, run_id)
        self.assertEqual(result["status"], "completed")
        worker = self.store.sessions(run_id)[0]
        attempts = self.store.attempts(worker["id"])
        self.assertEqual([a["status"] for a in attempts], ["incomplete", "completed"])
        rows = self.store.verification()
        self.assertEqual([(r["status"], r["exit_code"]) for r in rows], [("failed", 1), ("passed", 0), ("passed", 0)])
        self.assertIn('"check_name": "answer"', self.store.sessions(run_id)[-1]["prompt"])

    def test_missing_check_executable_does_not_escalate(self):
        self.config["checks"]["missing"] = {"argv": ["/no/such/executable"], "timeout_seconds": 1}
        run_id = self.submit(self.plan([self.task(checks=["missing"])]))
        self.assertEqual(router.run_pipeline(self.store, run_id)["status"], "incomplete")
        worker = self.store.sessions(run_id)[0]
        self.assertEqual(len(self.store.attempts(worker["id"])), 1)
        self.assertEqual(worker["model"], "luna")

    def test_failure_cannot_be_reported_as_complete(self):
        task = self.task(task="ALWAYS_WRONG", checks=[self.register_check()])
        run_id = self.submit(self.plan([task]))
        self.assertEqual(router.run_pipeline(self.store, run_id)["status"], "incomplete")
        for session in self.store.sessions(run_id):
            self.assertEqual(session["status"], "incomplete")

    def test_network_retry_does_not_upgrade(self):
        run_id = self.submit(self.plan([self.task(task="NETWORK_ONCE")]))
        router.run_pipeline(self.store, run_id)
        attempts = self.store.attempts(self.store.sessions(run_id)[0]["id"])
        self.assertEqual([(a["model"], a["effort"]) for a in attempts], [("luna", "medium"), ("luna", "medium")])
        self.assertEqual(attempts[0]["failure_kind"], "infrastructure")

    def test_network_retry_limit_never_escalates(self):
        run_id = self.submit(self.plan([self.task(task="NETWORK_ALWAYS")]))
        router.run_pipeline(self.store, run_id)
        worker = self.store.sessions(run_id)[0]
        self.assertEqual(worker["status"], "failed")
        self.assertEqual(len(self.store.attempts(worker["id"])), 2)
        self.assertEqual(worker["model"], "luna")

    def test_repeated_failure_checkpoint_escalates(self):
        run_id = self.submit(self.plan([self.task(task="REPEATED_FAILURE")]))
        router.run_pipeline(self.store, run_id)
        attempts = self.store.attempts(self.store.sessions(run_id)[0]["id"])
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]["failure_kind"], "reasoning")

    def test_stall_deadline_escalates(self):
        self.config["adaptive"]["stall_seconds"] = 1
        run_id = self.submit(self.plan([self.task(task="STALL")]))
        router.run_pipeline(self.store, run_id)
        attempts = self.store.attempts(self.store.sessions(run_id)[0]["id"])
        self.assertIn("stall deadline", attempts[0]["error"])
        self.assertEqual(len(attempts), 2)

    def test_reviewer_repairs_and_reviews_again(self):
        name = self.register_check("repair_check", "from pathlib import Path; assert Path('repair.txt').read_text() == 'fixed'")
        plan = dict(self.plan([], "REQUEST_REPAIR"), checks=[name])
        run_id = self.submit(plan)
        self.assertEqual(router.run_pipeline(self.store, run_id)["status"], "completed")
        sessions = self.store.sessions(run_id)
        self.assertEqual([s["role"] for s in sessions], ["reviewer", "repair", "reviewer"])
        self.assertEqual(len(set(s["thread_id"] for s in sessions)), 3)
        patch = self.store.artifact(sessions[-1]["id"], "final.patch")
        self.assertIn(b"repair.txt", patch)
        router.git(self.repo, "apply", "--check", "-", data=patch)

    def test_repair_round_limit(self):
        self.config["adaptive"]["max_repair_rounds"] = 1
        run_id = self.submit(self.plan([], "ALWAYS_REPAIR"))
        self.assertEqual(router.run_pipeline(self.store, run_id)["status"], "incomplete")
        self.assertEqual(len(self.store.sessions(run_id)), 3)

    def test_context_selection_required_records_and_staleness(self):
        first = self.task("a", "WRITE base.txt updated")
        second = self.task("b", "WRITE b.txt B", context_keys=["facts"])
        second["depends_on"] = ["a"]
        plan = dict(self.plan([first, second]), context_records=[
            {"key": "facts", "kind": "fact", "content": "FILE_FACT", "files": ["base.txt"]},
            {"key": "required", "kind": "constraint", "content": "UNIVERSAL", "files": []},
            {"key": "unused", "kind": "fact", "content": "UNSELECTED", "files": []}],
            required_context_keys=["required"], reviewer_context_keys=["facts"])
        run_id = self.submit(plan)
        router.run_pipeline(self.store, run_id)
        a, b, reviewer = self.store.sessions(run_id)
        self.assertNotIn("FILE_FACT", a["prompt"])
        self.assertIn("UNIVERSAL", a["prompt"])
        self.assertIn("FILE_FACT", b["prompt"])
        self.assertIn('"must_revalidate": true', b["prompt"])
        self.assertNotIn("UNSELECTED", reviewer["prompt"])

    def test_context_rejects_path_traversal(self):
        plan = dict(self.plan([]), context_records=[{"key": "bad", "kind": "fact", "content": "bad", "files": ["../secret"]}])
        with self.assertRaisesRegex(router.RouterError, "escapes"):
            self.submit(plan)

    def test_explicit_route_overrides_judgment_advice(self):
        task = self.task(assessment={"judgment": 3, "mechanical": 3})
        selected = router.validate_plan(self.plan([task]), self.config)["sidekicks"][0]
        self.assertEqual(selected["model"], "luna")
        self.assertEqual(selected["effort"], "medium")

    def test_assessment_cannot_supply_missing_route(self):
        task = self.task(assessment={"mechanical": 3})
        del task["model"], task["effort"]
        with self.assertRaisesRegex(router.RouterError, "Explicit model and effort"):
            router.validate_plan(self.plan([task]), self.config)

    def test_executable_check_registry_is_required(self):
        with self.assertRaisesRegex(router.RouterError, "Unknown verification"):
            self.submit(self.plan([self.task(checks=["invented-shell-command"])]))
        self.config["workspace_mode"] = "read-only"
        name = self.register_check()
        with self.assertRaisesRegex(router.RouterError, "disabled in read-only"):
            self.submit(self.plan([self.task(checks=[name])]))

    def test_verification_timeout_captured(self):
        self.config["adaptive"]["max_attempts"] = 1
        name = self.register_check(code="import time; time.sleep(5)")
        self.config["checks"][name]["timeout_seconds"] = 1
        run_id = self.submit(self.plan([self.task(checks=[name])]))
        self.assertEqual(router.run_pipeline(self.store, run_id)["status"], "incomplete")
        self.assertEqual(self.store.verification()[0]["status"], "timeout")

    def test_run_attempt_budget_is_enforced(self):
        self.config["adaptive"]["max_total_attempts"] = 1
        run_id = self.submit(self.plan([self.task()]))
        self.assertEqual(router.run_pipeline(self.store, run_id)["status"], "failed")
        self.assertEqual(sum(len(self.store.attempts(s["id"])) for s in self.store.sessions(run_id)), 1)

    def test_parallel_attempt_budget_is_atomic(self):
        from concurrent.futures import ThreadPoolExecutor
        self.config["adaptive"]["max_total_attempts"] = 1
        run_id = self.submit(self.plan([]))
        run = self.store.claim_run(run_id)
        workers = [self.store.add_session(run_id, str(i), "sidekick", "luna", "medium", "") for i in range(4)]
        def claim(worker):
            try:
                self.store.attempt(run, worker, "test")
                return True
            except router.RouterError:
                return False
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(claim, workers)), 1)

    def test_observed_token_budget_prevents_next_launch(self):
        self.config["adaptive"]["max_observed_tokens"] = 50
        run_id = self.submit(self.plan([self.task()]))
        self.assertEqual(router.run_pipeline(self.store, run_id)["status"], "failed")
        self.assertEqual(sum(len(self.store.attempts(s["id"])) for s in self.store.sessions(run_id)), 1)

    def test_metrics_and_tuning_use_verified_independent_samples(self):
        name = self.register_check("exists", "from pathlib import Path; assert Path('one.txt').exists()")
        for _ in range(2):
            run_id = self.submit(self.plan([self.task(checks=[name], task_type="mechanical")]))
            router.run_pipeline(self.store, run_id)
        groups = router.metrics(self.store, "mechanical")["groups"]
        self.assertEqual(groups[0]["verified_completed"], 2)
        self.assertEqual(groups[0]["clean_verified_sessions"], 2)
        tuned, reasons = router.tune(self.store, self.config, min_samples=2)
        self.assertEqual(tuned["task_type_routes"]["mechanical"]["model"], "luna")
        self.assertEqual(len(reasons), 1)
        self.assertNotIn("mechanical", self.config["task_type_routes"])

    def test_tuning_ignores_unverified_success(self):
        for _ in range(2):
            router.run_pipeline(self.store, self.submit())
        _, reasons = router.tune(self.store, self.config, min_samples=2)
        self.assertEqual(reasons, [])

    def test_ladder_never_downgrades_high_effort(self):
        result = router.escalation(self.config, "sol", "max")
        self.assertEqual(result["model"], "astra")
        self.assertIsNone(router.escalation(self.config, "astra", "max"))

    def test_tuned_routes_and_legacy_floor_are_advisory(self):
        self.config["task_type_routes"]["refactor"] = {"model": "sol", "effort": "medium"}
        self.config["routing_policy"] = {"enforce_frontier_floor": True}
        for assessment in ({"mechanical": 3}, {"judgment": 3}):
            self.assertEqual(router.selection(self.config, {"model": "luna", "effort": "low", "task_type": "refactor",
                                                           "assessment": assessment}), ("luna", "low"))

    def test_report_cannot_hide_checkpoint_escalation(self):
        checkpoint = {"status": "needs_escalation", "completed": [], "evidence": [], "failed_tests": [],
                      "blocker": "uncertain", "remaining_work": [], "suggested_model": "", "suggested_effort": ""}
        report = {"status": "completed", "summary": "false claim", "tests": [], "risks": [], "handoff": "", "checkpoint": checkpoint}
        with self.assertRaisesRegex(router.RouterError, "conflicts"):
            router.validate_report(report, self.config)

    def test_malformed_success_is_rejected(self):
        run_id = self.submit(self.plan([self.task(task="MALFORMED")]))
        router.run_pipeline(self.store, run_id)
        self.assertEqual(self.store.sessions(run_id)[0]["status"], "failed")

    def test_checkpoint_cli_is_atomic_and_validated(self):
        inbox = self.root / "inbox"
        inbox.mkdir()
        source = self.root / "checkpoint.json"
        data = {"status": "progress", "completed": ["step"], "evidence": [], "failed_tests": [], "blocker": "",
                "remaining_work": [], "suggested_model": "", "suggested_effort": ""}
        source.write_text(json.dumps(data))
        router.write_checkpoint(inbox, source)
        self.assertEqual(json.loads((inbox / "checkpoint.json").read_text()), data)
        self.assertEqual(len(list(inbox.iterdir())), 1)

    def test_auth_and_sandbox_failures_are_configuration_errors(self):
        for message in ("Operation not permitted", "Invalid API key", "unknown feature: obsolete"):
            self.assertEqual(router.failure_kind(message), "configuration")

    def test_status_labels_unverified_success(self):
        run_id = self.submit(self.plan([]))
        result = router.run_pipeline(self.store, run_id)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["sessions"][0]["verification_status"], "not_configured")
