from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any


ENV_VAR_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


def expand_env(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    if isinstance(value, str):
        return ENV_VAR_PATTERN.sub(lambda match: os.environ.get(match.group(1), ""), value)
    return value


def json_dumps(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_text(path: Path, content: str, executable: bool = False) -> None:
    ensure_parent(path)
    path.write_text(content, encoding="utf-8")
    if executable:
        path.chmod(0o755)


def write_json(path: Path, data: Any) -> None:
    write_text(path, json_dumps(data))


def remove_tree(path: Path) -> None:
    if not path.exists():
        return
    if path.is_file() or path.is_symlink():
        path.unlink()
        return
    shutil.rmtree(path)


def repo_root_from(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "pyproject.toml").exists() and (candidate / "tasks").exists() and (candidate / "src").exists():
            return candidate
    raise FileNotFoundError("Could not locate AmPermBench repository root")
