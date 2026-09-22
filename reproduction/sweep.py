#!/usr/bin/env python3
"""Full sweep under a given config, with a redacted plan and a manifest.

Why not `ampermbench-run`: its main() writes dataclasses.asdict(config) to summary/resolved_config.json,
which includes the resolved API key. This wrapper runs the same run_benchmark() and writes a plan without
secrets plus a manifest (fork commit, image id, timestamps). Keys come from ~/.config/wishing-willow/env.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from ampermbench.aggregate import summarize, write_outputs
from ampermbench.config import load_config
from ampermbench.runner import _base_image_tag, run_benchmark
from ampermbench.utils import json_dumps

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


def _sh(cmd: list[str]) -> str:
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser(description="Full AmPermBench sweep under one config; plan and manifest without secrets.")
    p.add_argument("--repo-root", default=str(repo_root))
    p.add_argument("--config", default="config/benchmark.yaml")
    p.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    p.add_argument("--tag", default="")
    p.add_argument("--tasks", default="", help="Comma-separated subset; default: all tasks in the config.")
    p.add_argument("--prompt-ids", default="", help="Comma-separated subset; default: every prompt.")
    p.add_argument("--repeats", type=int, default=0, help="Override repeats; default: config value.")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


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
    assert config.provider == "claude" and config.claude is not None
    bench = config.benchmark
    if args.tasks:
        bench = replace(bench, tasks=[t.strip() for t in args.tasks.split(",") if t.strip()])
    if args.prompt_ids:
        bench = replace(bench, prompt_ids=[t.strip() for t in args.prompt_ids.split(",") if t.strip()])
    if args.repeats:
        bench = replace(bench, repeats=args.repeats)
    config = replace(config, benchmark=bench, docker=replace(config.docker, build_images=False))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    parts = ["sweep", config_path.stem.replace("benchmark.", "").replace("benchmark", "default")]
    if args.tag:
        parts.append(args.tag)
    parts.append(stamp)
    results_root = repo_root / "results" / "runs" / "-".join(parts)

    n_prompts = len(bench.prompt_ids) if bench.prompt_ids else 32 * len(bench.tasks)
    plan = {
        "results_root": str(results_root),
        "config": str(config_path.relative_to(repo_root)),
        "keys_loaded_from_env_file": loaded,
        "base_url": config.claude.base_url,
        "model": config.claude.model,
        "permission_modes": config.claude.permission_modes,
        "tasks": bench.tasks,
        "prompt_ids": bench.prompt_ids or "all",
        "repeats": bench.repeats,
        "runs": n_prompts * len(config.claude.permission_modes) * bench.repeats,
    }
    manifest = {
        "fork_commit": _sh(["git", "-C", str(repo_root), "rev-parse", "HEAD"]),
        "fork_branch": _sh(["git", "-C", str(repo_root), "rev-parse", "--abbrev-ref", "HEAD"]),
        "base_image": _base_image_tag(config),
        "base_image_id": _sh(["docker", "image", "inspect", "--format", "{{.Id}}", _base_image_tag(config)]),
        "claude_version_pinned": config.claude.version,
        "host": _sh(["hostname"]),
        "started_utc": datetime.now(timezone.utc).isoformat(),
    }
    print(json.dumps({"plan": plan, "manifest": manifest}, indent=2), flush=True)
    if args.dry_run:
        return 0

    results_root.mkdir(parents=True, exist_ok=True)
    (results_root / "summary").mkdir(exist_ok=True)
    (results_root / "summary" / "manifest.json").write_text(json_dumps(manifest), encoding="utf-8")
    rows = run_benchmark(repo_root, config, results_root)
    metrics = summarize(rows)
    write_outputs(results_root, metrics)
    manifest["finished_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["rows"] = len(rows)
    (results_root / "summary" / "manifest.json").write_text(json_dumps(manifest), encoding="utf-8")
    (results_root / "summary" / "resolved_config.json").write_text(json_dumps(plan), encoding="utf-8")
    print(json.dumps({"results_root": str(results_root), "rows": len(rows), "finished_utc": manifest["finished_utc"]}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
