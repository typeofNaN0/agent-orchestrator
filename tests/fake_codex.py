"""Deterministic executable standing in for Codex. Never makes model calls."""
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

args = sys.argv[1:]
prompt = sys.stdin.read()
output = Path(args[args.index("--output-last-message") + 1])
cwd = Path(args[args.index("--cd") + 1])
schema = json.loads(Path(args[args.index("--output-schema") + 1]).read_text())
thread_id = args[-2] if "resume" in args else uuid.uuid4().hex
print(json.dumps({"type": "thread.started", "thread_id": thread_id}), flush=True)
(output.parent / "invocation.json").write_text(json.dumps({
    "args": args, "prompt": prompt, "parent_thread": os.environ.get("CODEX_THREAD_ID"),
    "child": os.environ.get("SIDEKICK_ROUTER_CHILD"),
}))
if "investigations" in schema["properties"]:
    report = {"reason": "Inspect the source before choosing a plan", "investigations": [
        {"id": "source", "task": "Inspect relevant source and tests", "context": "Read only", "model": "sol", "effort": "medium"}]}
    if "SKIP_DISCOVERY" in prompt:
        report["investigations"] = []
elif "sidekicks" in schema["properties"]:
    report = {"sidekicks": [], "reviewer_prompt": "Finish the task and verify it."}
    if "PLAN_FROM_DISCOVERY" in prompt:
        assert "DISCOVERED_FACT" in prompt
        report["sidekicks"] = [{"id": "implementation", "task": "WRITE discovered.txt implemented", "context": "Use DISCOVERED_FACT",
                                "model": "luna", "effort": "medium", "rationale": "Discovery found a mechanical task", "depends_on": []}]
    if ("ASK_FOLLOWUP" in prompt and 'discovery-2-deeper' not in prompt) or "ENDLESS_DISCOVERY" in prompt:
        report.update(sidekicks=[], discovery_reason="Need one more fact", discovery_requests=[
            {"id": "deeper", "task": "Inspect the unresolved issue", "context": "Focused follow-up", "model": "sol", "effort": "medium"}])
else:
    reviewer = "You are the final reviewer and integrator" in prompt
    attempt = int(output.parent.name.split("-")[-1]) if output.parent.name.startswith("attempt-") else 1
    model = args[args.index("-m") + 1]
    effort = next(x for x in args if x.startswith("model_reasoning_effort=")).split("=", 1)[1].strip('"')
    assignment = prompt.split("Your assignment:\n")[-1].split("\nYour context:")[0] if not reviewer else ""
    checkpoint = {"status": "needs_escalation", "completed": ["saved partial work"], "evidence": ["one case failed"],
                  "failed_tests": ["test_behavior"], "blocker": "needs more reasoning", "remaining_work": ["finish"],
                  "suggested_model": "", "suggested_effort": ""}
    if assignment.startswith("NETWORK") and (attempt == 1 or "ALWAYS" in assignment):
        print("503 network temporarily unavailable", file=sys.stderr)
        sys.exit(1)
    if assignment.startswith("LIVE_ESCALATE") and attempt == 1:
        (cwd / "preserved.txt").write_text("partial work")
        inbox = Path(args[args.index("--add-dir") + 1])
        temp = inbox / "checkpoint.tmp"
        temp.write_text(json.dumps(checkpoint))
        temp.replace(inbox / "checkpoint.json")
        time.sleep(5)
    if assignment.startswith("LIVE_ESCALATE") and attempt > 1:
        assert (cwd / "preserved.txt").read_text() == "partial work"
        (cwd / "finished.txt").write_text("done")
    if assignment.startswith("REPEATED_FAILURE") and attempt == 1:
        inbox = Path(args[args.index("--add-dir") + 1])
        checkpoint["status"] = "progress"
        for _ in range(4):
            temp = inbox / "checkpoint.tmp"
            temp.write_text(json.dumps(checkpoint))
            temp.replace(inbox / "checkpoint.json")
            time.sleep(.3)
        time.sleep(5)
    if assignment.startswith("STALL") and attempt == 1:
        time.sleep(5)
    if assignment.startswith("VERIFY_FIX"):
        (cwd / "answer.txt").write_text("correct" if effort == "high" else "wrong")
    if assignment.startswith("ALWAYS_WRONG"):
        (cwd / "answer.txt").write_text("wrong")
    if "Your assignment:\nFAIL" in prompt:
        print(json.dumps({"type": "turn.failed", "error": {"message": "deliberate failure"}}))
        sys.exit(2)
    if "Your assignment:\nSLEEP" in prompt:
        time.sleep(3)
    match = re.match(r"WRITE ([\w.-]+) (.*)", assignment)
    if match:
        (cwd / match[1]).write_text(match[2])
    status = "incomplete" if "Your assignment:\nINCOMPLETE" in prompt else "completed"
    if reviewer and "DO_NOT_FINISH" in prompt:
        status = "incomplete"
    report = {"status": status, "summary": "Verified fake run", "tests": ["fake verification"],
              "risks": [], "handoff": "Compact result only", "checkpoint": None, "repair_requests": []}
    if "discovery" in schema["properties"]:
        report["discovery"] = {"findings": [{"topic": "Source", "detail": "DISCOVERED_FACT", "files": ["base.txt"], "symbols": ["base"]}],
                               "existing_behavior": ["Baseline behavior"], "conventions": ["Plain text"], "candidate_checks": ["python3 -m unittest"],
                               "dependencies": [], "unresolved_questions": []}
        if "DISCOVERY_FAIL" in prompt:
            print("model not found", file=sys.stderr)
            sys.exit(1)
        if "DISCOVERY_WRITES" in prompt:
            (cwd / "base.txt").write_text("unwanted edit")
        if "DISCOVERY_BAD_CITATION" in prompt:
            report["discovery"]["findings"][0]["files"] = ["missing.txt"]
        if "DISCOVERY_CHECKPOINT" in prompt and attempt == 1:
            report.update(status="needs_escalation", checkpoint=checkpoint)
    if assignment.startswith("YIELD_ONCE") and attempt == 1:
        checkpoint["status"] = "yield"
        report.update(status="checkpoint", checkpoint=checkpoint)
    if reviewer and (("REQUEST_REPAIR" in prompt and not (cwd / "repair.txt").exists()) or "ALWAYS_REPAIR" in prompt):
        report.update(status="needs_repair", repair_requests=[{
            "id": "fix", "task": "WRITE repair.txt fixed", "context": "Finish the mechanical repair",
            "model": "luna", "effort": "medium", "rationale": "bounded fix", "task_type": "repair",
            "checks": [], "context_keys": [], "acceptance_criteria": ["repair.txt contains fixed"]}])
    if assignment.startswith("MALFORMED"):
        report = {"status": "completed"}
output.write_text(json.dumps(report))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 10}}))
