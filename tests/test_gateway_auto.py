from __future__ import annotations

import pytest

from ampermbench.config import (
    BenchmarkConfig,
    ClaudeConfig,
    Config,
    DockerConfig,
    DockerProxyConfig,
    validate_config,
)


def _gateway_config(allow: bool, model: str = "anthropic/claude-sonnet-4.6") -> Config:
    return Config(
        provider="claude",
        claude=ClaudeConfig(
            version="2.1.86",
            auth_mode="api_key",
            api_key="test-key",
            host_credentials_path="",
            base_url="https://openrouter.ai/api",
            model=model,
            permission_modes=["auto"],
            max_turns=8,
            max_budget_usd=1.0,
            allow_auto_via_gateway=allow,
        ),
        codex=None,
        benchmark=BenchmarkConfig(tasks=["cancel-jobs"], prompt_ids=[], repeats=1),
        docker=DockerConfig(
            restrict_egress=True,
            build_images=False,
            proxy=DockerProxyConfig(enabled=False, http="", https="", all="", no_proxy="", add_host_gateway=True),
        ),
    )


def test_auto_via_gateway_rejected_by_default():
    with pytest.raises(ValueError, match="first-party"):
        validate_config(_gateway_config(allow=False))


def test_auto_via_gateway_allowed_with_flag():
    validate_config(_gateway_config(allow=True))


def test_auto_via_gateway_still_requires_supported_model():
    with pytest.raises(ValueError, match="Sonnet/Opus"):
        validate_config(_gateway_config(allow=True, model="anthropic/claude-haiku-4.5"))


def test_first_party_auto_unchanged_without_flag():
    config = _gateway_config(allow=False)
    first_party = Config(
        provider="claude",
        claude=ClaudeConfig(**{**config.claude.__dict__, "base_url": "https://api.anthropic.com", "model": "claude-sonnet-4-6"}),
        codex=None,
        benchmark=config.benchmark,
        docker=config.docker,
    )
    validate_config(first_party)
