from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


PromptRecord = dict[str, Any]
OracleRecord = dict[str, Any]


@dataclass(frozen=True)
class TaskSpec:
    slug: str
    prompt_file: str
    oracle_file: str
    state_file: str
    log_file: str
    workdir: str
    prompts_factory: Callable[[], list[PromptRecord]]
    oracle_factory: Callable[[], dict[str, OracleRecord]]
    initial_state_factory: Callable[[], dict[str, Any] | list[dict[str, Any]]]
    shim_wrapper_names: tuple[str, ...]

    def task_dir(self, repo_root: Path) -> Path:
        return repo_root / "tasks" / self.slug

    def runtime_dir(self, repo_root: Path) -> Path:
        return self.task_dir(repo_root) / "runtime"

    def harness_dir(self, repo_root: Path) -> Path:
        return self.task_dir(repo_root) / "harness"


def build_prompt_records(
    prefix: str,
    s_templates: dict[str, str],
    b_slots: dict[str, dict[str, str]],
    r_slots: dict[str, dict[str, str]],
) -> list[PromptRecord]:
    records: list[PromptRecord] = []
    for s_axis, template in s_templates.items():
        for b_axis, b_slot in b_slots.items():
            for r_axis, r_slot in r_slots.items():
                text = template.format(
                    B_ref=b_slot["ref"].format(**r_slot),
                    B_ref_cap=b_slot["ref_cap"].format(**r_slot),
                    R_boundary=r_slot["boundary"],
                    **r_slot,
                )
                records.append(
                    {
                        "prompt_id": f"{prefix}-{s_axis}-{b_axis}-{r_axis}",
                        "s_axis": s_axis,
                        "b_axis": b_axis,
                        "r_axis": r_axis,
                        "prompt": text,
                    }
                )
    return records
