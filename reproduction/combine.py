#!/usr/bin/env python3
"""Combine several result roots into one aggregate.

Use case: a sweep that lost runs to an API-side failure (e.g. credits exhausted) is completed by a
rerun of the failed prompts into a second root. This script takes the latest non-failed row per
(task, prompt_id, mode, repeat) across the given roots, excludes rows whose Claude result line has
is_error=true, and writes summary/aggregate.{json,csv} plus a manifest naming every source run.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from ampermbench.aggregate import summarize, write_outputs
from ampermbench.utils import json_dumps


def run_failed(result_json: Path) -> tuple[bool, str]:
    stream = result_json.parent / "claude.stdout.jsonl"
    if not stream.exists():
        return True, "no stream"
    for line in stream.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("type") == "result":
            return bool(d.get("is_error")), str(d.get("result") or "")[:80]
    return True, "no result line"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("roots", nargs="+", help="Result roots in priority order: later roots override earlier ones.")
    p.add_argument("--out", required=True, help="Combined output root (created).")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    chosen: dict[tuple, dict] = {}
    sources: dict[tuple, str] = {}
    excluded: list[dict] = []
    for root in args.roots:
        for f in sorted(glob.glob(os.path.join(root, "*", "*", "*", "repeat-*", "result.json"))):
            row = json.loads(Path(f).read_text(encoding="utf-8"))
            key = (row.get("task") or f.split(os.sep)[-5], row["prompt_id"], row["mode"], f.split(os.sep)[-2])
            failed, why = run_failed(Path(f))
            if failed:
                excluded.append({"source": f, "why": why})
                continue
            chosen[key] = row
            sources[key] = f
    rows = list(chosen.values())
    report = {
        "roots": args.roots,
        "rows": len(rows),
        "excluded_failed": len(excluded),
        "excluded_examples": excluded[:5],
        "by_task": {t: sum(1 for k in chosen if k[0] == t) for t in sorted({k[0] for k in chosen})},
        "combined_utc": datetime.now(timezone.utc).isoformat(),
    }
    print(json.dumps(report, indent=2))
    if args.dry_run:
        return 0
    out = Path(args.out)
    (out / "summary").mkdir(parents=True, exist_ok=True)
    write_outputs(out, summarize(rows))
    (out / "summary" / "combine-manifest.json").write_text(
        json_dumps({**report, "sources": {"/".join(k): v for k, v in sources.items()}}), encoding="utf-8"
    )
    print(json.dumps({"out": str(out), "rows": len(rows)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
