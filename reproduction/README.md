# Reproduction workspace

Wishing Willow Milestone 1. Plan and evidence checklist: the `reproduction.md` page in the wishing-willow knowledge base. `main` in this fork is identical to upstream; everything WW adds during reproduction lives in this directory and in `config/benchmark.yaml` on this branch.

## Environment manifest (21 September 2026)

| Item | Value |
|---|---|
| Host | agent-redteaming-worker-1 (AWS m6id.4xlarge, 16 vCPU, 61 GB, 100 GB root), Ubuntu 26.04 LTS |
| Checkout | `~/wishing-willow/ampermbench`, fork `Libr-AI/ampermbench`, upstream pinned at tag `upstream-6dc2a2e` (`6dc2a2e95e106fbb5852f2fc250f3a82939c2c09`) |
| Docker | Docker CE 29.8.1, overlayfs, root `/var/lib/docker` |
| Python | uv 0.12.17; venv `.venv` on CPython 3.11.16; ampermbench 0.1.0, PyYAML 6.0.3, pytest 9.1.1 |
| Upstream tests | `python -m pytest -q tests` → 24 passed |
| Agent image | `ampermbench-claude-base:2.1.86` and one image per task, built with `docker build` from `docker/base/Dockerfile` (`CLAUDE_VERSION=2.1.86`) and `tasks/<task>/Dockerfile` |
| Base image ID | `sha256:c5fed5f9c3751fd8f907fcbf4769915d1e15267fd87c996edf4b94e265f884d8` |
| In-container | Claude Code 2.1.86, git 2.43.0, Python 3.12.3, user `bench` (UID 1001); `--permission-mode` choices include `auto` |
| Config changes vs upstream | `claude.auth_mode: api_key`; `claude.permission_modes: [auto, bypassPermissions]`; `docker.proxy.enabled: false` |
| Reset determinism | all four tasks identical across 3 resets, see `reset-determinism.md` |

## Before running

```bash
cd ~/wishing-willow/ampermbench && source .venv/bin/activate
export ANTHROPIC_API_KEY=...   # project key; never commit it
ampermbench-run                # uses config/benchmark.yaml
ampermbench-aggregate results/runs/<timestamp>
```

`results/runs/` is git-ignored. Resets rewrite tracked files under `tasks/*/runtime` (shim logs, git hook samples); do not commit that noise.
