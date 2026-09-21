# Reset determinism

Three consecutive ampermbench-reset-task runs per task, 21 September 2026, agent-redteaming-worker-1.

| Task | Reset 1 | Reset 2 | Reset 3 | Deterministic |
|---|---|---|---|---|
| clean-up-artifacts | 7c4e0f2789647325 | 7c4e0f2789647325 | 7c4e0f2789647325 | yes |
| clean-up-branches | 7e1d9e33a49b0e3f | 7e1d9e33a49b0e3f | 7e1d9e33a49b0e3f | yes |
| cancel-jobs | a5a82d4126408336 | a5a82d4126408336 | a5a82d4126408336 | yes |
| restart-services | e31e2f9aeb424d86 | e31e2f9aeb424d86 | e31e2f9aeb424d86 | yes |

Hash basis: clean-up-artifacts = objects.json; clean-up-branches = refs of repo and origin.git plus HEAD and porcelain status; others = every runtime file outside logs/.
