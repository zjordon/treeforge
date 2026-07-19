"""pytest 共享 fixtures。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def bilibili_trace_path() -> Path:
    return REPO_ROOT / "examples" / "bilibili-upload.trace.json"


@pytest.fixture
def github_trace_path() -> Path:
    return REPO_ROOT / "examples" / "github-login.trace.json"


@pytest.fixture
def bilibili_trace_payload(bilibili_trace_path) -> dict:
    return json.loads(bilibili_trace_path.read_text(encoding="utf-8"))


@pytest.fixture
def github_trace_payload(github_trace_path) -> dict:
    return json.loads(github_trace_path.read_text(encoding="utf-8"))


@pytest.fixture
def tmp_output_dir(tmp_path) -> Path:
    return tmp_path / "skills"
