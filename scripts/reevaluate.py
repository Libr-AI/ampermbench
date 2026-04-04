#!/usr/bin/env python3
"""Re-evaluate existing benchmark results using updated trace/evaluator logic.

Usage:
    python scripts/reevaluate.py results/runs/20260403T143049Z [--extra-dir results/runs/20260403T153117Z/clean-up-branches]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ampermbench.trace import build_action_trace, extract_session_summary
from ampermbench.evaluators import branches as eval_branches
from ampermbench.evaluators import cancel_jobs as eval_cancel_jobs
from ampermbench.evaluators import cleanup_artifacts as eval_cleanup_artifacts
from ampermbench.evaluators import restart_services as eval_restart_services
from ampermbench.utils import json_dumps

EVALUATORS = {
    "clean-up-branches": eval_branches.evaluate,
    "cancel-jobs": eval_cancel_jobs.evaluate,
    "restart-services": eval_restart_services.evaluate,
    "clean-up-artifacts": eval_cleanup_artifacts.evaluate,
}


def reevaluate_run(run_dir: Path, task: str, provider: str = "claude") -> dict | None:
    result_path = run_dir / "result.json"
    if not result_path.exists():
        return None
    old_result = json.loads(result_path.read_text(encoding="utf-8"))
    before_path = run_dir / "before_state.json"
    after_path = run_dir / "after_state.json"
    stdout_path = run_dir / "claude.stdout.jsonl" if provider == "claude" else run_dir / "codex.stdout.jsonl"
    if not before_path.exists() or not after_path.exists() or not stdout_path.exists():
        return None

    before = json.loads(before_path.read_text(encoding="utf-8"))
    after = json.loads(after_path.read_text(encoding="utf-8"))

    action_log_path = Path(old_result.get("action_log_path", run_dir / "claude-home/.claude/action-log.jsonl"))
    debug_path = Path(old_result.get("debug_path", run_dir / "debug.txt"))

    # Find runtime log
    runtime_log_path = None
    for candidate in ["job_ops.jsonl", "git_ops.jsonl", "service_ops.jsonl", "artifact_ops.jsonl"]:
        p = run_dir / candidate
        if p.exists():
            runtime_log_path = p
            break

    action_trace = build_action_trace(
        provider, task, before, stdout_path, action_log_path, debug_path,
        runtime_log_path, after=after,
    )

    # Write updated trace
    (run_dir / "action_trace.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in action_trace),
        encoding="utf-8",
    )

    prompt_id = old_result["prompt_id"]
    mode = old_result["mode"]
    evaluator = EVALUATORS[task]
    new_eval = evaluator(prompt_id, mode, before, after, action_trace)

    # Merge into old result, preserving non-eval fields
    for key in new_eval:
        old_result[key] = new_eval[key]

    result_path.write_text(json_dumps(old_result), encoding="utf-8")
    return old_result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("results_root", type=Path)
    parser.add_argument("--extra-dir", type=Path, action="append", default=[],
                        help="Extra result directories to include (merged into main)")
    parser.add_argument("--provider", default="claude")
    args = parser.parse_args()

    # Collect all (task, run_dir) pairs
    run_dirs: list[tuple[str, Path]] = []
    for task_dir in sorted(args.results_root.iterdir()):
        if not task_dir.is_dir():
            continue
        task = task_dir.name
        if task not in EVALUATORS:
            continue
        for prompt_dir in sorted(task_dir.iterdir()):
            for mode_dir in sorted(prompt_dir.iterdir()):
                for repeat_dir in sorted(mode_dir.iterdir()):
                    if (repeat_dir / "result.json").exists():
                        run_dirs.append((task, repeat_dir))

    # Add extra dirs
    for extra in args.extra_dir:
        if not extra.is_dir():
            print(f"Warning: {extra} not found, skipping", file=sys.stderr)
            continue
        # Determine task from directory name or parent
        task_name = extra.name
        if task_name not in EVALUATORS:
            # Try to find task from structure
            for sub in sorted(extra.iterdir()):
                if not sub.is_dir():
                    continue
                for mode_dir in sorted(sub.iterdir()):
                    for repeat_dir in sorted(mode_dir.iterdir()):
                        if (repeat_dir / "result.json").exists():
                            run_dirs.append((task_name, repeat_dir))
        else:
            for prompt_dir in sorted(extra.iterdir()):
                if not prompt_dir.is_dir():
                    continue
                for mode_dir in sorted(prompt_dir.iterdir()):
                    for repeat_dir in sorted(mode_dir.iterdir()):
                        if (repeat_dir / "result.json").exists():
                            run_dirs.append((task_name, repeat_dir))

    # Re-evaluate
    import collections
    old_cases = collections.Counter()
    new_cases = collections.Counter()
    changed = 0
    total = 0

    for task, run_dir in run_dirs:
        old = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
        old_case = old["classifier_case"]

        result = reevaluate_run(run_dir, task, args.provider)
        if result is None:
            continue

        total += 1
        old_cases[old_case] += 1
        new_cases[result["classifier_case"]] += 1

        if old_case != result["classifier_case"]:
            changed += 1
            print(f"  {task}/{result['prompt_id']}: {old_case} -> {result['classifier_case']} (gate={result['gate_decision']})")

    print(f"\nTotal: {total}, Changed: {changed}")
    print(f"Old distribution: {dict(sorted(old_cases.items()))}")
    print(f"New distribution: {dict(sorted(new_cases.items()))}")

    # Print new confusion matrix
    tp = new_cases.get("TP", 0)
    tn = new_cases.get("TN", 0)
    fp = new_cases.get("FP", 0)
    fn = new_cases.get("FN", 0)
    decided = tp + tn + fp + fn
    print(f"\nConfusion matrix: TP={tp} TN={tn} FP={fp} FN={fn}")
    print(f"NO_ATTEMPT={new_cases.get('NO_ATTEMPT', 0)} INVALID_TRACE={new_cases.get('INVALID_TRACE', 0)}")
    if decided:
        precision = tp / (tp + fp) if (tp + fp) else float("nan")
        recall = tp / (tp + fn) if (tp + fn) else float("nan")
        fpr = fp / (fp + tn) if (fp + tn) else float("nan")
        print(f"Decision coverage: {decided}/{total} ({decided / total * 100:.1f}%)")
        print(f"Precision={precision:.3f} Recall={recall:.3f} FPR={fpr:.3f}")


if __name__ == "__main__":
    main()
