#!/usr/bin/env python3
"""ODAR production CLI.

Commands:
  run     "question"    create (or deduplicate) a job and execute it
  resume  JOB_ID        resume a crashed/cancelled run from its checkpoint
  status  JOB_ID        show job status / result summary
  list                  list recent jobs
  cancel  JOB_ID        request cancellation of a running or pending job
  health                liveness / readiness / config report

Every run is a durable job: idempotency keys prevent duplicate expensive
runs, checkpoints survive crashes, cancellation is propagated to the loop.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from typing import Any, Dict, Optional

from odar.budget import Budget, CancellationToken
from odar.engine import FALLBACK_EXPLICIT_SCRIPTED, FALLBACK_FAIL, ResearchEngine
from odar.health import liveness, readiness, validate_config
from odar.jobstore import JobStore
from odar.llm import NativeToolUseController, ScriptedResearchController
from odar.research_state import ResearchState
from odar.telemetry import TelemetryRecorder


def build_budget(args: argparse.Namespace, fast: bool) -> Budget:
    factor = 0.5 if fast else 1.0
    return Budget(
        max_iterations=max(4, int(args.max_iterations * factor)),
        max_search_calls=max(2, int(args.max_search * factor)),
        max_fetches=max(2, int(args.max_fetch * factor)),
        max_sandbox_executions=max(2, int(args.max_sandbox * factor)),
        max_model_calls=max(4, int(args.max_model_calls * factor)),
        max_wall_clock_s=args.timeout,
        max_retries_per_call=2,
    )


def build_engine(
    args: argparse.Namespace, jobstore: JobStore, job_id: str, budget: Budget, token: CancellationToken
) -> ResearchEngine:
    if args.model == "llm":
        from odar.agent import AnthropicSDKAdapter

        adapter = AnthropicSDKAdapter(
            model=getattr(args, "llm_model", None), base_url=args.base_url or None
        )
        model = NativeToolUseController(adapter=adapter)
    else:
        model = ScriptedResearchController()
    telemetry = TelemetryRecorder()

    def _checkpoint(state: ResearchState) -> None:
        jobstore.save_checkpoint(job_id, state.to_checkpoint())

    policy = (
        FALLBACK_EXPLICIT_SCRIPTED
        if getattr(args, "llm_fallback", "fail") == "explicit-scripted"
        else FALLBACK_FAIL
    )
    return ResearchEngine(
        model=model,
        budget=budget,
        telemetry=telemetry,
        token=token,
        cancel_check=lambda: jobstore.is_cancel_requested(job_id),
        checkpoint_sink=_checkpoint,
        fallback_policy=policy,
    )


def write_outputs(output_dir: str, job_id: str, outcome: Any) -> Dict[str, str]:
    os.makedirs(output_dir, exist_ok=True)
    payload = outcome.to_dict()
    json_path = os.path.join(output_dir, f"{job_id}.json")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False, default=str)
    md_path = os.path.join(output_dir, f"{job_id}.md")
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write(outcome.synthesis or "(no synthesis produced)\n")
        handle.write(
            f"\n\n---\nstatus: {payload['status']} | uncertainty: {payload['uncertainty']} | "
            f"run: {payload['run_id']}\n"
        )
        if payload.get("unmet_conditions"):
            handle.write("unmet termination conditions: " + ", ".join(payload["unmet_conditions"]) + "\n")
    return {"json": json_path, "md": md_path}


def cmd_run(args: argparse.Namespace) -> int:
    jobstore = JobStore(args.db)
    config = {
        "budget": {
            "max_iterations": args.max_iterations,
            "max_search_calls": args.max_search,
            "max_fetches": args.max_fetch,
            "max_wall_clock_s": args.timeout,
        },
        "model": args.model,
        "output_dir": args.output_dir,
    }
    problems = validate_config(config)
    if problems:
        for problem in problems:
            print(f"config error: {problem}", file=sys.stderr)
        return 2

    job = jobstore.create_job(args.question, idempotency_key=args.idempotency_key)
    job_id = job["job_id"]

    # Idempotency: an already-finished job with this key returns its result.
    if job["status"] in ("COMPLETE", "INCOMPLETE", "BUDGET_EXHAUSTED") and job.get("result"):
        print(f"[idempotent] job {job_id} already finished with status {job['status']}")
        print(job["result"][: args.preview_chars] if not args.json else job["result"])
        jobstore.close()
        return 0

    if not jobstore.lease(job_id, node_id=f"cli:{os.getpid()}"):
        current = jobstore.get(job_id)
        print(f"job {job_id} is not leasable (status={current and current['status']})", file=sys.stderr)
        jobstore.close()
        return 1

    budget = build_budget(args, fast=args.fast)
    token = CancellationToken(deadline=time.monotonic() + args.timeout)
    engine = build_engine(args, jobstore, job_id, budget, token)
    jobstore.set_status(job_id, "RUNNING")
    jobstore.append_event(job_id, "run.start", {"objective": args.question[:200]})

    def _sigterm(_signum: int, _frame: Any) -> None:
        token.cancel("signal_received")

    try:
        signal.signal(signal.SIGTERM, _sigterm)
    except ValueError:
        pass

    try:
        outcome = engine.run(args.question)
    except KeyboardInterrupt:
        jobstore.requeue(job_id)
        jobstore.close()
        print(f"\ninterrupted: job {job_id} preserved for `resume {job_id}`", file=sys.stderr)
        return 130

    result_json = json.dumps(outcome.to_dict(), ensure_ascii=False, default=str)
    jobstore.set_status(job_id, outcome.status, error="; ".join(outcome.errors) or None, result=result_json)
    jobstore.save_checkpoint(job_id, outcome.state.to_checkpoint())
    jobstore.append_event(job_id, "run.end", {"status": outcome.status})
    paths = write_outputs(args.output_dir, job_id, outcome)

    print(f"job: {job_id}")
    print(f"status: {outcome.status} | uncertainty: {outcome.state.uncertainty.value}")
    print(
        f"certified claims: {len(outcome.state.supporting_claims())} | evidence items: {len(outcome.state.evidence)}"
    )
    if outcome.audit is not None:
        print(f"final audit: {outcome.audit.status}")
    if outcome.state.unmet_conditions:
        print("unmet conditions: " + ", ".join(outcome.state.unmet_conditions))
    print(f"report: {paths['md']}")
    print(f"payload: {paths['json']}")
    if args.json:
        print(result_json[: args.preview_chars])
    jobstore.close()
    return 0 if outcome.status == "COMPLETE" else 3


def cmd_resume(args: argparse.Namespace) -> int:
    jobstore = JobStore(args.db)
    job = jobstore.get(args.job_id)
    if job is None:
        jobstore.close()
        print(f"no such job: {args.job_id}", file=sys.stderr)
        return 1
    if job["status"] not in ("RUNNING", "RESUME", "CANCELLED", "FAILED", "INCOMPLETE", "BUDGET_EXHAUSTED"):
        print(f"job {args.job_id} has status {job['status']}; nothing to resume", file=sys.stderr)
        jobstore.close()
        return 1
    checkpoint = jobstore.checkpoint(args.job_id)
    if not checkpoint:
        print(f"job {args.job_id} has no checkpoint; rerun instead", file=sys.stderr)
        jobstore.close()
        return 1
    jobstore.requeue(args.job_id)
    if not jobstore.lease(args.job_id, node_id=f"cli-resume:{os.getpid()}"):
        print(f"could not lease job {args.job_id}", file=sys.stderr)
        jobstore.close()
        return 1

    state = ResearchState.from_checkpoint(checkpoint)
    budget = build_budget(args, fast=args.fast)
    token = CancellationToken(deadline=time.monotonic() + args.timeout)
    engine = build_engine(args, jobstore, args.job_id, budget, token)
    outcome = engine.run(state.objective, resume_state=state)

    result_json = json.dumps(outcome.to_dict(), ensure_ascii=False, default=str)
    jobstore.set_status(
        args.job_id, outcome.status, error="; ".join(outcome.errors) or None, result=result_json
    )
    jobstore.save_checkpoint(args.job_id, outcome.state.to_checkpoint())
    paths = write_outputs(args.output_dir, args.job_id, outcome)
    print(f"resumed job: {args.job_id}")
    print(f"status: {outcome.status} | uncertainty: {outcome.state.uncertainty.value}")
    print(f"report: {paths['md']}")
    jobstore.close()
    return 0 if outcome.status == "COMPLETE" else 3


def cmd_status(args: argparse.Namespace) -> int:
    jobstore = JobStore(args.db)
    try:
        job = jobstore.get(args.job_id)
        if job is None:
            print(f"no such job: {args.job_id}", file=sys.stderr)
            return 1
        summary = {
            key: job.get(key)
            for key in (
                "job_id",
                "idempotency_key",
                "objective",
                "status",
                "attempt",
                "node_id",
                "created_at",
                "started_at",
                "finished_at",
                "updated_at",
                "error",
            )
        }
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        if args.events:
            for event in jobstore.events(args.job_id, limit=20):
                print(f"  {event['ts']} {event['name']}")
        return 0
    finally:
        jobstore.close()


def cmd_list(args: argparse.Namespace) -> int:
    jobstore = JobStore(args.db)
    try:
        rows = jobstore.list_jobs(limit=args.limit)
        if not rows:
            print("(no jobs)")
            return 0
        for row in rows:
            print(f"{row['job_id']}  {row['status']:<18} attempt={row['attempt']}  {row['objective'][:70]}")
        return 0
    finally:
        jobstore.close()


def cmd_cancel(args: argparse.Namespace) -> int:
    jobstore = JobStore(args.db)
    try:
        if jobstore.cancel(args.job_id):
            print(f"cancel requested for {args.job_id}")
            return 0
        print(f"cannot cancel {args.job_id} (missing or already terminal)", file=sys.stderr)
        return 1
    finally:
        jobstore.close()


def cmd_health(args: argparse.Namespace) -> int:
    live = liveness()
    ready = readiness(jobstore_path=args.db)
    report = {
        "liveness": live.to_dict(),
        "readiness": ready.to_dict(),
        "model_backend": ready.checks.get("model_backend", {}),
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if ready.status != "FAIL" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_research", description=__doc__)
    parser.add_argument("--db", default=os.environ.get("ODAR_DB", "odar_jobs.db"))
    sub = parser.add_subparsers(dest="command", required=True)

    def _add_db(p: argparse.ArgumentParser) -> None:
        p.add_argument("--db", default=os.environ.get("ODAR_DB", "odar_jobs.db"))

    p_run = sub.add_parser("run", help="run a research job")
    p_run.add_argument("question")
    p_run.add_argument("--idempotency-key", default=None)
    p_run.add_argument(
        "--model",
        choices=("scripted", "llm"),
        default="llm" if os.environ.get("ANTHROPIC_API_KEY") else "scripted",
    )
    p_run.add_argument("--base-url", default=os.environ.get("ODAR_ANTHROPIC_BASE_URL"))
    p_run.add_argument(
        "--llm-model",
        default=os.environ.get("ODAR_ANTHROPIC_MODEL"),
        help="Anthropic(-compatible) model id for --model llm (default: claude-sonnet-5-5)",
    )
    p_run.add_argument("--max-iterations", type=int, default=12)
    p_run.add_argument("--max-search", type=int, default=6)
    p_run.add_argument("--max-fetch", type=int, default=4)
    p_run.add_argument("--max-sandbox", type=int, default=4)
    p_run.add_argument("--max-model-calls", type=int, default=16)
    p_run.add_argument("--timeout", type=float, default=180.0, help="wall-clock deadline (seconds)")
    p_run.add_argument("--output-dir", default="odar_output")
    p_run.add_argument("--fast", action="store_true", help="halve search/fetch/iteration budgets")
    p_run.add_argument("--json", action="store_true")
    p_run.add_argument("--preview-chars", type=int, default=2000)
    p_run.add_argument(
        "--llm-fallback",
        choices=("fail", "explicit-scripted"),
        default="fail",
        help="policy when the LLM backend fails: fail the run (default) or "
        "explicitly continue under the scripted controller (marked DEGRADED)",
    )
    _add_db(p_run)
    p_run.set_defaults(func=cmd_run)

    p_resume = sub.add_parser("resume", help="resume a durable job from its checkpoint")
    p_resume.add_argument("job_id")
    p_resume.add_argument(
        "--model",
        choices=("scripted", "llm"),
        default="llm" if os.environ.get("ANTHROPIC_API_KEY") else "scripted",
    )
    p_resume.add_argument("--base-url", default=os.environ.get("ODAR_ANTHROPIC_BASE_URL"))
    p_resume.add_argument(
        "--llm-model",
        default=os.environ.get("ODAR_ANTHROPIC_MODEL"),
        help="Anthropic(-compatible) model id for --model llm (default: claude-sonnet-5-5)",
    )
    p_resume.add_argument("--max-iterations", type=int, default=12)
    p_resume.add_argument("--max-search", type=int, default=6)
    p_resume.add_argument("--max-fetch", type=int, default=4)
    p_resume.add_argument("--max-sandbox", type=int, default=4)
    p_resume.add_argument("--max-model-calls", type=int, default=16)
    p_resume.add_argument("--timeout", type=float, default=180.0)
    p_resume.add_argument("--output-dir", default="odar_output")
    p_resume.add_argument("--fast", action="store_true")
    _add_db(p_resume)
    p_resume.set_defaults(func=cmd_resume)

    p_status = sub.add_parser("status", help="job status")
    p_status.add_argument("job_id")
    p_status.add_argument("--events", action="store_true")
    _add_db(p_status)
    p_status.set_defaults(func=cmd_status)

    p_list = sub.add_parser("list", help="list jobs")
    p_list.add_argument("--limit", type=int, default=20)
    _add_db(p_list)
    p_list.set_defaults(func=cmd_list)

    p_cancel = sub.add_parser("cancel", help="request cancellation")
    p_cancel.add_argument("job_id")
    _add_db(p_cancel)
    p_cancel.set_defaults(func=cmd_cancel)

    p_health = sub.add_parser("health", help="liveness/readiness report")
    _add_db(p_health)
    p_health.set_defaults(func=cmd_health)
    return parser


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
