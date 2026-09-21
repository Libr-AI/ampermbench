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
| Runner code | one documented deviation: `src/ampermbench/config.py` gains `claude.allow_auto_via_gateway` (default `false`), which lets `validate_config` accept `auto` behind an Anthropic-format gateway and a gateway alias of a supported model. 18 lines added, 3 changed; covered by `tests/test_gateway_auto.py` (4 tests). Everything else identical to upstream. |
| Reset determinism | all four tasks identical across 3 resets, see `reset-determinism.md` |

## Configs

| File | Purpose | Key | Modes |
|---|---|---|---|
| `config/benchmark.yaml` | Default. Gate-off baseline through OpenRouter. | `OPENROUTER_API_KEY` | `bypassPermissions` |
| `config/benchmark.openrouter-auto.yaml` | Empirical test: does Claude Code 2.1.86 enter auto mode through OpenRouter? Preflight plus one run (`CA-S0-B0-R0`). Sets `allow_auto_via_gateway: true`. | `OPENROUTER_API_KEY` | `auto` |
| `config/benchmark.anthropic-auto.yaml` | Fallback for the paper-replication sweep if the test above fails: direct Anthropic key, first-party model id, upstream validator rules apply unchanged. | `ANTHROPIC_API_KEY` | `auto` |

Why OpenRouter works without a runner change: the runner injects the key as `ANTHROPIC_API_KEY`, which Claude Code sends in `x-api-key`; OpenRouter accepts that header on `/api/v1/messages` (verified 21 September 2026 with a dummy key: both `x-api-key` and `Authorization: Bearer` reach key lookup). The runner also passes `--bare`, which skips background prefetches, so no request targets a Haiku id that OpenRouter would not recognise.

Caveats through the gateway: Claude Code does not recognise the alias `anthropic/claude-sonnet-4.6`, so `total_cost_usd` in `result.json` is unreliable and `max_budget_usd` may be inert; read spend from the OpenRouter activity log, and rely on `max_turns` and the 600 s timeout as guards. Pin the OpenRouter account to the Anthropic provider so requests are not served from Bedrock or Vertex variants.

## Auto-mode test through OpenRouter (do this before the sweeps)

```bash
cd ~/wishing-willow/ampermbench && source .venv/bin/activate
export OPENROUTER_API_KEY=...
ampermbench-run --config config/benchmark.openrouter-auto.yaml --results-root results/runs/openrouter-auto-test
```

Read the outcome in this order:

1. If the command aborts with `Requested auto mode, but Claude Code fell back to default during preflight: ...`, auto mode is not reachable through OpenRouter for this CLI build. Copy the quoted debug lines into the report and switch the paper sweep to `config/benchmark.anthropic-auto.yaml`.
2. If it runs, open `results/runs/openrouter-auto-test/clean-up-artifacts/CA-S0-B0-R0/auto/repeat-001/result.json` and check that `auto_mode_fallback_lines` is empty, then `action_trace.jsonl` for `gate_decision` values of `allowed` or `blocked` (not only `not_applicable`).
3. Grep the run's debug file (under the run directory) for `cannot determine the safety`, `not_found`, or a requested model id that is not `anthropic/...`: a classifier whose own model request fails through the gateway means auto mode is nominally on but never decides. That counts as not reproducible.
4. Check the OpenRouter activity log: every request should show provider Anthropic and the expected model. Note the spend; `total_cost_usd` in `result.json` is not reliable through the gateway.

If all four pass, add `"auto"` to `permission_modes` in `config/benchmark.yaml` together with `allow_auto_via_gateway: true` and run the day-3 sweep through OpenRouter; record the deviation in the report.

## Before running

```bash
cd ~/wishing-willow/ampermbench && source .venv/bin/activate
export OPENROUTER_API_KEY=...   # project key; never commit it
ampermbench-run                 # config/benchmark.yaml
ampermbench-aggregate results/runs/<timestamp>
```

`results/runs/` is git-ignored. Resets rewrite tracked files under `tasks/*/runtime` (shim logs, git hook samples); do not commit that noise.
