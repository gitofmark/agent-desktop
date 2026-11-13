"""Step 19: the compact snapshot is at least 50% smaller than the raw dump."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from conftest import ALL_FIXTURES, ROOT, fixture_path

sys.path.insert(0, ROOT)

from tools.token_count import measure  # noqa: E402
from snapshot_parser.render import RenderOptions, render_snapshot  # noqa: E402

from conftest import load  # noqa: E402

TARGET = 0.50


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_compact_snapshot_is_at_least_half_the_size(name):
    with open(fixture_path(name), "r", encoding="utf-8") as handle:
        raw = handle.read()
    compact = render_snapshot(load(name))
    data = measure(raw, compact)
    assert data["char_reduction"] >= TARGET, data
    assert data["token_reduction"] >= TARGET, data


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_compact_snapshot_is_never_longer(name):
    with open(fixture_path(name), "r", encoding="utf-8") as handle:
        raw_lines = len(handle.read().splitlines())
    compact_lines = len(render_snapshot(load(name)).splitlines())
    assert compact_lines < raw_lines


def test_token_count_tool_exits_zero():
    result = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "token_count.py")],
        capture_output=True, text=True, cwd=ROOT, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout


def test_token_count_tool_fails_loudly_when_the_target_is_impossible():
    result = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "token_count.py"),
         "--target", "0.999"],
        capture_output=True, text=True, cwd=ROOT, timeout=120,
    )
    assert result.returncode == 1
    assert "FAIL" in result.stderr


def test_token_count_json_shape():
    import json

    result = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "token_count.py"), "--json"],
        capture_output=True, text=True, cwd=ROOT, timeout=120,
    )
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    # every capture in tests/fixtures is checked, including the second-app ones
    assert len(payload["results"]) >= len(ALL_FIXTURES)
    for row in payload["results"]:
        assert row["compact_chars"] < row["raw_chars"]
