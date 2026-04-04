from __future__ import annotations

import argparse
from pathlib import Path

from .tasks import TASK_SPECS
from .utils import repo_root_from, write_json, write_text


WRAPPER_TEMPLATE = """#!/usr/bin/env bash
set -euo pipefail
exec python3 -m ampermbench.tasks.dispatch "{task}" "{command}" "$@"
"""


RESET_TEMPLATE = """#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${{BASH_SOURCE[0]}}")/../../.." && pwd)
export PYTHONPATH="$ROOT/src${{PYTHONPATH:+:$PYTHONPATH}}"
exec python3 -m ampermbench.tasks.reset_task "{task}" "$ROOT"
"""


def materialize_task(repo_root: Path, task: str) -> None:
    spec = TASK_SPECS[task]
    harness_dir = spec.harness_dir(repo_root)
    runtime_dir = spec.runtime_dir(repo_root)

    write_json(harness_dir / "prompts" / spec.prompt_file, spec.prompts_factory())
    write_json(harness_dir / "meta" / spec.oracle_file, spec.oracle_factory())
    write_json(harness_dir / "meta" / spec.state_file, spec.initial_state_factory())
    write_json(harness_dir / "meta" / "user_context.json", {"user": "alex"})
    write_text(harness_dir / "scripts" / "reset_env.sh", RESET_TEMPLATE.format(task=task), executable=True)

    (runtime_dir / "bin").mkdir(parents=True, exist_ok=True)
    (runtime_dir / "logs").mkdir(parents=True, exist_ok=True)
    if task == "cancel-jobs":
        (runtime_dir / "cluster").mkdir(parents=True, exist_ok=True)
    elif task == "restart-services":
        (runtime_dir / "cluster").mkdir(parents=True, exist_ok=True)
    elif task == "clean-up-artifacts":
        (runtime_dir / "storage").mkdir(parents=True, exist_ok=True)
    elif task == "clean-up-branches":
        (runtime_dir / "repo").mkdir(parents=True, exist_ok=True)
        (runtime_dir / "origin.git").mkdir(parents=True, exist_ok=True)

    for command in spec.shim_wrapper_names:
        write_text(runtime_dir / "bin" / command, WRAPPER_TEMPLATE.format(task=task, command=command), executable=True)


def materialize(repo_root: Path, tasks: list[str] | None = None) -> None:
    selected = tasks or list(TASK_SPECS)
    for task in selected:
        materialize_task(repo_root, task)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=None)
    parser.add_argument("--task", action="append", dest="tasks")
    args = parser.parse_args()
    repo_root = repo_root_from(args.repo_root)
    materialize(repo_root, args.tasks)


if __name__ == "__main__":
    main()
