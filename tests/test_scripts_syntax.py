"""Smoke-test: every .py file in the repo parses cleanly.

Catches typos / accidental breakage in scripts the test suite can't easily
run end-to-end (Pi-hardware code, CLI entry points, etc.) before push.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _python_files() -> list[Path]:
    skip_parts = {".venv", "venv", ".git", "build", "dist", "__pycache__"}
    out: list[Path] = []
    for p in REPO.rglob("*.py"):
        if any(part in skip_parts for part in p.parts):
            continue
        out.append(p)
    return sorted(out)


@pytest.mark.parametrize("path", _python_files(),
                         ids=lambda p: str(p.relative_to(REPO)))
def test_parses(path):
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
