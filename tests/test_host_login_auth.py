from __future__ import annotations

import json

from ampermbench.config import (
    BenchmarkConfig,
    ClaudeConfig,
    CodexConfig,
    Config,
    DockerConfig,
    DockerProxyConfig,
    validate_config,
)
from ampermbench.runner import (
    _auto_mode_fallback_lines,
    _agent_invocation,
    _docker_build_network_args,
    _docker_build_proxy_env_map,
    _docker_env_args,
    _docker_run_network_args,
    _prepare_agent_home,
    _stdout_has_completion_marker,
)


def _proxy(enabled: bool = False) -> DockerProxyConfig:
    return DockerProxyConfig(
        enabled=enabled,
        http="http://host.docker.internal:1145" if enabled else "",
        https="http://host.docker.internal:1145" if enabled else "",
        all="",
        no_proxy="127.0.0.1,localhost" if enabled else "",
        add_host_gateway=True,
    )


def test_host_login_config_allows_empty_api_key(tmp_path):
    credentials_path = tmp_path / ".credentials.json"
    credentials_path.write_text(json.dumps({"claudeAiOauth": {"accessToken": "redacted"}}), encoding="utf-8")

    config = Config(
        provider="claude",
        claude=ClaudeConfig(
            version="2.1.86",
            auth_mode="host_login",
            api_key="",
            host_credentials_path=str(credentials_path),
            base_url="https://api.anthropic.com",
            model="claude-sonnet-4-6",
            permission_modes=["auto"],
            max_turns=8,
            max_budget_usd=1.0,
        ),
        codex=None,
        benchmark=BenchmarkConfig(tasks=["cancel-jobs"], prompt_ids=[], repeats=1),
        docker=DockerConfig(restrict_egress=True, build_images=False, proxy=_proxy()),
    )

    validate_config(config)


def test_prepare_claude_home_copies_credentials_and_omits_bare(tmp_path):
    credentials_path = tmp_path / "host-creds.json"
    credentials_path.write_text(json.dumps({"claudeAiOauth": {"accessToken": "redacted"}}), encoding="utf-8")
    config = Config(
        provider="claude",
        claude=ClaudeConfig(
            version="2.1.86",
            auth_mode="host_login",
            api_key="",
            host_credentials_path=str(credentials_path),
            base_url="https://api.anthropic.com",
            model="claude-sonnet-4-6",
            permission_modes=["auto"],
            max_turns=8,
            max_budget_usd=1.0,
        ),
        codex=None,
        benchmark=BenchmarkConfig(tasks=["cancel-jobs"], prompt_ids=[], repeats=1),
        docker=DockerConfig(restrict_egress=True, build_images=False, proxy=_proxy(enabled=True)),
    )

    claude_home = tmp_path / "claude-home"
    _prepare_agent_home(config, claude_home)

    copied = claude_home / ".claude" / ".credentials.json"
    assert copied.exists()
    assert json.loads(copied.read_text(encoding="utf-8")) == json.loads(credentials_path.read_text(encoding="utf-8"))

    invocation = _agent_invocation(
        config,
        session_id="00000000-0000-0000-0000-000000000000",
        mode="auto",
        output_format="stream-json",
        prompt="Reply with OK.",
        debug_file="/home/bench/.claude/debug/test.txt",
    )
    assert "--bare" not in invocation

    env_args = _docker_env_args(config, ["1.2.3.4"])
    run_network_args = _docker_run_network_args(config)
    build_network_args = _docker_build_network_args(config)
    build_proxy_env = _docker_build_proxy_env_map(config)
    assert "HTTP_PROXY=http://host.docker.internal:1145" in env_args
    assert "HTTPS_PROXY=http://host.docker.internal:1145" in env_args
    assert "BENCH_ALLOW_EGRESS_ENDPOINTS=host.docker.internal:1145" in env_args
    assert run_network_args == ["--add-host", "host.docker.internal:host-gateway"]
    assert build_network_args == ["--network", "host"]
    assert build_proxy_env["HTTP_PROXY"] == "http://127.0.0.1:1145"
    assert build_proxy_env["HTTPS_PROXY"] == "http://127.0.0.1:1145"


def test_codex_host_login_copies_auth_and_uses_full_auto(tmp_path):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps({"auth_mode": "chatgpt", "tokens": {"access_token": "redacted"}}),
        encoding="utf-8",
    )
    config = Config(
        provider="codex",
        claude=None,
        codex=CodexConfig(
            version="0.104.0",
            auth_mode="host_login",
            api_key="",
            host_auth_path=str(auth_path),
            model="gpt-5.4-mini",
            permission_modes=["fullAuto", "bypassPermissions"],
            reasoning_effort="medium",
        ),
        benchmark=BenchmarkConfig(tasks=["cancel-jobs"], prompt_ids=[], repeats=1),
        docker=DockerConfig(restrict_egress=True, build_images=False, proxy=_proxy(enabled=True)),
    )

    validate_config(config)

    codex_home = tmp_path / "codex-home"
    _prepare_agent_home(config, codex_home)

    copied = codex_home / ".codex" / "auth.json"
    assert copied.exists()
    assert json.loads(copied.read_text(encoding="utf-8")) == json.loads(auth_path.read_text(encoding="utf-8"))

    invocation = _agent_invocation(
        config,
        session_id="00000000-0000-0000-0000-000000000000",
        mode="fullAuto",
        output_format="json",
        prompt="Reply with OK.",
        add_dirs=["/bench"],
    )
    assert invocation[:3] == ["codex", "exec", "--json"]
    assert "--add-dir" in invocation
    assert "/bench" in invocation
    assert "--full-auto" in invocation
    assert "--dangerously-bypass-approvals-and-sandbox" not in invocation

    env_args = _docker_env_args(config, ["1.2.3.4"])
    assert "CODEX_HOME=/home/bench/.codex" in env_args


def test_codex_api_key_requires_nonempty_key():
    config = Config(
        provider="codex",
        claude=None,
        codex=CodexConfig(
            version="0.104.0",
            auth_mode="api_key",
            api_key="",
            host_auth_path="",
            model="gpt-5.4-mini",
            permission_modes=["bypassPermissions"],
            reasoning_effort="medium",
        ),
        benchmark=BenchmarkConfig(tasks=["cancel-jobs"], prompt_ids=[], repeats=1),
        docker=DockerConfig(restrict_egress=True, build_images=False, proxy=_proxy()),
    )

    try:
        validate_config(config)
    except ValueError as exc:
        assert "codex.api_key" in str(exc)
    else:
        raise AssertionError("Expected validate_config to reject empty codex.api_key")


def test_codex_api_key_invocation_bootstraps_login():
    config = Config(
        provider="codex",
        claude=None,
        codex=CodexConfig(
            version="0.104.0",
            auth_mode="api_key",
            api_key="sk-test",
            host_auth_path="",
            model="gpt-5.4",
            permission_modes=["fullAuto", "bypassPermissions"],
            reasoning_effort="medium",
        ),
        benchmark=BenchmarkConfig(tasks=["cancel-jobs"], prompt_ids=[], repeats=1),
        docker=DockerConfig(restrict_egress=False, build_images=False, proxy=_proxy(enabled=True)),
    )

    invocation = _agent_invocation(
        config,
        session_id="00000000-0000-0000-0000-000000000000",
        mode="fullAuto",
        output_format="json",
        prompt="Reply with OK.",
        add_dirs=["/bench"],
    )

    assert invocation[:2] == ["sh", "-lc"]
    assert "codex login --with-api-key" in invocation[2]
    assert "--add-dir /bench" in invocation[2]
    assert "gpt-5.4" in invocation[2]
    env_args = _docker_env_args(config, [])
    assert "OPENAI_API_KEY=sk-test" in env_args


def test_auto_mode_fallback_lines_detect_circuit_breaker(tmp_path):
    debug_path = tmp_path / "auto-debug.txt"
    debug_path.write_text(
        "\n".join(
            [
                "2026-03-28T13:15:53.937Z [WARN] auto mode circuit breaker active (cached) — falling back to default",
                "2026-03-28T13:15:54.488Z [DEBUG] [auto-mode] verifyAutoModeGateAccess: enabledState=disabled disabledBySettings=false model=claude-sonnet-4-6 modelSupported=true disableFastModeBreakerFires=false carouselAvailable=false canEnterAuto=false",
            ]
        ),
        encoding="utf-8",
    )

    matches = _auto_mode_fallback_lines(debug_path)

    assert len(matches) == 2
    assert "falling back to default" in matches[0].lower()


def test_completion_marker_detection_for_claude_and_codex(tmp_path):
    claude_stdout = tmp_path / "claude.stdout.jsonl"
    claude_stdout.write_text(
        "\n".join(
            [
                json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}),
                json.dumps({"type": "result", "subtype": "success", "result": "OK."}),
            ]
        ),
        encoding="utf-8",
    )
    codex_stdout = tmp_path / "codex.stdout.jsonl"
    codex_stdout.write_text(
        "\n".join(
            [
                json.dumps({"type": "item.started", "item": {"id": "item_1", "type": "command_execution"}}),
                json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}),
            ]
        ),
        encoding="utf-8",
    )

    assert _stdout_has_completion_marker("claude", claude_stdout) is True
    assert _stdout_has_completion_marker("codex", codex_stdout) is True
    assert _stdout_has_completion_marker("claude", codex_stdout) is False
