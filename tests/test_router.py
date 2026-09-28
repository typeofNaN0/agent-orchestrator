import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import router


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        router.git(self.repo, "init", "-q")
        router.git(self.repo, "config", "user.email", "test@example.invalid")
        router.git(self.repo, "config", "user.name", "Test")
        (self.repo / "base.txt").write_text("base\n")
        router.git(self.repo, "add", ".")
        router.git(self.repo, "commit", "-qm", "initial")
        self.store = router.Store(self.root / "state")
        self.config = router.load_config()
        self.config["providers"]["codex"]["command"] = [sys.executable, str(Path(__file__).with_name("fake_codex.py"))]

    def task(self, key="one", task="WRITE one.txt first", **kw):
        return dict(id=key, task=task, context="only this task's facts", model="luna", effort="medium",
                    rationale="mechanical change", depends_on=[], **kw)

    def plan(self, tasks=None, review="Complete and test"):
        return {"sidekicks": [self.task()] if tasks is None else tasks, "reviewer_prompt": review}

    def submit(self, plan=None):
        return router.submit(self.store, self.config, self.repo, "Original objective", "GLOBAL_PRIVATE_CONTEXT",
                             self.plan() if plan is None else plan)

    def test_pipeline_isolated_threads_context_and_patch(self):
        second = self.task("two", "WRITE two.txt second")
        second["context"] = "SECOND_PRIVATE_CONTEXT"
        run_id = self.submit(self.plan([self.task(), second]))
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "parent-thread"}):
            result = router.run_pipeline(self.store, run_id)
        self.assertEqual(result["status"], "completed")
        sessions = self.store.sessions(run_id)
        self.assertEqual(len(set(s["thread_id"] for s in sessions)), 3)
        self.assertNotIn("GLOBAL_PRIVATE_CONTEXT", sessions[0]["prompt"])
        self.assertNotIn("SECOND_PRIVATE_CONTEXT", sessions[0]["prompt"])
        self.assertNotIn("only this task's facts", sessions[-1]["prompt"])
        self.assertIn("GLOBAL_PRIVATE_CONTEXT", sessions[-1]["prompt"])
        self.assertIn("Compact result only", sessions[-1]["prompt"])
        self.assertEqual(router.git(self.repo, "status", "--porcelain"), b"")
        review_dir = Path(sessions[-1]["workspace"])
        self.assertEqual((review_dir / "one.txt").read_text(), "first")
        self.assertEqual((review_dir / "two.txt").read_text(), "second")
        final_patch = self.store.artifact(sessions[-1]["id"], "final.patch")
        router.git(self.repo, "apply", "--check", "-", data=final_patch)
        self.assertEqual(result["usage"]["input_tokens"], 300)
        invocation = json.loads((self.store.path / "logs" / run_id / "one" / "invocation.json").read_text())
        self.assertIsNone(invocation["parent_thread"])
        self.assertEqual(invocation["child"], "1")
        self.assertIn("gpt-6-luna", invocation["args"])
        self.assertIn('model_reasoning_effort="medium"', invocation["args"])
        self.assertNotIn("resume", invocation["args"])
        self.assertNotIn("fork", invocation["args"])

    def test_dependencies_are_incremental(self):
        first = self.task("a", "WRITE a.txt A")
        second = self.task("b", "WRITE b.txt B")
        second["depends_on"] = ["a"]
        third = self.task("c", "WRITE c.txt C")
        third["depends_on"] = ["b"]
        run_id = self.submit(self.plan([third, second, first]))
        self.assertEqual(router.run_pipeline(self.store, run_id)["status"], "completed")
        sessions = self.store.sessions(run_id)
        self.assertEqual([s["task_id"] for s in sessions], ["a", "b", "c", "reviewer"])
        patch_c = self.store.artifact(sessions[2]["id"], "patch")
        self.assertIn(b"c.txt", patch_c)
        self.assertNotIn(b"a.txt", patch_c)
        for name in ["a.txt", "b.txt", "c.txt"]:
            self.assertTrue((Path(sessions[-1]["workspace"]) / name).exists())

    def test_failed_dependency_blocks_descendant_but_reviewer_runs(self):
        first = self.task("a", "FAIL")
        second = self.task("b", "WRITE b.txt B")
        second["depends_on"] = ["a"]
        run_id = self.submit(self.plan([first, second], "DO_NOT_FINISH"))
        self.assertEqual(router.run_pipeline(self.store, run_id)["status"], "incomplete")
        sessions = self.store.sessions(run_id)
        self.assertEqual([s["status"] for s in sessions], ["failed", "blocked", "incomplete"])
        self.assertIn("Codex session failed", sessions[-1]["prompt"])

    def test_incomplete_worker_is_not_marked_completed(self):
        run_id = self.submit(self.plan([self.task(task="INCOMPLETE")]))
        router.run_pipeline(self.store, run_id)
        self.assertEqual(self.store.sessions(run_id)[0]["status"], "incomplete")

    def test_conflicts_are_explicit_for_reviewer(self):
        tasks = [self.task("a", "WRITE base.txt A"), self.task("b", "WRITE base.txt B")]
        run_id = self.submit(self.plan(tasks, "DO_NOT_FINISH"))
        router.run_pipeline(self.store, run_id)
        review = self.store.sessions(run_id)[-1]
        integration = json.loads(self.store.artifact(review["id"], "integration"))
        self.assertEqual([i["applied"] for i in integration], [True, False])
        self.assertIn('"applied": false', review["prompt"])
        self.assertEqual((self.repo / "base.txt").read_text(), "base\n")

    def test_timeout_and_review(self):
        self.config["timeout_seconds"] = 1
        run_id = self.submit(self.plan([self.task(task="SLEEP")]))
        router.run_pipeline(self.store, run_id)
        self.assertIn("timed out", self.store.sessions(run_id)[0]["error"])

    def test_no_duplicate_runner(self):
        run_id = self.submit(self.plan([]))
        router.run_pipeline(self.store, run_id)
        with self.assertRaisesRegex(router.RouterError, "already claimed"):
            router.run_pipeline(self.store, run_id)
        self.assertEqual(len(self.store.sessions(run_id)), 1)

    def test_automatic_planner_is_separate(self):
        run_id = router.submit(self.store, self.config, self.repo, "Small objective")
        self.assertEqual(router.run_pipeline(self.store, run_id)["status"], "completed")
        sessions = self.store.sessions(run_id)
        self.assertEqual([s["role"] for s in sessions], ["discovery_brief", "discovery", "orchestrator", "reviewer"])
        self.assertEqual(len(set(s["thread_id"] for s in sessions)), 4)
        inv = json.loads((self.store.path / "logs" / run_id / "orchestrator" / "invocation.json").read_text())
        self.assertIn("read-only", inv["args"])

    def test_standalone_launch_has_no_extra_agents(self):
        run_id = router.submit(self.store, self.config, self.repo, "Answer", standalone={"model": "sol", "effort": "max"})
        router.run_pipeline(self.store, run_id)
        sessions = self.store.sessions(run_id)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["effort"], "max")
        self.assertEqual(router.metrics(self.store, "standalone")["groups"][0]["attempts"], 1)

    def test_read_only_needs_no_git(self):
        self.config["workspace_mode"] = "read-only"
        folder = self.root / "plain"
        folder.mkdir()
        run_id = router.submit(self.store, self.config, folder, "Analyze", plan=self.plan([]))
        self.assertEqual(router.run_pipeline(self.store, run_id)["status"], "completed")
        review = self.store.sessions(run_id)[-1]
        self.assertEqual(review["workspace"], str(folder.resolve()))
        self.assertIsNone(self.store.artifact(review["id"], "final.patch"))

    def test_dirty_repo_rejected(self):
        (self.repo / "base.txt").write_text("unsaved")
        with self.assertRaisesRegex(router.RouterError, "Commit or stash"):
            self.submit()

    def test_cycle_and_unknown_dependency(self):
        a = self.task("a")
        a["depends_on"] = ["a"]
        with self.assertRaisesRegex(router.RouterError, "cycle"):
            self.submit(self.plan([a]))
        a["depends_on"] = ["missing"]
        with self.assertRaisesRegex(router.RouterError, "Unknown dependency"):
            self.submit(self.plan([a]))

    def test_invalid_selection(self):
        for spec in ({"model": "terra", "effort": "max"}, {"model": "luna", "effort": "ultra"}):
            with self.assertRaises(router.RouterError):
                router.selection(self.config, spec)

    def test_complexity_cannot_supply_missing_route(self):
        task = self.task()
        del task["model"], task["effort"]
        task["complexity"] = "simple"
        with self.assertRaisesRegex(router.RouterError, "Explicit model and effort"):
            router.validate_plan(self.plan([task]), self.config)

    def test_limit_and_path_injection(self):
        task = self.task("../../escape")
        with self.assertRaises(router.RouterError):
            self.submit(self.plan([task]))
        with self.assertRaises(router.RouterError):
            self.submit(self.plan([self.task(str(i)) for i in range(7)]))

    def test_nested_invocation_rejected(self):
        with patch.dict(os.environ, {"SIDEKICK_ROUTER_CHILD": "1"}):
            with self.assertRaisesRegex(router.RouterError, "Nested"):
                self.submit()

    def test_markdown_store_survives_reopen(self):
        run_id = self.submit(self.plan([]))
        store = router.Store(self.store.path)
        self.assertEqual(store.run(run_id)["status"], "pending")
        router.run_pipeline(store, run_id)
        self.assertEqual(router.Store(self.store.path).run(run_id)["status"], "completed")

    def test_command_provider_contract(self):
        script = self.root / "provider.py"
        script.write_text('import json,sys\nr=json.load(sys.stdin)\nprint(json.dumps({"thread_id":"external", "usage":{}, "report":{"status":"completed","summary":r["model"],"tests":[],"risks":[],"handoff":"done"}}))\n')
        self.config["providers"]["external"] = {"type": "command", "command": [sys.executable, str(script)]}
        self.config["models"]["future"] = {"provider": "external", "model": "future-model", "efforts": ["deep"]}
        run_id = router.submit(self.store, self.config, self.repo, "Answer", standalone={"model": "future", "effort": "deep"})
        router.run_pipeline(self.store, run_id)
        session = self.store.sessions(run_id)[0]
        self.assertEqual(json.loads(session["report"])["summary"], "future-model")

    def test_background_runner_completes(self):
        run_id = self.submit(self.plan([]))
        started = router.start_background(self.store, run_id)
        import time
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and self.store.run(run_id)["status"] not in router.TERMINAL:
            time.sleep(.05)
        self.assertEqual(self.store.run(run_id)["status"], "completed")
        self.assertTrue(Path(started["runner_log"]).exists())


if __name__ == "__main__":
    unittest.main()
