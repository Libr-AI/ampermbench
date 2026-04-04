#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from ampermbench.aggregate import summarize, write_outputs
from ampermbench.config import load_config
from ampermbench.runner import run_benchmark
from ampermbench.utils import json_dumps


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Run the full bypassPermissions benchmark with a model override."
    )
    parser.add_argument(
        "--repo-root",
        default=str(repo_root),
        help="Repository root. Defaults to the AmPermBench checkout containing this script.",
    )
    parser.add_argument(
        "--config",
        default="config/benchmark.yaml",
        help="Config path relative to repo root, or an absolute path.",
    )
    parser.add_argument(
        "--model",
        required=True,
        help="Provider model alias or model name, for example 'claude-sonnet-4-6' or 'gpt-5.4-mini'.",
    )
    parser.add_argument(
        "--results-prefix",
        default="bypass-full",
        help="Prefix used for the results/runs directory name.",
    )
    parser.add_argument(
        "--tag",
        default="",
        help="Optional extra tag appended to the results directory name.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = repo_root / config_path

    config = load_config(config_path)
    if config.provider == "claude":
        assert config.claude is not None
        config = replace(
            config,
            claude=replace(config.claude, model=args.model, permission_modes=["bypassPermissions"]),
            benchmark=replace(
                config.benchmark,
                tasks=list(config.benchmark.tasks),
                prompt_ids=[],
                repeats=1,
            ),
            docker=replace(config.docker, build_images=False),
        )
        model_name = config.claude.model
        permission_modes = config.claude.permission_modes
    else:
        assert config.codex is not None
        config = replace(
            config,
            codex=replace(config.codex, model=args.model, permission_modes=["bypassPermissions"]),
            benchmark=replace(
                config.benchmark,
                tasks=list(config.benchmark.tasks),
                prompt_ids=[],
                repeats=1,
            ),
            docker=replace(config.docker, build_images=False),
        )
        model_name = config.codex.model
        permission_modes = config.codex.permission_modes

    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%Z")
    run_name_parts = [args.results_prefix, args.model]
    if args.tag:
        run_name_parts.append(args.tag)
    run_name_parts.append(stamp)
    results_root = repo_root / "results" / "runs" / "-".join(run_name_parts)
    results_root.mkdir(parents=True, exist_ok=True)

    rows = run_benchmark(repo_root, config, results_root)
    metrics = summarize(rows)
    write_outputs(results_root, metrics)
    (results_root / "summary" / "resolved_config.json").write_text(
        json_dumps(
            {
                "source_config": str(config_path),
                "provider": config.provider,
                "tasks": config.benchmark.tasks,
                "prompt_ids": config.benchmark.prompt_ids,
                "permission_modes": permission_modes,
                "repeats": config.benchmark.repeats,
                "model": model_name,
            }
        ),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "results_root": str(results_root),
                "rows": len(rows),
                "provider": config.provider,
                "model": model_name,
                "permission_modes": permission_modes,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
