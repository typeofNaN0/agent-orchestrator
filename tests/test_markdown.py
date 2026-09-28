import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import router
import test_router


class MarkdownTests(unittest.TestCase):
    setUp = test_router.RouterTests.setUp
    task = test_router.RouterTests.task
    plan = test_router.RouterTests.plan
    submit = test_router.RouterTests.submit

    def cli(self, *args, cwd=None):
        return subprocess.run([sys.executable, "-B", router.__file__, "--store-dir", str(self.store.path), *map(str, args)],
                              cwd=cwd or router.HERE, capture_output=True, text=True, timeout=30)

    def config_file(self):
        path = self.root / "config.json"
        path.write_text(json.dumps(self.config))
        return path

    def test_no_database_and_exact_prompt_snapshot(self):
        run_id = self.submit()
        result = router.run_pipeline(self.store, run_id)
        reopened = router.Store(self.store.path)
        self.assertEqual(router.status(reopened, run_id, full=True)["status"], "completed")
        self.assertEqual(result["store_dir"], str(self.store.path))
        self.assertNotIn("database", result)
        self.assertEqual(result["usage"]["cached_input_tokens"], 40)
        self.assertFalse(any(p.suffix in (".db", ".sqlite", ".sqlite3") for p in self.store.path.rglob("*")))
        for session in reopened.sessions(run_id):
            for attempt in reopened.attempts(session["id"]):
                prompt_path = reopened.run_dir(run_id) / "sessions" / session["id"] / "attempts" / attempt["id"] / "prompt.md"
                invocation = json.loads((self.store.path / "logs" / run_id / session["task_id"] / "invocation.json").read_text())
                self.assertEqual(prompt_path.read_bytes(), invocation["prompt"].encode())
                self.assertEqual(attempt["prompt"], invocation["prompt"])
        exported = self.root / "result.patch"
        output = self.cli("export", run_id, "--output", exported)
        self.assertEqual(output.returncode, 0, output.stderr)
        router.git(self.repo, "apply", "--check", "-", data=exported.read_bytes())
        self.assertEqual(json.loads(self.cli("metrics").stdout)["groups"][0]["cached_input_tokens"], 20)

    def test_duplicate_processes_claim_only_one_runner(self):
        run_id = self.submit()
        command = [sys.executable, "-B", router.__file__, "--store-dir", str(self.store.path), "run", run_id]
        processes = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        try:
            results = [p.communicate(timeout=30) for p in processes]
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.wait()
        self.assertEqual(sorted(p.returncode for p in processes), [0, 1], results)
        self.assertEqual(len(self.store.sessions(run_id)), 2)
        self.assertEqual(self.store.run(run_id)["status"], "completed")
        self.assertIn("already claimed", next(err for p, (_, err) in zip(processes, results) if p.returncode))

    def test_independent_processes_run_concurrently(self):
        run_ids = [self.submit(self.plan([self.task("a", "WRITE a.txt A"), self.task("b", "WRITE b.txt B")])) for _ in range(3)]
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda rid: self.cli("run", rid), run_ids))
        for run_id, result in zip(run_ids, results):
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(self.store.sessions(run_id)), 3)
            events = self.store.events(run_id)
            self.assertEqual(sum(e["kind"] == "session_finished" for e in events), 3)
            self.assertEqual(len({e["id"] for e in events}), len(events))
            self.assertEqual([e["sequence"] for e in events], list(range(1, len(events) + 1)))

    def test_other_process_cannot_modify_owned_run(self):
        run_id = self.submit(self.plan([]))
        self.store.claim_run(run_id)
        code = "import router,sys; router.Store(sys.argv[1]).update('runs',sys.argv[2],status='completed')"
        result = subprocess.run([sys.executable, "-B", "-c", code, str(self.store.path), run_id],
                                cwd=router.HERE, capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("owned by another process", result.stderr)
        self.assertEqual(self.store.run(run_id)["status"], "running")

    def test_attempt_budget_shared_by_reopened_stores(self):
        self.config["adaptive"]["max_total_attempts"] = 1
        run_id = self.submit(self.plan([]))
        run = self.store.claim_run(run_id)
        workers = [self.store.add_session(run_id, str(i), "sidekick", "luna", "medium", "") for i in range(8)]
        def reserve(worker):
            try:
                router.Store(self.store.path).attempt(run, worker, "task")
                return True
            except router.RouterError:
                return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(reserve, workers)), 1)

    def test_parallel_events_are_not_lost(self):
        run_id = self.submit(self.plan([]))
        self.store.claim_run(run_id)
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda n: router.Store(self.store.path).event(run_id, "test", {"n": n}), range(40)))
        events = [e for e in self.store.events(run_id) if e["kind"] == "test"]
        self.assertEqual(sorted(e["data"]["n"] for e in events), list(range(40)))

    def test_atomic_update_failure_preserves_prior_record(self):
        run_id = self.submit(self.plan([]))
        self.store.claim_run(run_id)
        path = self.store.run_dir(run_id) / "run.md"
        original = path.read_bytes()
        with patch("router.os.replace", side_effect=OSError("interrupted write")):
            with self.assertRaisesRegex(OSError, "interrupted write"):
                self.store.update("runs", run_id, error="new value")
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(router.Store(self.store.path).run(run_id)["status"], "running")
        self.assertFalse(list(path.parent.glob("*.tmp")))

    def test_readers_never_observe_partial_records(self):
        run_id = self.submit(self.plan([]))
        self.store.claim_run(run_id)
        finished = threading.Event()
        def write():
            try:
                for n in range(30):
                    self.store.update("runs", run_id, error="message " + str(n))
            finally:
                finished.set()
        def read():
            reader = router.Store(self.store.path)
            while not finished.is_set():
                value = reader.run(run_id)
                self.assertEqual(value["task"], "Original objective")
                self.assertEqual(value["status"], "running")
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(read), pool.submit(read), pool.submit(write)]
            for future in futures:
                future.result(timeout=15)

    def test_unpublished_runs_are_invisible(self):
        run_id = self.submit(self.plan([]))
        staged = dict(self.store.run(run_id), id=uuid.uuid4().hex)
        reader = router.Store(self.store.path)
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            with self.store.new_run(staged):
                self.assertEqual([r["id"] for r in reader.runs()], [run_id])
                raise RuntimeError("interrupted")
        self.assertEqual([r["id"] for r in reader.runs()], [run_id])

    def test_interrupted_claim_is_visible_and_never_replayed(self):
        run_id = self.submit(self.plan([]))
        (self.store.run_dir(run_id) / "claim").mkdir()
        self.assertIn("Interrupted", router.status(self.store, run_id)["warning"])
        with self.assertRaisesRegex(router.RouterError, "already claimed"):
            router.run_pipeline(self.store, run_id)
        self.assertEqual(self.store.sessions(run_id), [])

    def test_unclaimed_run_rejects_scheduler_writes(self):
        run_id = self.submit(self.plan([]))
        with self.assertRaisesRegex(router.RouterError, "claim it before writing"):
            self.store.update("runs", run_id, status="completed")
        self.assertEqual(self.store.run(run_id)["status"], "pending")

    def test_malformed_records_report_the_path(self):
        run_id = self.submit(self.plan([]))
        path = self.store.run_dir(run_id) / "run.md"
        path.write_text("truncated document")
        with self.assertRaisesRegex(router.RouterError, str(path)):
            router.status(self.store, run_id)
        router.write_record(path, "run", {"id": run_id})
        with self.assertRaisesRegex(router.RouterError, "Missing fields"):
            router.status(self.store, run_id)

    def test_store_must_be_external_even_in_read_only_mode(self):
        self.config["workspace_mode"] = "read-only"
        with self.assertRaisesRegex(router.RouterError, "router source"):
            router.Store(router.HERE / "data")
        with self.assertRaisesRegex(router.RouterError, "outside the target workspace"):
            router.submit(router.Store(self.repo / "data"), self.config, self.repo, "inspect")
        self.assertFalse((self.repo / "data").exists())
        with self.assertRaisesRegex(router.RouterError, "Invalid record ID"):
            self.store.run("../escape")

    def test_legacy_database_is_untouched(self):
        archive = self.root / "state.sqlite3"
        archive.write_bytes(b"legacy archive")
        with self.assertRaisesRegex(router.RouterError, "legacy SQLite"):
            router.Store(archive)
        self.submit(self.plan([]))
        self.assertEqual(archive.read_bytes(), b"legacy archive")

    def test_markdown_content_with_fences_round_trips(self):
        content = "# Context\n```json\n{\"instruction\": \"literal\"}\n```\nUnicode: café\n"
        run_id = router.submit(self.store, self.config, self.repo, content, content, self.plan([]))
        reopened = router.Store(self.store.path).run(run_id)
        self.assertEqual(reopened["task"], content)
        self.assertEqual(reopened["context"], content)

    def test_explicit_initial_route_fields_required(self):
        for missing in ("model", "effort"):
            assignment = self.task()
            del assignment[missing]
            with self.assertRaisesRegex(router.RouterError, "Explicit model and effort"):
                self.submit(self.plan([assignment]))
            with self.assertRaisesRegex(router.RouterError, "Explicit model and effort"):
                router.validate_discovery_requests([assignment], self.config)
        self.assertEqual(self.store.runs(), [])

    def test_prompt_files_resolve_and_snapshot_before_background_execution(self):
        inputs = self.root / "input files"
        inputs.mkdir()
        (inputs / "task.md").write_text("Original objective", encoding="utf-8")
        (inputs / "assignment.md").write_text("WRITE one.txt snapshotted", encoding="utf-8")
        (inputs / "context.md").write_text("ONLY_THIS_WORKER", encoding="utf-8")
        (inputs / "review.md").write_text("Complete and verify", encoding="utf-8")
        task = self.task()
        del task["task"], task["context"]
        task.update(task_file="assignment.md", context_file="context.md")
        (inputs / "plan.json").write_text(json.dumps({"sidekicks": [task], "reviewer_prompt_file": "review.md"}))
        submitted = self.cli("--config", self.config_file(), "submit", "--workspace", self.repo,
                             "--task-file", "task.md", "--plan", "plan.json", cwd=inputs)
        self.assertEqual(submitted.returncode, 0, submitted.stderr)
        run_id = json.loads(submitted.stdout)["run_id"]
        (inputs / "assignment.md").write_text("FAIL")
        (inputs / "context.md").write_text("CHANGED_SOURCE")
        started = self.cli("start", run_id)
        self.assertEqual(started.returncode, 0, started.stderr)
        import time
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and self.store.run(run_id)["status"] not in router.TERMINAL:
            time.sleep(.05)
        self.assertEqual(self.store.run(run_id)["status"], "completed")
        worker, reviewer = self.store.sessions(run_id)
        self.assertIn("ONLY_THIS_WORKER", worker["prompt"])
        self.assertNotIn("CHANGED_SOURCE", worker["prompt"])
        self.assertNotIn("ONLY_THIS_WORKER", reviewer["prompt"])
        self.assertEqual((Path(reviewer["workspace"]) / "one.txt").read_text(), "snapshotted")
        sources = router.read_record(self.store.run_dir(run_id) / "sources.md", "sources")["files"]
        self.assertEqual(len(sources), 4)
        for source in sources:
            raw = (self.store.run_dir(run_id) / source["snapshot"]).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), source["sha256"])
            self.assertTrue(Path(source["path"]).is_absolute())

    def test_discovery_brief_prompt_files_and_explicit_route(self):
        folder = self.root / "brief"
        folder.mkdir()
        (folder / "investigate.md").write_text("Inspect source", encoding="utf-8")
        (folder / "context.md").write_text("DISCOVERY_ONLY_CONTEXT", encoding="utf-8")
        brief = {"reason": "Need facts", "investigations": [{"id": "source", "task_file": "investigate.md",
                 "context_file": "context.md", "model": "luna", "effort": "low"}]}
        (folder / "brief.json").write_text(json.dumps(brief))
        result = self.cli("--config", self.config_file(), "discover", "--workspace", self.repo, "--task", "Investigate",
                          "--brief", folder / "brief.json", "--wait")
        self.assertEqual(result.returncode, 0, result.stderr)
        run_id = json.loads(result.stdout)["run_id"]
        discovery = self.store.sessions(run_id)[1]
        self.assertEqual((discovery["model"], discovery["effort"]), ("luna", "low"))
        self.assertIn("DISCOVERY_ONLY_CONTEXT", discovery["prompt"])

    def test_invalid_files_and_ambiguous_inputs_fail_before_submission(self):
        config = self.config_file()
        sources = []
        file = self.root / "prompt.md"
        file.write_text("saved text")
        with self.assertRaisesRegex(router.RouterError, "only one"):
            router.load_prompt_fields({"task": "inline", "task_file": str(file)}, ("task",), self.root, "task", self.config, sources)
        for raw, error in ((b"x" * (self.config["max_context_chars"] + 1), "exceeds limit"), (b"\xff", "UTF-8")):
            file.write_bytes(raw)
            result = self.cli("--config", config, "submit", "--workspace", self.repo, "--task-file", file)
            self.assertEqual(result.returncode, 1)
            self.assertIn(error, result.stderr)
        result = self.cli("--config", config, "submit", "--workspace", self.repo, "--task-file", self.root / "missing.md")
        self.assertIn("does not exist", result.stderr)
        result = self.cli("submit", "--workspace", self.repo, "--task", "inline", "--task-file", file)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.store.runs(), [])

    def test_equivalent_files_have_stable_prefixes_despite_paths(self):
        prompts = []
        for name in ("a.md", "different name.md"):
            source = self.root / name
            source.write_text("WRITE one.txt value", encoding="utf-8")
            assignment = self.task()
            del assignment["task"]
            assignment["task_file"] = str(source)
            sources = []
            assignment = router.load_prompt_fields(assignment, ("task", "context"), self.root, "worker", self.config, sources)
            run_id = router.submit(self.store, self.config, self.repo, "Original objective", plan=self.plan([assignment]), source_snapshots=sources)
            router.run_pipeline(self.store, run_id)
            prompt = self.store.sessions(run_id)[0]["prompt"]
            self.assertNotIn(str(source), prompt)
            self.assertEqual(prompt.count("Original objective"), 1)
            prompts.append(prompt.split("\nCheckpoint protocol:")[0])
        self.assertEqual(prompts[0], prompts[1])


if __name__ == "__main__":
    unittest.main()
