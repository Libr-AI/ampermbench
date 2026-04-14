#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG_PATH="${1:-config/benchmark.yaml}"
VENV_DIR="${REPO_ROOT}/.venv"

cd "${REPO_ROOT}"

log() {
  printf '[ampermbench] %s\n' "$*"
}

die() {
  printf '\n[ampermbench] ERROR: %s\n' "$1" >&2
  if [[ $# -ge 2 ]]; then
    printf '[ampermbench] FIX: %s\n' "$2" >&2
  fi
  exit 1
}

ensure_uv() {
  command -v uv >/dev/null 2>&1 || die \
    "uv is not installed or not on PATH." \
    "Install uv first: curl -LsSf https://astral.sh/uv/install.sh | sh  (then open a new shell)"
}

ensure_config_exists() {
  [[ -f "${CONFIG_PATH}" ]] || die \
    "Config file not found: ${CONFIG_PATH}" \
    "Run this script from the repository root or pass an explicit config path, for example: bash scripts/bootstrap_and_run_uv.sh config/benchmark.yaml"
}

ensure_docker() {
  command -v docker >/dev/null 2>&1 || die \
    "docker is not installed or not on PATH." \
    "Install Docker first, then verify with: docker version"

  if docker info >/dev/null 2>&1; then
    return
  fi

  local info_output
  info_output="$(docker info 2>&1 || true)"
  if grep -qi "permission denied" <<<"${info_output}"; then
    die \
      "docker is installed but the current user cannot access the daemon." \
      "Run: sudo usermod -aG docker \"$USER\" && newgrp docker"
  fi
  if grep -Eqi "cannot connect|is the docker daemon running|error during connect" <<<"${info_output}"; then
    die \
      "docker daemon is not running." \
      "Run: sudo systemctl start docker"
  fi
  die \
    "docker info failed." \
    "Run: docker info ; fix that error first, then rerun this script."
}

provider_name() {
  read_config_field 'config.provider'
}

ensure_provider_cli() {
  local provider
  provider="$(provider_name)"
  if [[ "${provider}" == "claude" ]]; then
    command -v claude >/dev/null 2>&1 || die \
      "Claude Code is not installed on the host." \
      "Install Claude Code on the host, then verify with: claude --version"
    return
  fi
  if [[ "${provider}" == "codex" ]]; then
    command -v codex >/dev/null 2>&1 || die \
      "Codex CLI is not installed on the host." \
      "Install Codex CLI on the host, then verify with: codex --version"
    return
  fi
  die \
    "Unsupported provider in config: ${provider}" \
    "Set provider to 'claude' or 'codex' in config/benchmark.yaml"
}

ensure_venv() {
  if [[ -f "${VENV_DIR}/bin/python" ]]; then
    return
  fi
  log "Creating virtual environment: ${VENV_DIR}"
  uv venv --python 3.11 "${VENV_DIR}" || die \
    "Failed to create virtual environment at ${VENV_DIR}." \
    "Run manually: uv venv --python 3.11 .venv"
}

install_project() {
  log "Installing Python package into venv: ${VENV_DIR}"
  uv pip install --python "${VENV_DIR}/bin/python" -e . >/dev/null || die \
    "Failed to install the project into ${VENV_DIR}." \
    "Run manually: uv pip install --python .venv/bin/python -e ."
}

read_config_field() {
  local field="$1"
  "${VENV_DIR}/bin/python" - <<PY
from ampermbench.config import load_config
config = load_config("${CONFIG_PATH}")
value = ${field}
print(value)
PY
}

ensure_auth() {
  local provider
  provider="$(provider_name)"
  local auth_mode
  auth_mode="$(read_config_field "config.${provider}.auth_mode")"
  if [[ "${auth_mode}" == "host_login" ]]; then
    if [[ "${provider}" == "claude" ]] && claude auth status >/dev/null 2>&1; then
      return
    fi
    if [[ "${provider}" == "codex" ]] && codex login status >/dev/null 2>&1; then
      return
    fi
    if [[ "${provider}" == "claude" ]]; then
      die \
        "Host Claude Code is not logged in." \
        "Run: claude auth login"
    fi
    die \
      "Host Codex CLI is not logged in." \
      "Run: codex login"
  fi

  local api_key
  api_key="$(read_config_field "config.${provider}.api_key")"
  [[ -n "${api_key}" ]] || die \
    "${provider}.auth_mode=api_key but no API key was resolved from the config." \
    "Export the matching key first, for example: export ANTHROPIC_API_KEY=... or export OPENAI_API_KEY=..."
}

print_proxy_hint() {
  local proxy_enabled
  proxy_enabled="$(read_config_field 'config.docker.proxy.enabled')"
  if [[ "${proxy_enabled}" == "True" ]]; then
    log "Proxy enabled from config. Docker build and docker run will use the configured proxy."
  fi
}

run_benchmark() {
  log "Starting benchmark with config: ${CONFIG_PATH}"
  "${VENV_DIR}/bin/ampermbench-run" --config "${CONFIG_PATH}" || die \
    "Benchmark run failed." \
    "Inspect the latest logs under results/runs/, fix the reported error, then rerun: bash scripts/bootstrap_and_run_uv.sh ${CONFIG_PATH}"
}

ensure_uv
ensure_config_exists
ensure_docker
ensure_venv
install_project
ensure_provider_cli
ensure_auth
print_proxy_hint
run_benchmark
