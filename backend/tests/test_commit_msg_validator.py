"""Guard for the commit-msg hook's validator (issue #114).

``scripts/validate_commit_msg.py`` is the commit-msg gate, but the hook only
fires on machines that ran ``pre-commit install`` — CI never runs git hooks,
and a fresh clone that skipped the install step commits with no gate at all.
So the *rules* get their mechanical home here: this suite imports the
validator directly and pins its four rules (non-empty, subject <= 72 chars,
conventional prefix, ticket-number reminder) plus the Merge exemption.

The seam is the pure ``evaluate(message)`` function: string in, verdict out.
Changing the rules here means a CI-red decision, not a silent weakening of the
gate that only shows up the next time someone commits on a configured machine.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_VALIDATOR_PATH = Path(__file__).resolve().parents[2] / "scripts" / "validate_commit_msg.py"

_spec = importlib.util.spec_from_file_location("validate_commit_msg", _VALIDATOR_PATH)
assert _spec is not None and _spec.loader is not None  # 路径写错时立即失败，而非 AttributeError
_validator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_validator)


def test_validator_file_exists() -> None:
    assert _VALIDATOR_PATH.is_file(), f"validator 不在预期位置：{_VALIDATOR_PATH}"


def test_accepts_conventional_subjects() -> None:
    ok, errors, _ = _validator.evaluate("docs(agents): 对齐上游\n")
    assert ok and not errors


def test_accepts_scoped_subject_with_ticket_in_body() -> None:
    ok, errors, remind = _validator.evaluate("feat(precommit): 双钩子\n\nCloses #114\n")
    assert ok and not errors and not remind


def test_rejects_missing_prefix() -> None:
    ok, errors, _ = _validator.evaluate("wip stuff\n")
    assert not ok
    assert any("conventional" in e for e in errors)


def test_rejects_overlong_subject() -> None:
    ok, errors, _ = _validator.evaluate("feat: " + "x" * 80 + "\n")
    assert not ok
    assert any("72" in e for e in errors)


def test_rejects_empty_message() -> None:
    ok, _, _ = _validator.evaluate("\n")
    assert not ok


def test_merge_commits_are_exempt() -> None:
    # 不 squash 是 workflow 步 6 的既定做法；GitHub 生成的 Merge 主题既超 72 字符
    # 又无 conventional 前缀，一刀切会拦死每一次合并。
    ok, errors, remind = _validator.evaluate(
        "Merge pull request #111 from renjianguojinqianfan/fix/tool-node-reentry\n"
    )
    assert ok and not errors and not remind


def test_non_docs_without_ticket_only_reminds() -> None:
    # docs 小修无票号直推是既定惯例（issue-tracker.md Release conventions）；
    # 非 docs 类提醒但不阻断——拦死只会逼所有人 --no-verify。
    ok, errors, remind = _validator.evaluate("feat: something\n")
    assert ok and not errors and remind


def test_docs_without_ticket_does_not_remind() -> None:
    ok, _, remind = _validator.evaluate("docs: 小修无票号\n")
    assert ok and not remind
