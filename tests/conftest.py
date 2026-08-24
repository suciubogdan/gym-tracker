from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from gym_tracker.storage.repository import ProjectRepository


@pytest.fixture
def repository(tmp_path: Path) -> ProjectRepository:
    source = Path(__file__).parents[1]
    for directory in ("plans", "config"):
        shutil.copytree(source / directory, tmp_path / directory)
    (tmp_path / "data" / "imported").mkdir(parents=True)
    (tmp_path / "data" / "sync").mkdir(parents=True)
    return ProjectRepository(tmp_path)
