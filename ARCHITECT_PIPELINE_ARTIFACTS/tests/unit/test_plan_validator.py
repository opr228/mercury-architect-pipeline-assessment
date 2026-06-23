"""Plan validator tests."""

from pathlib import Path

from modules.plan_validator import parse_plan_frontmatter, validate_plan

VALID_PLAN = """---
task_id: test-task-v1
scope_files:
  - modules/foo.py
  - tests/unit/test_foo.py
out_of_scope:
  - modules/bar.py
acceptance:
  - pytest tests/unit/test_foo.py -q
phases:
  - id: implement
    max_diff_lines: 80
---

# Implementation plan

## Scope
Implement foo.

## Risks
Low.

## Verified files
- modules/foo.py

""" + ("detail line\n" * 25)


def test_parse_plan_frontmatter():
    data, err = parse_plan_frontmatter(VALID_PLAN)
    assert err is None
    assert data is not None
    assert data["task_id"] == "test-task-v1"
    assert len(data["scope_files"]) == 2


def test_validate_plan_ok(tmp_path: Path):
    p = tmp_path / "PLAN_test.md"
    p.write_text(VALID_PLAN, encoding="utf-8")
    result = validate_plan(p)
    assert result.valid is True
    assert result.stub is False


def test_validate_plan_missing_frontmatter(tmp_path: Path):
    p = tmp_path / "PLAN_bad.md"
    p.write_text("# no frontmatter\n", encoding="utf-8")
    result = validate_plan(p)
    assert result.valid is False
    assert result.stub is True
