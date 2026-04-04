#!/usr/bin/env python3
"""Export benchmark runs to structured, readable JSON for paper appendix.

Each prompt produces one JSON file with:
  - metadata (prompt_id, axes, task)
  - prompt text and oracle ground truth
  - readable conversation log
  - all auto-mode actions with gate decisions
  - state diff

Output: results/readable/<prompt_id>.json
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ampermbench.tasks.cancel_jobs import prompts as cj_prompts, oracle as cj_oracle
from ampermbench.tasks.branches import prompts as cb_prompts, oracle as cb_oracle
from ampermbench.tasks.restart_services import prompts as rs_prompts, oracle as rs_oracle
from ampermbench.tasks.artifacts import prompts as ca_prompts, oracle as ca_oracle

TASK_META = {
    "cancel-jobs": {
        "prompts_fn": cj_prompts,
        "oracle_fn": cj_oracle,
        "prefix": "CJ",
        "description": "Cancel stale cluster jobs via scancel CLI",
        "authorized_key": "authorized_cancel_set",
        "target_entity": "jobs",
    },
    "clean-up-branches": {
        "prompts_fn": cb_prompts,
        "oracle_fn": cb_oracle,
        "prefix": "CB",
        "description": "Delete stale git branches (local or remote)",
        "authorized_key": "authorized_delete_set",
        "target_entity": "branches",
    },
    "restart-services": {
        "prompts_fn": rs_prompts,
        "oracle_fn": rs_oracle,
        "prefix": "RS",
        "description": "Restart unhealthy Kubernetes deployments via kubectl",
        "authorized_key": "authorized_restart_set",
        "target_entity": "services",
    },
    "clean-up-artifacts": {
        "prompts_fn": ca_prompts,
        "oracle_fn": ca_oracle,
        "prefix": "CA",
        "description": "Delete stale S3 artifacts via aws CLI",
        "authorized_key": "authorized_delete_set",
        "target_entity": "artifacts",
    },
}


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def extract_conversation(stream_rows: list[dict]) -> list[dict]:
    """Extract a clean, readable conversation from Claude stream JSON."""
    conversation = []
    for row in stream_rows:
        rtype = row.get("type")

        if rtype == "system" and row.get("subtype") == "init":
            conversation.append({
                "role": "system",
                "event": "session_init",
                "permission_mode": row.get("permissionMode"),
                "model": row.get("model"),
            })

        elif rtype == "system" and row.get("subtype") == "status":
            pm = row.get("permissionMode")
            if pm:
                conversation.append({
                    "role": "system",
                    "event": "permission_mode_update",
                    "permission_mode": pm,
                })

        elif rtype == "assistant":
            msg = row.get("message", {})
            content_blocks = msg.get("content", [])
            for block in content_blocks:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    conversation.append({
                        "role": "assistant",
                        "type": "text",
                        "content": block["text"],
                    })
                elif block.get("type") == "tool_use":
                    entry = {
                        "role": "assistant",
                        "type": "tool_use",
                        "tool": block.get("name"),
                        "tool_use_id": block.get("id"),
                    }
                    inp = block.get("input", {})
                    if block.get("name") == "Bash":
                        entry["command"] = inp.get("command", "")
                        entry["description"] = inp.get("description", "")
                    elif block.get("name") == "Read":
                        entry["file_path"] = inp.get("file_path", "")
                    elif block.get("name") == "Edit":
                        entry["file_path"] = inp.get("file_path", "")
                        entry["old_string"] = inp.get("old_string", "")[:200]
                        entry["new_string"] = inp.get("new_string", "")[:200]
                    else:
                        entry["input_summary"] = str(inp)[:300]
                    conversation.append(entry)

        elif rtype == "user":
            content_blocks = row.get("message", {}).get("content", [])
            for block in content_blocks:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    text = str(block.get("content", ""))
                    is_denial = (
                        "permission" in text.lower() and "denied" in text.lower()
                    ) or "blocked" in text.lower()
                    conversation.append({
                        "role": "tool_result",
                        "tool_use_id": block.get("tool_use_id"),
                        "content": text[:2000],
                        "is_denial": is_denial,
                    })

        elif rtype == "result":
            conversation.append({
                "role": "system",
                "event": "session_end",
                "stop_reason": row.get("stop_reason"),
                "num_turns": row.get("num_turns"),
                "total_cost_usd": row.get("total_cost_usd", 0),
                "permission_denials_count": len(row.get("permission_denials", []) or []),
            })

    return conversation


def extract_auto_mode_actions(conversation: list[dict], permission_denials: list[dict]) -> list[dict]:
    """Extract all auto-mode gate decisions from the conversation.

    An 'action' is any tool_use that could be state-changing, paired with
    its tool_result (which tells us if it was allowed or blocked).
    """
    # Build denial lookup by tool_use_id
    formal_denial_ids = set()
    for d in (permission_denials or []):
        tid = d.get("tool_use_id")
        if tid:
            formal_denial_ids.add(tid)

    # Pair tool_use with tool_result
    tool_uses = {}
    actions = []
    action_id = 0

    for entry in conversation:
        if entry.get("type") == "tool_use":
            tool_uses[entry.get("tool_use_id")] = entry
        elif entry.get("role") == "tool_result":
            tuid = entry.get("tool_use_id")
            tool_use = tool_uses.get(tuid, {})
            if not tool_use:
                continue

            # Determine gate decision
            is_denial = entry.get("is_denial", False)
            is_formal_denial = tuid in formal_denial_ids

            if is_formal_denial or is_denial:
                gate = "blocked"
            else:
                gate = "allowed"

            action_id += 1
            action = {
                "action_id": action_id,
                "tool": tool_use.get("tool"),
                "tool_use_id": tuid,
                "gate_decision": gate,
            }
            if tool_use.get("tool") == "Bash":
                action["command"] = tool_use.get("command", "")
                action["description"] = tool_use.get("description", "")
            elif tool_use.get("tool") == "Edit":
                action["file_path"] = tool_use.get("file_path", "")
            elif tool_use.get("tool") == "Read":
                action["file_path"] = tool_use.get("file_path", "")

            if gate == "blocked":
                action["denial_text"] = entry.get("content", "")[:500]

            actions.append(action)

    return actions


def compute_state_diff(task: str, before: dict, after: dict) -> dict:
    """Compute human-readable state diff."""
    diff = {"changes": []}
    if task == "clean-up-branches":
        dl = sorted(set(before.get("local", [])) - set(after.get("local", [])))
        dr = sorted(set(before.get("remote", [])) - set(after.get("remote", [])))
        if dl:
            diff["changes"].append({"type": "branch_deleted_local", "targets": dl})
        if dr:
            diff["changes"].append({"type": "branch_deleted_remote", "targets": dr})
    elif task == "cancel-jobs":
        bmap = {j["job_id"]: j for j in before.get("jobs", [])}
        amap = {j["job_id"]: j for j in after.get("jobs", [])}
        cancelled = [jid for jid, j in amap.items()
                     if bmap.get(jid, {}).get("status") != "cancelled" and j["status"] == "cancelled"]
        if cancelled:
            diff["changes"].append({"type": "jobs_cancelled", "targets": sorted(cancelled)})
    elif task == "restart-services":
        bmap = {s["service_id"]: s for s in before.get("services", [])}
        amap = {s["service_id"]: s for s in after.get("services", [])}
        restarted = [sid for sid, s in amap.items()
                     if s["restart_count"] > bmap.get(sid, {}).get("restart_count", 0)]
        if restarted:
            diff["changes"].append({"type": "services_restarted", "targets": sorted(restarted)})
    elif task == "clean-up-artifacts":
        bmap = {o["object_id"]: o for o in before.get("objects", [])}
        amap = {o["object_id"]: o for o in after.get("objects", [])}
        deleted = [oid for oid, o in amap.items()
                   if bmap.get(oid, {}).get("status") == "present" and o["status"] == "deleted"]
        if deleted:
            diff["changes"].append({"type": "artifacts_deleted", "targets": sorted(deleted)})
    return diff


def export_one(task: str, run_dir: Path, prompt_rec: dict, oracle_rec: dict, task_info: dict) -> dict:
    """Export one run to structured readable format."""
    result = json.loads((run_dir / "result.json").read_text())
    before = json.loads((run_dir / "before_state.json").read_text())
    after = json.loads((run_dir / "after_state.json").read_text())
    stdout_path = run_dir / "claude.stdout.jsonl"
    stream_rows = _read_jsonl(stdout_path)

    conversation = extract_conversation(stream_rows)
    permission_denials = result.get("permission_denials", [])
    auto_actions = extract_auto_mode_actions(conversation, permission_denials)
    state_diff = compute_state_diff(task, before, after)

    pid = prompt_rec["prompt_id"]
    s_idx = int(prompt_rec["s_axis"][1:])
    b_idx = int(prompt_rec["b_axis"][1:])
    r_idx = int(prompt_rec["r_axis"][1:])

    return {
        "prompt_id": pid,
        "task": task,
        "task_description": task_info["description"],
        "axes": {
            "S": s_idx,
            "B": b_idx,
            "R": r_idx,
            "S_label": prompt_rec["s_axis"],
            "B_label": prompt_rec["b_axis"],
            "R_label": prompt_rec["r_axis"],
        },
        "prompt_text": prompt_rec["prompt"],
        "oracle": oracle_rec,
        "session_meta": {
            "model": "claude-sonnet-4-6",
            "permission_mode": result.get("reported_permission_mode", "auto"),
            "timed_out": result.get("timed_out", False),
            "total_cost_usd": result.get("total_cost_usd", 0),
            "total_tokens": result.get("total_token_usage", 0),
            "num_turns": result.get("loop_num_turns"),
            "wall_clock_ms": result.get("wall_clock_ms"),
        },
        "conversation": conversation,
        "auto_mode_actions": auto_actions,
        "state_diff": state_diff,
        "outcome": {
            "safe_success": result.get("safe_success", 0),
            "under_completion": result.get("under_completion", 0),
            "wrong_target": result.get("wrong_target", 0),
            "over_scope": result.get("over_scope", 0),
            "unsafe_allow": result.get("unsafe_allow", 0),
            "protected_touch": result.get("protected_touch", 0),
        },
        # Placeholder for subagent judgments — filled later
        "action_judgments": [],
    }


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("results_root", type=Path)
    parser.add_argument("--extra-dir", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, default=Path("results/readable"))
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)

    # Build prompt/oracle lookups
    prompts_map = {}
    oracles_map = {}
    for task, info in TASK_META.items():
        for p in info["prompts_fn"]():
            prompts_map[p["prompt_id"]] = (task, p)
        oracles_map[task] = info["oracle_fn"]()

    # Collect all run directories
    runs = {}  # prompt_id -> (task, run_dir)
    for source in [args.results_root] + args.extra_dir:
        if not source.is_dir():
            continue
        # Check if source is a task-level dir or root
        subdirs = [d for d in source.iterdir() if d.is_dir()]
        task_dirs = []
        for d in subdirs:
            if d.name in TASK_META:
                task_dirs.append((d.name, d))
            else:
                # Might be prompt-level under a task dir
                # Check parent name
                if source.name in TASK_META:
                    task_dirs.append((source.name, source))
                    break

        for task_name, task_dir in task_dirs:
            for prompt_dir in sorted(task_dir.iterdir()):
                if not prompt_dir.is_dir():
                    continue
                for mode_dir in sorted(prompt_dir.iterdir()):
                    for repeat_dir in sorted(mode_dir.iterdir()):
                        rp = repeat_dir / "result.json"
                        if rp.exists():
                            pid = prompt_dir.name
                            if pid not in runs:  # don't overwrite main with extra
                                runs[pid] = (task_name, repeat_dir)

    print(f"Found {len(runs)} runs")

    exported = []
    for pid in sorted(runs):
        task, run_dir = runs[pid]
        if pid not in prompts_map:
            print(f"  SKIP {pid}: no prompt record")
            continue
        _, prompt_rec = prompts_map[pid]
        oracle_rec = oracles_map[task].get(pid, {})
        info = TASK_META[task]

        doc = export_one(task, run_dir, prompt_rec, oracle_rec, info)
        out_path = args.output / f"{pid}.json"
        out_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
        exported.append(pid)

    print(f"Exported {len(exported)} files to {args.output}")

    # Write index
    index = {
        "total": len(exported),
        "by_task": {},
        "prompt_ids": sorted(exported),
    }
    for pid in exported:
        task = runs[pid][0]
        index["by_task"].setdefault(task, []).append(pid)
    (args.output / "_index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    print("Index written to _index.json")


if __name__ == "__main__":
    main()
