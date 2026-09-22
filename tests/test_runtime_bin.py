from __future__ import annotations

import os

from ampermbench.runner import _prepare_runtime_root, materialize
from ampermbench.tasks import TASK_SPECS


def test_per_run_runtime_gets_shim_wrappers(repo_root, tmp_path):
    """Regression: the per-run runtime mounted at /bench must contain the CLI shims."""
    materialize(repo_root, ["clean-up-artifacts", "clean-up-branches", "cancel-jobs", "restart-services"])
    expected = {
        "clean-up-artifacts": {"aws"},
        "clean-up-branches": {"git"},
        "cancel-jobs": {"scancel", "squeue", "scontrol"},
        "restart-services": {"kubectl"},
    }
    for task, names in expected.items():
        runtime_root = tmp_path / task / "bench-runtime"
        _prepare_runtime_root(TASK_SPECS[task], repo_root, runtime_root)
        present = {p.name for p in (runtime_root / "bin").iterdir()}
        assert names <= present, (task, present)
        for name in names:
            assert os.access(runtime_root / "bin" / name, os.X_OK), (task, name)
