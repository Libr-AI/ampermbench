# AmPermBench

AmPermBench 是一个基于 Docker 的 benchmark，用于评测代码代理（coding agent）在权限模式下处理**授权歧义**（authorization ambiguity）问题的能力。

当前一等支持 **Claude Code** 和 **Codex CLI**。

## 论文

> **[Measuring Authorization Ambiguity in Claude Code Auto Mode](paper/paper.pdf)**

## 项目概览

- 4 个 task family，共 128 条固定 prompt
- 确定性 reset 脚本和 task-local shim
- 统一的 Python runner 和确定性 evaluator
- 面向 `auto` 的混淆矩阵式指标
- 面向任务层的安全性和完成度指标

四个 task family：

| Task Family | 模拟环境 | 核心歧义 |
|---|---|---|
| `clean-up-branches` | Git 仓库 | 分支身份、local vs remote、破坏性 git 操作 |
| `cancel-jobs` | Slurm 集群 | 任务身份与归属，多个同名 job |
| `restart-services` | Kubernetes 集群 | 服务选择与环境边界（dev vs staging vs prod） |
| `clean-up-artifacts` | AWS S3 存储 | 存储前缀、归属、删除范围 |

每个 task family 有 `4 × 4 × 2 = 32` 条 prompt，按三个轴展开：

- **S**：授权明确度（4 级）
- **B**：目标绑定确定性（4 级）
- **R**：blast radius（2 级）

## 快速开始

```bash
git clone https://github.com/yan5ui/cc-auto-mode-measurement.git
cd cc-auto-mode-measurement
conda create -n ampermbench python=3.11 -y
conda activate ampermbench
pip install -e .
bash scripts/bootstrap_and_run.sh
```

如果需要走宿主机代理，编辑 `config/benchmark.yaml`：

```yaml
docker:
  proxy:
    enabled: true
    http: "http://host.docker.internal:1145"
    https: "http://host.docker.internal:1145"
    add_host_gateway: true
```

## 仓库结构

```text
cc-auto-mode-measurement/
  config/
    benchmark.yaml          # 主配置文件
  docker/
    base/Dockerfile         # Claude Code 基础镜像
    codex-base/Dockerfile   # Codex CLI 基础镜像
  scripts/
    bootstrap_and_run.sh    # 一键启动脚本
    container-entrypoint.sh # 容器入口
  src/ampermbench/
    runner.py               # benchmark runner
    trace.py                # action trace 归一化
    aggregate.py            # 结果聚合
    evaluators/             # 确定性评测逻辑
    tasks/                  # task spec 与 prompt 生成
  tasks/
    clean-up-branches/      # Git 分支清理任务
    cancel-jobs/            # Slurm 任务取消任务
    restart-services/       # K8s 服务重启任务
    clean-up-artifacts/     # S3 制品清理任务
  tests/                    # 测试套件
  paper/
    paper.pdf               # 论文
```

## 运行模型

每个 benchmark run 的执行流程：

1. 物化 prompt、oracle 和初始状态资产
2. 将 task 环境 reset 到确定性初始状态
3. 启动对应 task 的 Docker 镜像
4. 用固定 prompt 调用 agent CLI
5. 收集 stdout / stderr / debug log / action log / transcript / task runtime log
6. 对最终状态做快照
7. 计算 task 指标和 classifier-style 指标
8. 写出 `result.json`、`action_trace.jsonl` 和聚合结果

## 认证配置

### Claude Code

支持两种认证模式：

**API Key**：

```yaml
claude:
  auth_mode: "api_key"
  api_key: "${ANTHROPIC_API_KEY}"
```

**宿主机登录态**（复用 Pro 订阅等）：

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

## 指标

### 主指标：classifier metrics（`auto` mode）

| 指标 | 含义 |
|---|---|
| TP | 不安全动作被正确阻止 |
| FP | 安全动作被错误阻止 |
| TN | 安全动作被正确放行 |
| FN | 不安全动作被错误放行 |
| FPR / FNR | 误报率 / 漏报率 |
| Precision / Recall / F1 | 精确率 / 召回率 / F1 |
| DecisionCoverage | 有效决策覆盖率 |

### 次级任务指标

- **UnsafeAllowRate**：执行了未授权副作用的 run 比例
- **SafeTaskSuccessRate**：无副作用完成目标的 run 比例
- **WrongTargetRate**：在目标歧义下操作了错误对象的比例
- **OverScopeRate**：超出授权范围的比例
- **UnderCompletionRate**：授权动作只完成了一部分的比例
- **RunToRunStability**：重复 run 一致性

## 结果目录

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

## 环境要求

- Linux + Docker
- Python 3.11+
- Claude Code 或 Codex CLI 已安装并登录

## 开发与测试

```bash
pip install -e '.[dev]'
python3 -m pytest -q tests
```

## 常用命令

```bash
# 重新生成 benchmark 资产
ampermbench-materialize

# 运行 benchmark
ampermbench-run

# 聚合已有结果
ampermbench-aggregate results/runs/<timestamp>

# 重置单个 task
ampermbench-reset-task cancel-jobs .
```

## 设计文档

详细的 benchmark 设计动机和指标定义见 [proposal.md](proposal.md)。

## License

MIT
