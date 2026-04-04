# AmPermBench: Measuring Authorization Ambiguity in Claude Code Auto Mode

AmPermBench is a Dockerized benchmark for evaluating how coding agents handle **authorization ambiguity** under different permission modes.

First-class support for **Claude Code** and **Codex CLI**.

## Paper

> **[Measuring Authorization Ambiguity in Claude Code Auto Mode](paper/paper.pdf)**

## Overview

- 4 task families, 128 fixed prompts total
- Deterministic reset scripts and task-local shims
- Unified Python runner with deterministic evaluators
- Confusion-matrix style metrics for `auto` mode
- Task-layer safety and completion metrics

The four task families:

| Task Family | Simulated Environment | Core Ambiguity |
|---|---|---|
| `clean-up-branches` | Git repository | Branch identity, local vs remote, destructive git ops |
| `cancel-jobs` | Slurm cluster | Job identity and ownership, multiple similarly-named jobs |
| `restart-services` | Kubernetes cluster | Service selection and environment boundary (dev vs staging vs prod) |
| `clean-up-artifacts` | AWS S3 storage | Storage prefix, ownership, deletion scope |

Each task family has a `4 × 4 × 2 = 32` prompt matrix across three axes:

- **S**: Authorization clarity (4 levels)
- **B**: Target-binding certainty (4 levels)
- **R**: Blast radius (2 levels)

## Quick Start

```bash
git clone https://github.com/yan5ui/cc-auto-mode-measurement.git
cd cc-auto-mode-measurement
conda create -n ampermbench python=3.11 -y
conda activate ampermbench
pip install -e .
bash scripts/bootstrap_and_run.sh
```

To use a host proxy, edit `config/benchmark.yaml`:

```yaml
docker:
  proxy:
    enabled: true
    http: "http://host.docker.internal:1145"
    https: "http://host.docker.internal:1145"
    add_host_gateway: true
```

## Repository Layout

```text
cc-auto-mode-measurement/
  config/
    benchmark.yaml          # Main config
  docker/
    base/Dockerfile         # Claude Code base image
    codex-base/Dockerfile   # Codex CLI base image
  scripts/
    bootstrap_and_run.sh    # One-click launcher
    container-entrypoint.sh # Container entrypoint
  src/ampermbench/
    runner.py               # Benchmark runner
    trace.py                # Action trace normalization
    aggregate.py            # Result aggregation
    evaluators/             # Deterministic evaluation logic
    tasks/                  # Task specs and prompt generation
  tasks/
    clean-up-branches/      # Git branch cleanup task
    cancel-jobs/            # Slurm job cancellation task
    restart-services/       # K8s service restart task
    clean-up-artifacts/     # S3 artifact cleanup task
  tests/                    # Test suite
  paper/
    paper.pdf               # Paper
```

## Runtime Model

For each benchmark run, the harness:

1. Materializes prompts, oracles, and initial-state assets
2. Resets the task environment to a deterministic initial state
3. Starts the corresponding task Docker image
4. Invokes the agent CLI with a fixed prompt
5. Collects stdout / stderr / debug log / action log / transcript / task runtime log
6. Snapshots the final state
7. Computes task metrics and classifier-style metrics
8. Writes `result.json`, `action_trace.jsonl`, and aggregate summaries

## Authentication

### Claude Code

Two authentication modes:

**API Key**:

```yaml
claude:
  auth_mode: "api_key"
  api_key: "${ANTHROPIC_API_KEY}"
```

**Host login** (reuse Pro subscription, etc.):

```yaml
claude:
  auth_mode: "host_login"
  host_credentials_path: "${HOME}/.claude/.credentials.json"
```

### Codex CLI

```yaml
provider: "codex"
codex:
  auth_mode: "api_key"
  api_key: "${OPENAI_API_KEY}"
```

## Metrics

### Primary: Classifier Metrics (`auto` mode)

| Metric | Meaning |
|---|---|
| TP | Unsafe action correctly blocked |
| FP | Safe action incorrectly blocked |
| TN | Safe action correctly allowed |
| FN | Unsafe action incorrectly allowed |
| FPR / FNR | False positive / false negative rate |
| Precision / Recall / F1 | Standard classifier metrics |
| DecisionCoverage | Fraction of runs with a valid classifier decision |

### Secondary: Task-Layer Metrics

- **UnsafeAllowRate**: Fraction of runs producing unauthorized side effects
- **SafeTaskSuccessRate**: Fraction of runs completing the goal without side effects
- **WrongTargetRate**: Fraction of runs acting on the wrong object under target ambiguity
- **OverScopeRate**: Fraction of runs exceeding the authorized scope
- **UnderCompletionRate**: Fraction of runs only partially completing the authorized action
- **RunToRunStability**: Agreement across repeated runs of the same item

## Results Layout

```text
results/runs/<timestamp>/
  <task>/<prompt_id>/<mode>/repeat-001/
    result.json
    action_trace.jsonl
    before_state.json / after_state.json
    claude.stdout.jsonl / claude.stderr.txt
  summary/
    aggregate.json
    aggregate.csv
```

## Requirements

- Linux + Docker
- Python 3.11+
- Claude Code or Codex CLI installed and authenticated

## Development

```bash
pip install -e '.[dev]'
python3 -m pytest -q tests
```

## Common Commands

```bash
# Regenerate benchmark assets
ampermbench-materialize

# Run the benchmark
ampermbench-run

# Aggregate existing results
ampermbench-aggregate results/runs/<timestamp>

# Reset a single task
ampermbench-reset-task cancel-jobs .
```

## Design Document

See [proposal.md](proposal.md) for detailed benchmark design rationale and metric definitions.

## License

MIT
