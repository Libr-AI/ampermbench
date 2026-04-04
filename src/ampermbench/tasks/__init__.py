from .artifacts import TASK_SPEC as CLEANUP_ARTIFACTS
from .branches import TASK_SPEC as CLEANUP_BRANCHES
from .cancel_jobs import TASK_SPEC as CANCEL_JOBS
from .restart_services import TASK_SPEC as RESTART_SERVICES
from .common import TaskSpec

TASK_SPECS: dict[str, TaskSpec] = {
    CLEANUP_BRANCHES.slug: CLEANUP_BRANCHES,
    CANCEL_JOBS.slug: CANCEL_JOBS,
    RESTART_SERVICES.slug: RESTART_SERVICES,
    CLEANUP_ARTIFACTS.slug: CLEANUP_ARTIFACTS,
}

__all__ = ["TASK_SPECS", "TaskSpec"]

