from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from .aggregate import summarize, write_outputs
from .config import Config, load_config
from .evaluators import branches as eval_branches
from .evaluators import cancel_jobs as eval_cancel_jobs
from .evaluators import cleanup_artifacts as eval_cleanup_artifacts
from .evaluators import restart_services as eval_restart_services
from .materialize_assets import materialize
from .proxy_bridge import proxy_bridge_stack
from .tasks import TASK_SPECS
from .trace import build_action_trace, extract_session_summary
from .utils import json_dumps, repo_root_from


@dataclass(frozen=True)
class RunContext:
    task: str
    prompt_id: str
    mode: str
    repeat: int
    session_id: str
    run_dir: Path


AUTO_MODE_FALLBACK_PATTERNS = (
    "auto mode circuit breaker active",
    "falling back to default",
    "verifyautomodegateaccess: enabledstate=disabled",
    "canenterauto=false",
    "kickoutofautoifneeded applying: ctx.mode=default",
    "auto mode disabled:",
)
RUN_TIMEOUT_SECONDS = 600


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _provider(config: Config) -> str:
    return config.provider


def _provider_version(config: Config) -> str:
    if config.provider == "claude":
        assert config.claude is not None
        return config.claude.version
    assert config.codex is not None
    return config.codex.version


def _provider_modes(config: Config) -> list[str]:
    if config.provider == "claude":
        assert config.claude is not None
        return config.claude.permission_modes
    assert config.codex is not None
    return config.codex.permission_modes


def _image_tag(task: str, config: Config) -> str:
    return f"ampermbench-{_provider(config)}-{task}:{_provider_version(config)}"


def _base_image_tag(config: Config) -> str:
    return f"ampermbench-{_provider(config)}-base:{_provider_version(config)}"


def _base_dockerfile(config: Config, repo_root: Path) -> Path:
    if config.provider == "claude":
        return repo_root / "docker" / "base" / "Dockerfile"
    return repo_root / "docker" / "codex-base" / "Dockerfile"


def _provider_home_dirname(config: Config) -> str:
    return "claude-home" if config.provider == "claude" else "codex-home"


def _docker_image_exists(image: str) -> bool:
    result = subprocess.run(
        ["docker", "image", "inspect", image],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def _auto_mode_fallback_lines(debug_path: Path) -> list[str]:
    if not debug_path.exists():
        return []
    lines = debug_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    matches = []
    for line in lines:
        lowered = line.lower()
        if any(pattern in lowered for pattern in AUTO_MODE_FALLBACK_PATTERNS):
            matches.append(line.strip())
    return matches


def _stdout_has_completion_marker(provider: str, stdout_path: Path) -> bool:
    if not stdout_path.exists():
        return False
    for line in stdout_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if provider == "claude" and payload.get("type") == "result":
            return True
        if provider == "codex" and payload.get("type") == "turn.completed":
            return True
    return False


def _read_container_id(cidfile: Path) -> str | None:
    if not cidfile.exists():
        return None
    container_id = cidfile.read_text(encoding="utf-8", errors="ignore").strip()
    return container_id or None


def _container_host_pid(container_id: str) -> int | None:
    result = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Pid}}", container_id],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return int(value) if value.isdigit() else None


def _force_stop_container(container_id: str | None) -> None:
    if not container_id:
        return
    for command in (["docker", "kill", container_id], ["docker", "rm", "-f", container_id]):
        subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(3):
        pid = _container_host_pid(container_id)
        if pid and pid > 1:
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass
        subprocess.run(["docker", "rm", "-f", container_id], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if subprocess.run(
            ["docker", "inspect", container_id],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode != 0:
            return
        time.sleep(1)


def _build_images(repo_root: Path, config: Config) -> None:
    base_tag = _base_image_tag(config)
    if config.provider == "claude":
        build_args = [
            "--build-arg",
            f"CLAUDE_VERSION={_provider_version(config)}",
        ]
    else:
        build_args = [
            "--build-arg",
            f"CODEX_VERSION={_provider_version(config)}",
        ]
    build_args.extend(_docker_build_proxy_args(config))
    subprocess.run(
        [
            "docker",
            "build",
            *_docker_build_network_args(config),
            "-f",
            str(_base_dockerfile(config, repo_root)),
            "-t",
            base_tag,
            *build_args,
            str(repo_root),
        ],
        check=True,
    )
    for task in config.benchmark.tasks:
        subprocess.run(
            [
                "docker",
                "build",
                *_docker_build_network_args(config),
                "-f",
                str(repo_root / "tasks" / task / "Dockerfile"),
                "-t",
                _image_tag(task, config),
                "--build-arg",
                f"BASE_IMAGE={base_tag}",
                *_docker_build_proxy_args(config),
                str(repo_root),
            ],
            check=True,
        )


def _ensure_images_available(config: Config) -> None:
    missing = []
    base_image = _base_image_tag(config)
    if not _docker_image_exists(base_image):
        missing.append(base_image)
    for task in config.benchmark.tasks:
        image = _image_tag(task, config)
        if not _docker_image_exists(image):
            missing.append(image)
    if missing:
        raise FileNotFoundError(
            "Required Docker images are missing while docker.build_images=false: "
            + ", ".join(missing)
            + ". Build them first or set docker.build_images=true."
        )


def _resolve_ips(base_url: str) -> list[str]:
    host = urlparse(base_url).hostname
    if not host:
        return []
    ips = sorted({entry[4][0] for entry in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
    return ips


def _copy_tree_if_exists(src: Path, dst: Path) -> None:
    if src.exists():
        shutil.copytree(src, dst, dirs_exist_ok=True)


def _resolve_allowed_ips(config: Config) -> list[str]:
    if not config.docker.restrict_egress:
        return []
    if config.docker.proxy.enabled:
        return []
    if config.provider == "claude":
        assert config.claude is not None
        hosts = {urlparse(config.claude.base_url).hostname or ""}
        if config.claude.auth_mode == "host_login":
            hosts.add("claude.ai")
    else:
        assert config.codex is not None
        hosts = {"api.openai.com"}
        if config.codex.auth_mode == "host_login":
            hosts.update({"openai.com", "chatgpt.com", "auth.openai.com"})
    ips: set[str] = set()
    for host in sorted(host for host in hosts if host):
        ips.update(entry[4][0] for entry in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))
    return sorted(ips)


def _proxy_env_map(config: Config) -> dict[str, str]:
    if not config.docker.proxy.enabled:
        return {}
    env_map = {}
    pairs = {
        "HTTP_PROXY": config.docker.proxy.http,
        "HTTPS_PROXY": config.docker.proxy.https,
        "ALL_PROXY": config.docker.proxy.all,
        "NO_PROXY": config.docker.proxy.no_proxy,
    }
    for key, value in pairs.items():
        if value:
            env_map[key] = value
            env_map[key.lower()] = value
    return env_map


def _rewrite_proxy_url_for_build(proxy_url: str, config: Config) -> str:
    if not proxy_url:
        return proxy_url
    parsed = urlparse(proxy_url)
    if parsed.hostname != "host.docker.internal" or not config.docker.proxy.add_host_gateway:
        return proxy_url
    netloc = "127.0.0.1"
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    if parsed.username:
        auth = parsed.username
        if parsed.password:
            auth += f":{parsed.password}"
        netloc = f"{auth}@{netloc}"
    return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))


def _docker_build_proxy_env_map(config: Config) -> dict[str, str]:
    return {key: _rewrite_proxy_url_for_build(value, config) for key, value in _proxy_env_map(config).items()}


def _docker_build_proxy_args(config: Config) -> list[str]:
    args: list[str] = []
    for key, value in _docker_build_proxy_env_map(config).items():
        args.extend(["--build-arg", f"{key}={value}"])
    return args


def _docker_build_network_args(config: Config) -> list[str]:
    if config.docker.proxy.enabled and config.docker.proxy.add_host_gateway:
        return ["--network", "host"]
    return []


def _docker_run_network_args(config: Config) -> list[str]:
    if config.docker.proxy.enabled and config.docker.proxy.add_host_gateway:
        return ["--add-host", "host.docker.internal:host-gateway"]
    return []


def _proxy_allow_endpoints(config: Config) -> list[str]:
    if not config.docker.proxy.enabled:
        return []
    endpoints: list[str] = []
    for proxy_url in [config.docker.proxy.http, config.docker.proxy.https, config.docker.proxy.all]:
        if not proxy_url:
            continue
        parsed = urlparse(proxy_url)
        if not parsed.hostname or not parsed.port:
            continue
        endpoint = f"{parsed.hostname}:{parsed.port}"
        if endpoint not in endpoints:
            endpoints.append(endpoint)
    return endpoints


def _docker_env_args(config: Config, allow_ips: list[str]) -> list[str]:
    env_args = [
        "-e",
        f"BENCH_ALLOW_EGRESS_IPS={','.join(allow_ips)}",
        "-e",
        f"BENCH_ALLOW_EGRESS_ENDPOINTS={','.join(_proxy_allow_endpoints(config))}",
        "-e",
        "HOME=/home/bench",
    ]
    if hasattr(os, "getuid") and hasattr(os, "getgid"):
        env_args.extend(
            [
                "-e",
                f"AMPERMBENCH_UID={os.getuid()}",
                "-e",
                f"AMPERMBENCH_GID={os.getgid()}",
            ]
        )
    for key, value in _proxy_env_map(config).items():
        env_args.extend(["-e", f"{key}={value}"])
    if config.provider == "claude":
        assert config.claude is not None
        env_args.extend(["-e", f"ANTHROPIC_BASE_URL={config.claude.base_url}"])
        if config.claude.auth_mode == "api_key":
            env_args.extend(["-e", f"ANTHROPIC_API_KEY={config.claude.api_key}"])
    else:
        env_args.extend(["-e", "CODEX_HOME=/home/bench/.codex"])
        assert config.codex is not None
        if config.codex.auth_mode == "api_key":
            env_args.extend(["-e", f"OPENAI_API_KEY={config.codex.api_key}"])
    return env_args


def _prepare_agent_home(config: Config, agent_home: Path) -> None:
    if config.provider == "claude":
        assert config.claude is not None
        claude_dir = agent_home / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        (claude_dir / "debug").mkdir(parents=True, exist_ok=True)
        if config.claude.auth_mode == "host_login":
            shutil.copy2(Path(config.claude.host_credentials_path).expanduser(), claude_dir / ".credentials.json")
        return
    assert config.codex is not None
    codex_dir = agent_home / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    if config.codex.auth_mode == "host_login":
        shutil.copy2(Path(config.codex.host_auth_path).expanduser(), codex_dir / "auth.json")


def _agent_invocation(
    config: Config,
    session_id: str,
    mode: str,
    output_format: str,
    prompt: str,
    debug_file: str | None = None,
    add_dirs: list[str] | None = None,
) -> list[str]:
    if config.provider == "claude":
        assert config.claude is not None
        cmd = [
            "claude",
            "-p",
            "--session-id",
            session_id,
            "--permission-mode",
            mode,
            "--output-format",
            output_format,
            "--model",
            config.claude.model,
            "--max-turns",
            str(config.claude.max_turns),
        ]
        if debug_file:
            cmd.extend(["--debug-file", debug_file])
        if output_format == "stream-json":
            cmd.append("--verbose")
        if config.claude.auth_mode == "api_key":
            cmd.append("--bare")
        cmd.extend(["--max-budget-usd", str(config.claude.max_budget_usd), prompt])
        return cmd
    assert config.codex is not None
    cmd = [
        "codex",
        "exec",
        "--json",
        "--skip-git-repo-check",
        "-c",
        f'model_reasoning_effort="{config.codex.reasoning_effort}"',
        "-m",
        config.codex.model,
    ]
    for directory in add_dirs or []:
        cmd.extend(["--add-dir", directory])
    if mode == "fullAuto":
        cmd.append("--full-auto")
    elif mode == "bypassPermissions":
        cmd.append("--dangerously-bypass-approvals-and-sandbox")
    else:
        raise ValueError(f"Unsupported Codex mode: {mode}")
    cmd.append(prompt)
    if config.codex.auth_mode == "api_key":
        return [
            "sh",
            "-lc",
            'mkdir -p "$CODEX_HOME" && printf %s "$OPENAI_API_KEY" | codex login --with-api-key >/dev/null && exec '
            + shlex.join(cmd),
        ]
    return cmd


def _snapshot_task_state(task: str, runtime: Path) -> dict[str, Any]:
    if task == "clean-up-branches":
        repo_dir = runtime / "repo"
        local: list[str] = []
        remote: list[str] = []
        snapshot_errors: list[str] = []
        if repo_dir.exists():
            try:
                local = subprocess.check_output(
                    ["git", "-C", str(repo_dir), "for-each-ref", "--format=%(refname:short)", "refs/heads"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                ).splitlines()
            except subprocess.CalledProcessError as exc:
                snapshot_errors.append(f"local_refs_failed:{exc.returncode}")
            try:
                remote = sorted(
                    [
                        f"origin/{line.split()[1].removeprefix('refs/heads/')}"
                        for line in subprocess.check_output(
                            ["git", "-C", str(repo_dir), "ls-remote", "--heads", "origin"],
                            text=True,
                            stderr=subprocess.DEVNULL,
                        ).splitlines()
                        if line.strip()
                    ]
                )
            except subprocess.CalledProcessError as exc:
                snapshot_errors.append(f"remote_refs_failed:{exc.returncode}")
        else:
            snapshot_errors.append("repo_missing")
        return {"local": sorted(local), "remote": remote, "snapshot_errors": snapshot_errors}
    if task == "cancel-jobs":
        return json.loads((runtime / "cluster" / "jobs.json").read_text(encoding="utf-8"))
    if task == "restart-services":
        return json.loads((runtime / "cluster" / "services.json").read_text(encoding="utf-8"))
    if task == "clean-up-artifacts":
        return json.loads((runtime / "storage" / "objects.json").read_text(encoding="utf-8"))
    raise ValueError(f"Unsupported task: {task}")


def _prompt_lookup(task: str, repo_root: Path) -> dict[str, dict[str, Any]]:
    spec = TASK_SPECS[task]
    prompts = json.loads((spec.harness_dir(repo_root) / "prompts" / spec.prompt_file).read_text(encoding="utf-8"))
    return {prompt["prompt_id"]: prompt for prompt in prompts}


def _evaluate(task: str, prompt_id: str, mode: str, before: dict[str, Any], after: dict[str, Any], action_trace: list[dict[str, Any]]) -> dict[str, Any]:
    if task == "clean-up-branches":
        return eval_branches.evaluate(prompt_id, mode, before, after, action_trace)
    if task == "cancel-jobs":
        return eval_cancel_jobs.evaluate(prompt_id, mode, before, after, action_trace)
    if task == "restart-services":
        return eval_restart_services.evaluate(prompt_id, mode, before, after, action_trace)
    if task == "clean-up-artifacts":
        return eval_cleanup_artifacts.evaluate(prompt_id, mode, before, after, action_trace)
    raise ValueError(f"Unsupported task: {task}")


def _preflight_provider(repo_root: Path, config: Config) -> None:
    task = config.benchmark.tasks[0]
    allow_ips = _resolve_allowed_ips(config)
    image = _image_tag(task, config)
    with tempfile.TemporaryDirectory(prefix=f"ampermbench-{config.provider}-preflight-") as temp_dir:
        preflight_home = Path(temp_dir)
        _prepare_agent_home(config, preflight_home)
        stdout_path = preflight_home / "preflight.stdout.jsonl"
        if config.provider == "claude":
            assert config.claude is not None
            if "auto" not in config.claude.permission_modes:
                return
            debug_file = "/home/bench/.claude/debug/preflight-auto.txt"
            cmd = [
                "docker",
                "run",
                "--rm",
                "--cap-add",
                "NET_ADMIN",
                *_docker_run_network_args(config),
                *_docker_env_args(config, allow_ips),
                "-v",
                f"{preflight_home}:/home/bench",
                image,
                *_agent_invocation(
                    config,
                    session_id=str(uuid.uuid4()),
                    mode="auto",
                    output_format="stream-json",
                    prompt="Reply with OK.",
                    debug_file=debug_file,
                ),
            ]
            with stdout_path.open("w", encoding="utf-8") as stdout_handle:
                subprocess.run(cmd, check=True, stdout=stdout_handle, stderr=subprocess.DEVNULL)
            fallback_lines = _auto_mode_fallback_lines(preflight_home / ".claude" / "debug" / "preflight-auto.txt")
            if fallback_lines:
                raise RuntimeError(
                    "Requested auto mode, but Claude Code fell back to default during preflight: "
                    + " | ".join(fallback_lines[:3])
                )
            return
        assert config.codex is not None
        if "fullAuto" not in config.codex.permission_modes:
            return
        cmd = [
            "docker",
            "run",
            "--rm",
            "--cap-add",
            "NET_ADMIN",
            *_docker_run_network_args(config),
            *_docker_env_args(config, allow_ips),
            "-v",
            f"{preflight_home}:/home/bench",
            "-w",
            "/tmp",
            image,
                *_agent_invocation(
                    config,
                    session_id=str(uuid.uuid4()),
                    mode="fullAuto",
                    output_format="json",
                    prompt="Reply with OK.",
                    add_dirs=[],
                ),
            ]
        with stdout_path.open("w", encoding="utf-8") as stdout_handle:
            subprocess.run(cmd, check=True, stdout=stdout_handle, stderr=subprocess.DEVNULL)


def _run_one(repo_root: Path, config: Config, task: str, prompt: dict[str, Any], mode: str, repeat: int, results_root: Path) -> dict[str, Any]:
    spec = TASK_SPECS[task]
    session_id = str(uuid.uuid4())
    run_dir = results_root / task / prompt["prompt_id"] / mode / f"repeat-{repeat:03d}"
    run_dir.mkdir(parents=True, exist_ok=True)
    agent_home = run_dir / _provider_home_dirname(config)
    runtime_root = run_dir / "bench-runtime"
    _prepare_agent_home(config, agent_home)
    reset_stdout_path = run_dir / "reset_env.stdout.txt"
    reset_stderr_path = run_dir / "reset_env.stderr.txt"
    reset_env = os.environ.copy()
    reset_env["PYTHONPATH"] = (
        f"{repo_root / 'src'}{os.pathsep}{reset_env['PYTHONPATH']}"
        if reset_env.get("PYTHONPATH")
        else str(repo_root / "src")
    )
    with reset_stdout_path.open("w", encoding="utf-8") as reset_stdout_handle, reset_stderr_path.open(
        "w", encoding="utf-8"
    ) as reset_stderr_handle:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "ampermbench.tasks.reset_task",
                task,
                str(repo_root),
                str(runtime_root),
            ],
            check=True,
            env=reset_env,
            stdout=reset_stdout_handle,
            stderr=reset_stderr_handle,
        )
    before = _snapshot_task_state(task, runtime_root)
    (run_dir / "before_state.json").write_text(json_dumps(before), encoding="utf-8")
    (run_dir / "prompt.json").write_text(json_dumps(prompt), encoding="utf-8")
    ips = _resolve_allowed_ips(config)
    docker_cmd = [
        "docker",
        "run",
        "--rm",
        "--cidfile",
        str(run_dir / "docker.cid"),
        "--cap-add",
        "NET_ADMIN",
        *_docker_run_network_args(config),
        *_docker_env_args(config, ips),
        "-v",
        f"{runtime_root}:/bench",
        "-v",
        f"{agent_home}:/home/bench",
        "-w",
        spec.workdir,
        _image_tag(task, config),
        *_agent_invocation(
            config,
            session_id=session_id,
            mode=mode,
            output_format="stream-json" if config.provider == "claude" else "json",
            prompt=prompt["prompt"],
            debug_file=f"/home/bench/.claude/debug/{session_id}.txt" if config.provider == "claude" else None,
            add_dirs=["/bench"] if config.provider == "codex" else [],
        ),
    ]
    stdout_path = run_dir / f"{config.provider}.stdout.jsonl"
    stderr_path = run_dir / f"{config.provider}.stderr.txt"
    cidfile = run_dir / "docker.cid"
    timed_out = False
    completion_marker_seen = False
    with stdout_path.open("w", encoding="utf-8") as stdout_handle, stderr_path.open("w", encoding="utf-8") as stderr_handle:
        start = time.perf_counter()
        try:
            process = subprocess.run(
                docker_cmd,
                stdout=stdout_handle,
                stderr=stderr_handle,
                timeout=RUN_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            timed_out = True
            stdout_handle.flush()
            stderr_handle.flush()
            _force_stop_container(_read_container_id(cidfile))
            completion_marker_seen = _stdout_has_completion_marker(config.provider, stdout_path)
            stderr_handle.write(
                f"ampermbench timeout after {RUN_TIMEOUT_SECONDS}s; "
                f"completion_marker_seen={str(completion_marker_seen).lower()}\n"
            )
            stderr_handle.flush()
            process = subprocess.CompletedProcess(docker_cmd, 124)
        wall_clock_ms = int((time.perf_counter() - start) * 1000)
    after = _snapshot_task_state(task, runtime_root)
    (run_dir / "after_state.json").write_text(json_dumps(after), encoding="utf-8")
    if config.provider == "claude":
        action_log_path = agent_home / ".claude" / "action-log.jsonl"
        debug_path = agent_home / ".claude" / "debug" / f"{session_id}.txt"
        transcript_dir = agent_home / ".claude" / "projects"
    else:
        action_log_path = agent_home / ".codex" / "action-log.jsonl"
        debug_path = agent_home / ".codex" / "debug.txt"
        transcript_dir = agent_home / ".codex" / "sessions"
    runtime_log_src = runtime_root / "logs" / spec.log_file
    runtime_log_path = run_dir / spec.log_file
    if runtime_log_src.exists():
        shutil.copy2(runtime_log_src, runtime_log_path)
    _copy_tree_if_exists(transcript_dir, run_dir / "transcripts")
    action_trace = build_action_trace(config.provider, task, before, stdout_path, action_log_path, debug_path, runtime_log_path, after=after)
    (run_dir / "action_trace.jsonl").write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in action_trace), encoding="utf-8")
    session_summary = extract_session_summary(config.provider, stdout_path)
    auto_fallback_lines = _auto_mode_fallback_lines(debug_path)
    if config.provider == "claude" and mode == "auto" and auto_fallback_lines:
        raise RuntimeError(
            f"Requested auto mode for {task}/{prompt['prompt_id']}, but Claude Code fell back to default: "
            + " | ".join(auto_fallback_lines[:3])
        )
    result = _evaluate(task, prompt["prompt_id"], mode, before, after, action_trace)
    result.update(
        {
            "task": task,
            "session_id": session_id,
            "docker_exit_code": process.returncode,
            "before_state_path": str(run_dir / "before_state.json"),
            "after_state_path": str(run_dir / "after_state.json"),
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "debug_path": str(debug_path),
            "action_log_path": str(action_log_path),
            "runtime_log_path": str(runtime_log_path),
            "transcript_dir": str(run_dir / "transcripts"),
            "reset_stdout_path": str(reset_stdout_path),
            "reset_stderr_path": str(reset_stderr_path),
            "wall_clock_ms": wall_clock_ms,
            "timed_out": timed_out,
            "timeout_seconds": RUN_TIMEOUT_SECONDS,
            "completion_marker_seen": completion_marker_seen,
            **session_summary,
            "auto_mode_fallback_lines": auto_fallback_lines,
        }
    )
    (run_dir / "result.json").write_text(json_dumps(result), encoding="utf-8")
    return result


def run_benchmark(repo_root: Path, config: Config, results_root: Path) -> list[dict[str, Any]]:
    materialize(repo_root, config.benchmark.tasks)
    if config.docker.build_images:
        _build_images(repo_root, config)
    else:
        _ensure_images_available(config)
    with proxy_bridge_stack(config):
        _preflight_provider(repo_root, config)
        prompts_cache = {task: _prompt_lookup(task, repo_root) for task in config.benchmark.tasks}
        requested_prompt_ids = set(config.benchmark.prompt_ids)
        task_jobs: dict[str, list[tuple[str, dict[str, Any], str, int]]] = {task: [] for task in config.benchmark.tasks}
        for task in config.benchmark.tasks:
            prompt_ids = sorted(prompt_id for prompt_id in prompts_cache[task] if not requested_prompt_ids or prompt_id in requested_prompt_ids)
            for prompt_id in prompt_ids:
                prompt = prompts_cache[task][prompt_id]
                for mode in _provider_modes(config):
                    for repeat in range(1, config.benchmark.repeats + 1):
                        task_jobs[task].append((task, prompt, mode, repeat))
        jobs: list[tuple[str, dict[str, Any], str, int]] = []
        while any(task_jobs.values()):
            for task in config.benchmark.tasks:
                if task_jobs[task]:
                    jobs.append(task_jobs[task].pop(0))
        rows: list[dict[str, Any]] = []
        if len(jobs) == 1:
            task, prompt, mode, repeat = jobs[0]
            rows.append(_run_one(repo_root, config, task, prompt, mode, repeat, results_root))
        else:
            with ThreadPoolExecutor(max_workers=min(4, len(jobs))) as executor:
                futures = [
                    executor.submit(_run_one, repo_root, config, task, prompt, mode, repeat, results_root)
                    for task, prompt, mode, repeat in jobs
                ]
                for future in as_completed(futures):
                    rows.append(future.result())
        if requested_prompt_ids and not rows:
            raise ValueError(f"No prompts matched requested prompt_ids: {', '.join(sorted(requested_prompt_ids))}")
        return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config/benchmark.yaml"))
    parser.add_argument("--results-root", type=Path, default=None)
    args = parser.parse_args()
    repo_root = repo_root_from(args.config.resolve().parent if args.config.is_absolute() else Path.cwd())
    config_path = args.config if args.config.is_absolute() else repo_root / args.config
    config = load_config(config_path)
    results_root = args.results_root or (repo_root / "results" / "runs" / _timestamp())
    results_root.mkdir(parents=True, exist_ok=True)
    rows = run_benchmark(repo_root, config, results_root)
    metrics = summarize(rows)
    write_outputs(results_root, metrics)
    (results_root / "summary" / "resolved_config.json").write_text(json_dumps(asdict(config)), encoding="utf-8")


if __name__ == "__main__":
    main()
