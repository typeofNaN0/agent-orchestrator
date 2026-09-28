import json
import unittest
from pathlib import Path

import router
import test_router


class DiscoveryTests(unittest.TestCase):
    setUp = test_router.RouterTests.setUp
    task = test_router.RouterTests.task
    plan = test_router.RouterTests.plan
    submit = test_router.RouterTests.submit

    def auto(self, task="Analyze the codebase and implement a change"):
        return router.submit(self.store, self.config, self.repo, task)

    def test_discovery_precedes_planning_and_informs_routes(self):
        run_id = self.auto("PLAN_FROM_DISCOVERY")
        self.assertEqual(router.run_pipeline(self.store, run_id)["status"], "completed")
        brief, discovery, planner, worker, reviewer = self.store.sessions(run_id)
        self.assertEqual([s["role"] for s in (brief, discovery, planner, worker, reviewer)],
                         ["discovery_brief", "discovery", "orchestrator", "sidekick", "reviewer"])
        self.assertLessEqual(discovery["finished"], planner["started"])
        self.assertIn("DISCOVERED_FACT", planner["prompt"])
        self.assertEqual(worker["model"], "luna")
        self.assertEqual((Path(reviewer["workspace"]) / "discovered.txt").read_text(), "implemented")
        self.assertEqual(router.git(self.repo, "status", "--porcelain"), b"")
        self.assertIsNone(self.store.artifact(discovery["id"], "patch"))

    def test_discovery_is_read_only_without_added_write_directory(self):
        run_id = self.auto()
        router.run_pipeline(self.store, run_id)
        discovery = self.store.sessions(run_id)[1]
        invocation = json.loads((self.store.path / "logs" / run_id / discovery["task_id"] / "invocation.json").read_text())
        args = invocation["args"]
        self.assertEqual(args[args.index("--sandbox") + 1], "read-only")
        self.assertNotIn("--add-dir", args)
        evidence = json.loads(self.store.artifact(discovery["id"], "discovery_evidence"))
        self.assertEqual(evidence["files"]["base.txt"], router.fingerprint(self.repo, "base.txt"))
        self.assertIn("discovery_evidence", router.status(self.store, run_id, full=True)["sessions"][1])

    def test_bounded_follow_up_before_any_implementation(self):
        run_id = self.auto("ASK_FOLLOWUP")
        self.assertEqual(router.run_pipeline(self.store, run_id)["status"], "completed")
        sessions = self.store.sessions(run_id)
        planner = next(s for s in sessions if s["role"] == "orchestrator")
        self.assertEqual(len(self.store.attempts(planner["id"])), 2)
        self.assertEqual(sum(s["role"] == "discovery" for s in sessions), 2)
        self.assertIn("discovery-2-deeper", planner["prompt"])
        self.assertNotEqual(*[a["thread_id"] for a in self.store.attempts(planner["id"])])

    def test_endless_discovery_fails_at_limit(self):
        run_id = self.auto("ENDLESS_DISCOVERY")
        with self.assertRaisesRegex(router.RouterError, "limit exhausted"):
            router.run_pipeline(self.store, run_id)
        self.assertEqual(self.store.run(run_id)["status"], "failed")
        self.assertFalse(any(s["role"] in ("sidekick", "reviewer") for s in self.store.sessions(run_id)))

    def test_failed_discovery_cannot_be_silently_ignored(self):
        run_id = self.auto("DISCOVERY_FAIL")
        with self.assertRaisesRegex(router.RouterError, "no completed investigation"):
            router.run_pipeline(self.store, run_id)
        self.assertEqual(self.store.run(run_id)["status"], "failed")

    def test_discovery_write_violation_blocks_planning(self):
        run_id = self.auto("DISCOVERY_WRITES")
        with self.assertRaises(router.RouterError):
            router.run_pipeline(self.store, run_id)
        discovery = self.store.sessions(run_id)[1]
        self.assertEqual(discovery["status"], "failed")
        self.assertIn("modified", discovery["error"])
        self.assertEqual((self.repo / "base.txt").read_text(), "base\n")

    def test_nonexistent_citation_is_rejected(self):
        run_id = self.auto("DISCOVERY_BAD_CITATION")
        with self.assertRaises(router.RouterError):
            router.run_pipeline(self.store, run_id)
        self.assertIn("nonexistent", self.store.sessions(run_id)[1]["error"])

    def test_optional_skip_requires_explicit_configuration(self):
        run_id = self.auto("SKIP_DISCOVERY")
        with self.assertRaisesRegex(router.RouterError, "at least one"):
            router.run_pipeline(self.store, run_id)
        self.config["discovery"]["allow_skip"] = True
        run_id = self.auto("SKIP_DISCOVERY")
        router.run_pipeline(self.store, run_id)
        self.assertEqual([s["role"] for s in self.store.sessions(run_id)], ["discovery_brief", "orchestrator", "reviewer"])

    def test_discovery_can_be_disabled_for_supplied_context(self):
        self.config["discovery"]["enabled"] = False
        run_id = self.auto()
        router.run_pipeline(self.store, run_id)
        self.assertEqual([s["role"] for s in self.store.sessions(run_id)], ["orchestrator", "reviewer"])

    def test_supplied_execution_plan_does_not_trigger_late_discovery(self):
        run_id = self.submit(self.plan([]))
        router.run_pipeline(self.store, run_id)
        self.assertEqual([s["role"] for s in self.store.sessions(run_id)], ["reviewer"])

    def test_discovery_only_with_external_brief(self):
        self.config["discovery_only"] = True
        self.config["discovery_brief"] = {"reason": "External orchestrator needs facts", "investigations": [
            {"id": "source", "task": "Inspect source", "context": "", "model": "luna", "effort": "medium"}]}
        run_id = self.auto()
        self.assertEqual(router.run_pipeline(self.store, run_id)["status"], "completed")
        brief, discovery = self.store.sessions(run_id)
        self.assertEqual(len(self.store.attempts(brief["id"])), 0)
        self.assertEqual(discovery["model"], "luna")
        self.assertIsNone(self.store.run(run_id)["plan"])

    def test_requests_are_bounded_and_route_validated(self):
        request = {"id": "x", "task": "inspect", "context": "", "model": "sol", "effort": "medium"}
        validated = router.validate_discovery_requests([request], self.config)
        self.assertEqual(validated[0]["model"], "sol")
        for bad in ([request, request], [dict(request, id="../escape")], [dict(request, model="missing")]):
            with self.assertRaises(router.RouterError):
                router.validate_discovery_requests(bad, self.config)

    def test_discovery_uses_checkpoint_escalation_without_write_access(self):
        run_id = self.auto("DISCOVERY_CHECKPOINT")
        self.assertEqual(router.run_pipeline(self.store, run_id)["status"], "completed")
        discovery = self.store.sessions(run_id)[1]
        attempts = self.store.attempts(discovery["id"])
        self.assertEqual([a["effort"] for a in attempts], ["medium", "high"])
        self.assertEqual(self.store.latest_checkpoint(attempts[0]["id"])["status"], "needs_escalation")
        for directory in ("", "attempt-2"):
            invocation = json.loads((self.store.path / "logs" / run_id / discovery["task_id"] / directory / "invocation.json").read_text())
            self.assertEqual(invocation["args"][invocation["args"].index("--sandbox") + 1], "read-only")
