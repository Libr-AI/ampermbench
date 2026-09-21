from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import yaml

from .tasks import TASK_SPECS
from .utils import expand_env


AUTO_MODELS = {
    "sonnet",
    "opus",
    "claude-sonnet-4-6",
    "claude-opus-4-6",
}

CLAUDE_FIRST_PARTY_SUFFIXES = ("anthropic.com", "claude.ai")
OPENAI_FIRST_PARTY_SUFFIXES = ("openai.com", "chatgpt.com")
SUPPORTED_PROVIDERS = {"claude", "codex"}
SUPPORTED_MODES = {
    "claude": {"auto", "bypassPermissions"},
    "codex": {"fullAuto", "bypassPermissions"},
}
SUPPORTED_AUTH_MODES = {"api_key", "host_login"}
SUPPORTED_CODEX_REASONING_EFFORTS = {"minimal", "low", "medium", "high"}


@dataclass(frozen=True)
class ClaudeConfig:
    version: str
    auth_mode: str
    api_key: str
    host_credentials_path: str
    base_url: str
    model: str
    permission_modes: list[str]
    max_turns: int
    max_budget_usd: float


@dataclass(frozen=True)
class CodexConfig:
    version: str
    auth_mode: str
    api_key: str
    host_auth_path: str
    model: str
    permission_modes: list[str]
    reasoning_effort: str


@dataclass(frozen=True)
class BenchmarkConfig:
    tasks: list[str]
    prompt_ids: list[str]
    repeats: int


@dataclass(frozen=True)
class DockerConfig:
    restrict_egress: bool
    build_images: bool
    proxy: "DockerProxyConfig"


@dataclass(frozen=True)
class DockerProxyConfig:
    enabled: bool
    http: str
    https: str
    all: str
    no_proxy: str
    add_host_gateway: bool


@dataclass(frozen=True)
class Config:
    provider: str
    claude: ClaudeConfig | None
    codex: CodexConfig | None
    benchmark: BenchmarkConfig
    docker: DockerConfig

    @property
    def active_provider_config(self) -> ClaudeConfig | CodexConfig:
        if self.provider == "claude":
            if self.claude is None:
                raise ValueError("provider=claude requires a claude config block")
            return self.claude
        if self.provider == "codex":
            if self.codex is None:
                raise ValueError("provider=codex requires a codex config block")
            return self.codex
        raise ValueError(f"Unsupported provider: {self.provider}")


def load_config(path: str | Path) -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    data = expand_env(raw)
    provider = data.get("provider")
    if provider is None:
        if "claude" in data:
            provider = "claude"
        elif "codex" in data:
            provider = "codex"
        else:
            raise ValueError("Config must define either provider=... or a claude/codex config block")
    docker_data = dict(data["docker"])
    proxy_data = {
        "enabled": False,
        "http": "",
        "https": "",
        "all": "",
        "no_proxy": "",
        "add_host_gateway": True,
        **docker_data.get("proxy", {}),
    }
    docker_data["proxy"] = DockerProxyConfig(**proxy_data)
    config = Config(
        provider=provider,
        claude=ClaudeConfig(**data["claude"]) if data.get("claude") else None,
        codex=CodexConfig(**data["codex"]) if data.get("codex") else None,
        benchmark=BenchmarkConfig(**data["benchmark"]),
        docker=DockerConfig(**docker_data),
    )
    validate_config(config)
    return config


def is_claude_first_party_base_url(base_url: str) -> bool:
    host = urlparse(base_url).hostname or ""
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in CLAUDE_FIRST_PARTY_SUFFIXES)


def is_openai_first_party_host(host: str) -> bool:
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in OPENAI_FIRST_PARTY_SUFFIXES)


def validate_config(config: Config) -> None:
    if config.provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported provider: {config.provider}")
    if config.provider == "claude":
        claude = config.claude
        if claude is None:
            raise ValueError("provider=claude requires a claude config block")
        if not claude.version:
            raise ValueError("claude.version must be set")
        if claude.auth_mode not in SUPPORTED_AUTH_MODES:
            raise ValueError(f"Unsupported claude.auth_mode: {claude.auth_mode}")
        if claude.auth_mode == "api_key" and not claude.api_key:
            raise ValueError("claude.api_key resolved to empty string")
        if claude.auth_mode == "host_login":
            credentials_path = Path(claude.host_credentials_path).expanduser()
            if not credentials_path.exists():
                raise ValueError(f"claude.host_credentials_path does not exist: {credentials_path}")
            if not is_claude_first_party_base_url(claude.base_url):
                raise ValueError("claude.auth_mode=host_login requires a first-party Anthropic base URL")
        if not claude.base_url:
            raise ValueError("claude.base_url must be set")
        for mode in claude.permission_modes:
            if mode not in SUPPORTED_MODES["claude"]:
                raise ValueError(f"Unsupported Claude mode: {mode}")
            if mode == "auto":
                if not is_claude_first_party_base_url(claude.base_url):
                    raise ValueError("auto mode requires a first-party Anthropic base URL")
                if claude.model not in AUTO_MODELS:
                    raise ValueError("auto mode requires a supported Sonnet/Opus model")
    if config.provider == "codex":
        codex = config.codex
        if codex is None:
            raise ValueError("provider=codex requires a codex config block")
        if not codex.version:
            raise ValueError("codex.version must be set")
        if codex.auth_mode not in SUPPORTED_AUTH_MODES:
            raise ValueError(f"Unsupported codex.auth_mode: {codex.auth_mode}")
        if codex.auth_mode == "api_key" and not codex.api_key:
            raise ValueError("codex.api_key resolved to empty string")
        if codex.auth_mode == "host_login":
            auth_path = Path(codex.host_auth_path).expanduser()
            if not auth_path.exists():
                raise ValueError(f"codex.host_auth_path does not exist: {auth_path}")
        for mode in codex.permission_modes:
            if mode not in SUPPORTED_MODES["codex"]:
                raise ValueError(f"Unsupported Codex mode: {mode}")
        if codex.reasoning_effort not in SUPPORTED_CODEX_REASONING_EFFORTS:
            raise ValueError(
                "codex.reasoning_effort must be one of: "
                + ", ".join(sorted(SUPPORTED_CODEX_REASONING_EFFORTS))
            )
    if config.benchmark.repeats < 1:
        raise ValueError("benchmark.repeats must be >= 1")
    if config.docker.proxy.enabled and not any(
        [config.docker.proxy.http, config.docker.proxy.https, config.docker.proxy.all]
    ):
        raise ValueError("docker.proxy.enabled=true requires at least one of docker.proxy.http/https/all")
    unknown_tasks = [task for task in config.benchmark.tasks if task not in TASK_SPECS]
    if unknown_tasks:
        raise ValueError(f"Unknown benchmark tasks: {', '.join(sorted(unknown_tasks))}")
