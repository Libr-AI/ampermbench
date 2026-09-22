# Reproduction workspace

Wishing Willow Milestone 1. Plan and evidence checklist: the `reproduction.md` page in the wishing-willow knowledge base. `main` in this fork is identical to upstream; everything WW adds during reproduction lives in this directory and in `config/` on this branch.

## Environment manifest (21 September 2026)

| Item | Value |
|---|---|
| Host | agent-redteaming-worker-1 (AWS m6id.4xlarge, 16 vCPU, 61 GB, 100 GB root), Ubuntu 26.04 LTS |
| Checkout | `~/wishing-willow/ampermbench`, fork `Libr-AI/ampermbench`, upstream pinned at tag `upstream-6dc2a2e` (`6dc2a2e95e106fbb5852f2fc250f3a82939c2c09`) |
| Docker | Docker CE 29.8.1, overlayfs, root `/var/lib/docker` |
| Python | uv 0.12.17; venv `.venv` on CPython 3.11.16; ampermbench 0.1.0, PyYAML 6.0.3, pytest 9.1.1 |
| Upstream tests | `python -m pytest -q tests` → 24 passed (also after our config changes) |
| Agent image | `ampermbench-claude-base:2.1.86` and one image per task, built with `docker build` from `docker/base/Dockerfile` (`CLAUDE_VERSION=2.1.86`) and `tasks/<task>/Dockerfile` |
| Base image ID | `sha256:c5fed5f9c3751fd8f907fcbf4769915d1e15267fd87c996edf4b94e265f884d8` |
| In-container | Claude Code 2.1.86, git 2.43.0, Python 3.12.3, user `bench` (UID 1001); `--permission-mode` choices include `auto` |
| Model routing | OpenRouter, Anthropic Messages format, `https://openrouter.ai/api`; model id `anthropic/claude-sonnet-4.6`; key from `OPENROUTER_API_KEY` |
| Runner code | unchanged from upstream: `src/` and `tests/` are identical to tag `upstream-6dc2a2e` |
| Reset determinism | all four tasks identical across 3 resets, see `reset-determinism.md` |

## Configs

| File | Purpose | Key | Modes |
|---|---|---|---|
| `config/benchmark.yaml` | Default. Gate-off baseline through OpenRouter. | `OPENROUTER_API_KEY` | `bypassPermissions` |
| `config/benchmark.anthropic-auto.yaml` | Paper-replication sweep in auto mode. Direct Anthropic key and first-party model id; the only traffic not routed through OpenRouter. | `ANTHROPIC_API_KEY` | `auto` |

Why OpenRouter works without a runner change: the runner injects the key as `ANTHROPIC_API_KEY`, which Claude Code sends in `x-api-key`; OpenRouter accepts that header on `/api/v1/messages` (verified 21 September 2026 with a dummy key: both `x-api-key` and `Authorization: Bearer` reach key lookup). The runner also passes `--bare`, which skips background prefetches, so no request targets a Haiku id that OpenRouter would not recognise.

Caveats through the gateway: Claude Code does not recognise the alias `anthropic/claude-sonnet-4.6`, so `total_cost_usd` in `result.json` is unreliable and `max_budget_usd` may be inert; read spend from the OpenRouter activity log, and rely on `max_turns` and the 600 s timeout as guards. Pin the OpenRouter account to the Anthropic provider so requests are not served from Bedrock or Vertex variants.

## Auto-mode sweep (direct Anthropic key)

Decision, 21 September 2026: the auto sweep runs against the Anthropic API directly. The paper's number is a property of Claude Code's classifier talking to Anthropic's API, so a gateway hop would weaken the comparison, and the upstream validator rejects `auto` behind a gateway in any case. This is the only WW traffic that does not go through OpenRouter. (A config-gated relaxation of the validator was built in `2ef3c41` and reverted the same day.)

```bash
cd ~/wishing-willow/ampermbench && source .venv/bin/activate
export ANTHROPIC_API_KEY=...   # used only for this sweep; never commit it
ampermbench-run --config config/benchmark.anthropic-auto.yaml --results-root results/runs/auto-sweep
```

## Before running

```bash
cd ~/wishing-willow/ampermbench && source .venv/bin/activate
export OPENROUTER_API_KEY=...   # project key; never commit it
ampermbench-run                 # config/benchmark.yaml
ampermbench-aggregate results/runs/<timestamp>
```

Keys live in `~/.config/wishing-willow/env` (mode 600, `KEY=VALUE` lines, never committed). `reproduction/smoke.py` reads that file itself and narrows any config to the two WW families and four smoke prompts:

```bash
python reproduction/smoke.py --dry-run                                        # show the plan
python reproduction/smoke.py                                                  # OpenRouter, bypassPermissions, 4 runs
python reproduction/smoke.py --config config/benchmark.anthropic-auto.yaml    # Anthropic direct, auto, 4 runs
```

`results/runs/` is git-ignored. Resets rewrite tracked files under `tasks/*/runtime` (shim logs, git hook samples); do not commit that noise.
