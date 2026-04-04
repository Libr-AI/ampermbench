from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bench_root() -> Path:
    return Path(os.environ.get("BENCH_RUNTIME_ROOT", "/bench")).resolve()


def _log(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _exit(message: str, code: int = 1) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def _dispatch_git(argv: list[str]) -> None:
    log_path = _bench_root() / "logs" / "git_ops.jsonl"
    _log(log_path, {"ts": _now(), "cmd": "git", "args": argv})
    os.execv("/usr/bin/git", ["/usr/bin/git", *argv])


def _dispatch_cancel_jobs(command: str, argv: list[str]) -> None:
    jobs_path = _bench_root() / "cluster" / "jobs.json"
    log_path = _bench_root() / "logs" / "job_ops.jsonl"
    state = _load_json(jobs_path)
    jobs = state["jobs"]
    _log(log_path, {"ts": _now(), "cmd": command, "args": argv})
    if command == "squeue":
        user = None
        queue = None
        i = 0
        while i < len(argv):
            if argv[i] == "-u":
                user = argv[i + 1]
                i += 2
            elif argv[i] == "-p":
                queue = argv[i + 1]
                i += 2
            else:
                _exit(f"Unsupported squeue arg: {argv[i]}")
        rows = []
        for job in jobs:
            if job["status"] == "cancelled":
                continue
            if user and job["owner"] != user:
                continue
            if queue and job["queue"] != queue:
                continue
            rows.append(job)
        print("JOBID PARTITION USER STATE NAME")
        for job in rows:
            print(f'{job["job_id"]} {job["queue"]} {job["owner"]} {job["status"]} {job["name"]}')
        return
    if command == "scontrol":
        if argv[:2] != ["show", "job"] or len(argv) != 3:
            _exit("Usage: scontrol show job <job-id>")
        job_id = argv[2]
        job = next((item for item in jobs if item["job_id"] == job_id), None)
        if not job:
            _exit(f"Unknown job: {job_id}", 2)
        print(
            " ".join(
                [
                    f'JobId={job["job_id"]}',
                    f'JobName={job["name"]}',
                    f'UserId={job["owner"]}',
                    f'Partition={job["queue"]}',
                    f'JobState={job["status"]}',
                    f'Stale={"true" if job["stale"] else "false"}',
                    f'Protected={"true" if job["protected"] else "false"}',
                ]
            )
        )
        return
    if command == "scancel":
        if not argv:
            _exit("Usage: scancel <job-id>...", 2)
        updated = False
        for job_id in argv:
            job = next((item for item in jobs if item["job_id"] == job_id), None)
            if not job:
                _exit(f"Unknown job: {job_id}", 2)
            if job["status"] in {"running", "queued"}:
                job["status"] = "cancelled"
                updated = True
        if updated:
            _write_json(jobs_path, state)
        return
    _exit(f"Unsupported command: {command}", 2)


def _parse_labels(selector: str | None) -> dict[str, str]:
    if not selector:
        return {}
    labels = {}
    for chunk in selector.split(","):
        if "=" not in chunk:
            _exit(f"Unsupported label selector: {selector}")
        key, value = chunk.split("=", 1)
        labels[key] = value
    return labels


def _dispatch_restart_services(argv: list[str]) -> None:
    services_path = _bench_root() / "cluster" / "services.json"
    log_path = _bench_root() / "logs" / "service_ops.jsonl"
    state = _load_json(services_path)
    services = state["services"]
    _log(log_path, {"ts": _now(), "cmd": "kubectl", "args": argv})
    if argv[:2] in (["get", "deployment"], ["get", "deployments"]):
        namespace = None
        selector = None
        i = 2
        while i < len(argv):
            if argv[i] == "-n":
                namespace = argv[i + 1]
                i += 2
            elif argv[i] == "-l":
                selector = argv[i + 1]
                i += 2
            else:
                _exit(f"Unsupported kubectl arg: {argv[i]}")
        labels = _parse_labels(selector)
        print("NAME READY STATUS OWNER COMPONENT")
        for service in services:
            if namespace and service["env"] != namespace:
                continue
            if labels:
                checks = {
                    "owner": service["owner"],
                    "component": service["component"],
                    "health": service["health"],
                }
                if any(checks.get(key) != value for key, value in labels.items()):
                    continue
            ready = "1/1" if service["health"] == "healthy" else "0/1"
            print(f'{service["service_id"]} {ready} {service["health"]} {service["owner"]} {service["component"]}')
        return
    if argv[:2] == ["describe", "deployment"]:
        if len(argv) != 5 or argv[3] != "-n":
            _exit("Usage: kubectl describe deployment <service-id> -n <env>", 2)
        service_id = argv[2]
        namespace = argv[4]
        service = next((item for item in services if item["service_id"] == service_id and item["env"] == namespace), None)
        if not service:
            _exit(f"Unknown deployment: {service_id}", 2)
        print("\n".join(
            [
                f'Name: {service["service_id"]}',
                f'Namespace: {service["env"]}',
                f'Owner: {service["owner"]}',
                f'Component: {service["component"]}',
                f'Health: {service["health"]}',
                f'Protected: {"true" if service["protected"] else "false"}',
                f'RestartCount: {service["restart_count"]}',
            ]
        ))
        return
    if argv[:2] == ["rollout", "restart"]:
        if "-n" not in argv:
            _exit("kubectl rollout restart requires -n", 2)
        idx = argv.index("-n")
        targets = argv[2:idx]
        namespace = argv[idx + 1]
        if not targets:
            _exit("No deployments supplied", 2)
        if targets[0] in {"deployment", "deployments"}:
            targets = targets[1:]
        for target in targets:
            if target.startswith("deployment/"):
                service_id = target.split("/", 1)[1]
            else:
                service_id = target
            service = next((item for item in services if item["service_id"] == service_id and item["env"] == namespace), None)
            if not service:
                _exit(f"Unknown deployment: {service_id}", 2)
            service["restart_count"] += 1
            service["health"] = "healthy"
        _write_json(services_path, state)
        return
    _exit("Unsupported kubectl invocation", 2)


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("s3://"):
        _exit(f"Unsupported S3 URI: {uri}", 2)
    bucket_and_key = uri[5:]
    parts = bucket_and_key.split("/", 1)
    bucket = parts[0]
    key = parts[1] if len(parts) > 1 else ""
    return bucket, key


def _dispatch_artifacts(argv: list[str]) -> None:
    objects_path = _bench_root() / "storage" / "objects.json"
    log_path = _bench_root() / "logs" / "artifact_ops.jsonl"
    state = _load_json(objects_path)
    objects = state["objects"]
    _log(log_path, {"ts": _now(), "cmd": "aws", "args": argv})
    if argv[:2] == ["s3", "ls"]:
        if len(argv) < 3:
            _exit("Usage: aws s3 ls <uri>", 2)
        bucket, prefix = _parse_s3_uri(argv[2])
        recursive = "--recursive" in argv[3:]
        if bucket != "bench-artifacts":
            _exit(f"Unsupported bucket: {bucket}", 2)
        print("LastModified Size Key")
        for obj in objects:
            if obj["status"] != "present":
                continue
            if recursive:
                if not obj["key"].startswith(prefix):
                    continue
            elif not obj["key"].startswith(prefix):
                continue
            print(f'2026-03-28T00:00:00Z 1024 {obj["key"]}')
        return
    if argv[:2] == ["s3api", "head-object"]:
        bucket = None
        key = None
        i = 2
        while i < len(argv):
            if argv[i] == "--bucket":
                bucket = argv[i + 1]
                i += 2
            elif argv[i] == "--key":
                key = argv[i + 1]
                i += 2
            else:
                _exit(f"Unsupported aws arg: {argv[i]}", 2)
        if bucket != "bench-artifacts" or not key:
            _exit("Usage: aws s3api head-object --bucket bench-artifacts --key <key>", 2)
        obj = next((item for item in objects if item["key"] == key), None)
        if not obj:
            _exit(f"Unknown object key: {key}", 2)
        print(json.dumps(
            {
                "ObjectId": obj["object_id"],
                "Key": obj["key"],
                "Owner": obj["owner"],
                "Namespace": obj["ns"],
                "Stale": obj["stale"],
                "Protected": obj["protected"],
                "ImpactLevel": obj["impact_level"],
            },
            indent=2,
            sort_keys=True,
        ))
        return
    if argv[:2] == ["s3", "rm"]:
        if len(argv) < 3:
            _exit("Usage: aws s3 rm <uri> [--recursive]", 2)
        bucket, key = _parse_s3_uri(argv[2])
        recursive = "--recursive" in argv[3:]
        if bucket != "bench-artifacts":
            _exit(f"Unsupported bucket: {bucket}", 2)
        matched = False
        for obj in objects:
            if recursive:
                if obj["key"].startswith(key):
                    obj["status"] = "deleted"
                    matched = True
            else:
                if obj["key"] == key:
                    obj["status"] = "deleted"
                    matched = True
        if not matched:
            _exit(f"No objects matched: {key}", 2)
        _write_json(objects_path, state)
        return
    _exit("Unsupported aws invocation", 2)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("task")
    parser.add_argument("command")
    parser.add_argument("args", nargs=argparse.REMAINDER)
    ns = parser.parse_args()
    argv = ns.args
    if ns.task == "clean-up-branches" and ns.command == "git":
        _dispatch_git(argv)
    elif ns.task == "cancel-jobs":
        _dispatch_cancel_jobs(ns.command, argv)
    elif ns.task == "restart-services" and ns.command == "kubectl":
        _dispatch_restart_services(argv)
    elif ns.task == "clean-up-artifacts" and ns.command == "aws":
        _dispatch_artifacts(argv)
    else:
        _exit(f"Unsupported task/command: {ns.task} {ns.command}", 2)


if __name__ == "__main__":
    main()
