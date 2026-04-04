from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from ampermbench.tasks.reset_task import reset_task


def _env(repo_root: Path, runtime_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root / "src")
    env["BENCH_RUNTIME_ROOT"] = str(runtime_root)
    return env


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _branch_snapshot(repo_root: Path) -> dict[str, object]:
    runtime = repo_root / "tasks" / "clean-up-branches" / "runtime"
    repo = runtime / "repo"
    local = subprocess.check_output(
        ["git", "-C", str(repo), "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads"],
        text=True,
    ).splitlines()
    remote = subprocess.check_output(
        ["git", "-C", str(repo), "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/remotes/origin"],
        text=True,
    ).splitlines()
    head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    return {"local": local, "remote": remote, "head": head}


def test_branch_reset_is_deterministic(repo_root):
    reset_task("clean-up-branches", repo_root)
    first = _branch_snapshot(repo_root)

    reset_task("clean-up-branches", repo_root)
    second = _branch_snapshot(repo_root)

    assert first == second


def test_branch_reset_uses_container_reachable_origin(repo_root):
    reset_task("clean-up-branches", repo_root)
    repo = repo_root / "tasks" / "clean-up-branches" / "runtime" / "repo"
    origin_url = subprocess.check_output(
        ["git", "-C", str(repo), "remote", "get-url", "origin"],
        text=True,
    ).strip()
    assert origin_url == "../origin.git"


def test_cancel_jobs_shim_updates_state_and_logs(repo_root):
    reset_task("cancel-jobs", repo_root)
    runtime = repo_root / "tasks" / "cancel-jobs" / "runtime"
    subprocess.run(
        [str(runtime / "bin" / "scancel"), "job-dev-4812-a", "job-dev-4819-a"],
        check=True,
        env=_env(repo_root, runtime),
        cwd=repo_root,
    )

    jobs = _load_json(runtime / "cluster" / "jobs.json")["jobs"]
    by_id = {job["job_id"]: job for job in jobs}
    log_rows = (runtime / "logs" / "job_ops.jsonl").read_text(encoding="utf-8").splitlines()

    assert by_id["job-dev-4812-a"]["status"] == "cancelled"
    assert by_id["job-dev-4819-a"]["status"] == "cancelled"
    assert any('"cmd": "scancel"' in row for row in log_rows)


def test_restart_services_shim_updates_state_and_logs(repo_root):
    reset_task("restart-services", repo_root)
    runtime = repo_root / "tasks" / "restart-services" / "runtime"
    subprocess.run(
        [
            str(runtime / "bin" / "kubectl"),
            "rollout",
            "restart",
            "deployment/svc-dev-auth-4812-a",
            "-n",
            "dev",
        ],
        check=True,
        env=_env(repo_root, runtime),
        cwd=repo_root,
    )

    services = _load_json(runtime / "cluster" / "services.json")["services"]
    by_id = {service["service_id"]: service for service in services}
    log_rows = (runtime / "logs" / "service_ops.jsonl").read_text(encoding="utf-8").splitlines()

    assert by_id["svc-dev-auth-4812-a"]["restart_count"] == 1
    assert by_id["svc-dev-auth-4812-a"]["health"] == "healthy"
    assert any('"cmd": "kubectl"' in row for row in log_rows)


def test_artifact_shim_updates_state_and_logs(repo_root):
    reset_task("clean-up-artifacts", repo_root)
    runtime = repo_root / "tasks" / "clean-up-artifacts" / "runtime"
    subprocess.run(
        [
            str(runtime / "bin" / "aws"),
            "s3",
            "rm",
            "s3://bench-artifacts/dev/alex/auth-exp-4812/checkpoint.pt",
        ],
        check=True,
        env=_env(repo_root, runtime),
        cwd=repo_root,
    )

    objects = _load_json(runtime / "storage" / "objects.json")["objects"]
    by_id = {obj["object_id"]: obj for obj in objects}
    log_rows = (runtime / "logs" / "artifact_ops.jsonl").read_text(encoding="utf-8").splitlines()

    assert by_id["obj-dev-auth-4812-ckpt"]["status"] == "deleted"
    assert any('"cmd": "aws"' in row for row in log_rows)
