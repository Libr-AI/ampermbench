#!/usr/bin/env python3
"""Day-2 smoke run: the two WW families, easiest and hardest prompt each, under a given config.

Mirrors scripts/run_full_bypass.py: loads a config, narrows it with dataclasses.replace, runs, aggregates.
Reads project keys from ~/.config/wishing-willow/env (KEY=VALUE lines) so nothing is exported by hand;
a value of REPLACE_ME is treated as unset. Does not modify src/ or the config files.
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from ampermbench.aggregate import summarize, write_outputs
from ampermbench.config import load_config
from ampermbench.runner import run_benchmark
from ampermbench.utils import json_dumps

SMOKE_TASKS = ["clean-up-artifacts", "clean-up-branches"]
SMOKE_PROMPTS = ["CA-S0-B0-R0", "CA-S3-B3-R1", "CB-S0-B0-R0", "CB-S3-B3-R1"]
DEFAULT_ENV_FILE = Path.home() / ".config" / "wishing-willow" / "env"


def load_env_file(path: Path) -> list[str]:
    if not path.exists():
        return []
    loaded: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if not value or value == "REPLACE_ME" or key in os.environ:
            continue
        os.environ[key] = value
        loaded.append(key)
    return loaded


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Smoke run: two WW families, four prompts, one config.")
    parser.add_argument("--repo-root", default=str(repo_root))
    parser.add_argument("--config", default="config/benchmark.yaml")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--tag", default="", help="Appended to the results directory name.")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true", help="Print the resolved plan without running.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = repo_root / config_path

    loaded = load_env_file(Path(args.env_file).expanduser())
    if args.dry_run:
        for key in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
            os.environ.setdefault(key, "dry-run-placeholder")

    config = load_config(config_path)
    assert config.provider == "claude" and config.claude is not None, "smoke.py covers the claude provider"
    config = replace(
        config,
        benchmark=replace(config.benchmark, tasks=list(SMOKE_TASKS), prompt_ids=list(SMOKE_PROMPTS), repeats=args.repeats),
        docker=replace(config.docker, build_images=False),
    )

    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%Z")
    parts = ["smoke", config_path.stem.replace("benchmark.", "").replace("benchmark", "default")]
    if args.tag:
        parts.append(args.tag)
    parts.append(stamp)
    results_root = repo_root / "results" / "runs" / "-".join(parts)

    plan = {
        "results_root": str(results_root),
        "config": str(config_path.relative_to(repo_root)),
        "keys_loaded_from_env_file": loaded,
        "base_url": config.claude.base_url,
        "model": config.claude.model,
        "permission_modes": config.claude.permission_modes,
        "tasks": config.benchmark.tasks,
        "prompt_ids": config.benchmark.prompt_ids,
        "repeats": config.benchmark.repeats,
        "runs": len(config.benchmark.prompt_ids) * len(config.claude.permission_modes) * config.benchmark.repeats,
    }
    print(json.dumps(plan, indent=2))
    if args.dry_run:
        return 0

    results_root.mkdir(parents=True, exist_ok=True)
    rows = run_benchmark(repo_root, config, results_root)
    metrics = summarize(rows)
    write_outputs(results_root, metrics)
    (results_root / "summary" / "resolved_config.json").write_text(json_dumps(plan), encoding="utf-8")
    print(json.dumps({"results_root": str(results_root), "rows": len(rows)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
