from __future__ import annotations

from ampermbench.tasks import artifacts, branches, cancel_jobs, restart_services


TASK_MODULES = {
    "clean-up-branches": branches,
    "cancel-jobs": cancel_jobs,
    "restart-services": restart_services,
    "clean-up-artifacts": artifacts,
}

SCOPE_KEYWORDS = (" local", " remote", " origin", " dev ", " prod ", "prod-", "dev-")


def test_prompt_texts_are_unique_and_b_slots_are_scope_neutral():
    for task, module in TASK_MODULES.items():
        prompts = module.prompts()
        texts = [prompt["prompt"] for prompt in prompts]
        assert len(prompts) == 32
        assert len(set(texts)) == 32, task

        for slot_name, slot in module.B_SLOTS.items():
            for value in slot.values():
                lowered = value.lower()
                assert not any(keyword in lowered for keyword in SCOPE_KEYWORDS), (task, slot_name, value)
