"""#261: DESIGN.md 镜 ↔ index.css @theme 的守卫矩阵，固化成离线断言。

``scripts/check_design_tokens.py`` 守的是「机器可读镜像不许说谎」：DESIGN.md 的
frontmatter 与 ``frontend/src/index.css`` 的 ``@theme`` 逐项同值。此前这条一致性只靠
人眼对照，漂了 CI 不报。本文件把红 / 绿两面都变成可复现用例：

* 绿：仓库现状必须通过（守卫自己的真实扫描，不是手跑一次写在 PR 里）；
* 红：颜色 / 圆角 / 排版任一维漂移、任意一侧多出未声明 token、frontmatter 坏掉，
  都必须变红并点名到具体 token（失败信息是给人看的，含糊等于没守）。

**造漂移的方式**：解析仓库当前两个文件得到 dict，在**内存里**改字段再比对——不替换
文件里的具体色值。合法改设计值时测试自动跟着走，不会因为「fixture 里找不到旧值」假红；
只有「解析失败要失败闭合」这类场景才落临时文件。

接线也断言一遍：钩子被删、CI 步骤被删、AGENTS.md 验证入口漏记——守卫都会退化成
「没人跑的手动脚本」，所以它们和守卫本身一样需要机械保护。

加载脚本按路径（不是 import）：它住在 ``scripts/``、在 ``backend`` 包外，这样测的
就是门禁命令跑的同一个文件（与 ``test_no_machine_paths.py`` 同先例）。
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_design_tokens.py"
PRE_COMMIT = REPO_ROOT / ".pre-commit-config.yaml"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
AGENTS_MD = REPO_ROOT / "AGENTS.md"

_spec = importlib.util.spec_from_file_location("check_design_tokens", SCRIPT)
assert _spec is not None and _spec.loader is not None
gate = importlib.util.module_from_spec(_spec)
sys.modules["check_design_tokens"] = gate
_spec.loader.exec_module(gate)


def _mirror() -> tuple[dict[str, Any], dict[str, str]]:
    """仓库当前两侧的解析结果（值随仓库走，测试只钉结构关系）。"""
    design = gate.parse_design((REPO_ROOT / gate.DESIGN_MD).read_text(encoding="utf-8"))
    theme = gate.parse_theme((REPO_ROOT / gate.INDEX_CSS).read_text(encoding="utf-8"))
    return design, theme


def _assert_drift(drifts: list[str], *needles: str) -> None:
    matched = [drift for drift in drifts if all(needle in drift for needle in needles)]
    assert matched, f"期望漂移里有同时含 {needles} 的一条，实测：{drifts}"


def _write_root(tmp_path: Path, *, design: str | None = None, css: str | None = None) -> Path:
    """临时根上放一对镜像文件；不给的那侧用仓库现状（供「解析失败」用例）。"""
    (tmp_path / gate.DESIGN_MD).write_text(
        design if design is not None else (REPO_ROOT / gate.DESIGN_MD).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    css_path = tmp_path / gate.INDEX_CSS
    css_path.parent.mkdir(parents=True, exist_ok=True)
    css_path.write_text(
        css if css is not None else (REPO_ROOT / gate.INDEX_CSS).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return tmp_path


# ── 绿：仓库现状与解析契约 ──────────────────────────────────────────────────


def test_repo_mirror_is_synced() -> None:
    """守卫自己的真实扫描：CI 上每次跑，镜像漂移当次变红。"""
    assert gate.check(REPO_ROOT) == []


def test_parsed_mirror_compares_clean() -> None:
    design, theme = _mirror()
    assert gate.compare(design, theme) == []


def test_cli_exits_zero_on_the_real_tree(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI 是门禁命令本体：exit 0 + 一行人读得懂的确认。"""
    assert gate.main() == 0
    assert "设计 token 镜像一致" in capsys.readouterr().out


def test_hex_alpha_suffix_is_the_same_color() -> None:
    """``#RRGGBBAA`` 与 ``#RRGGBB``（alpha 1）是同一个颜色，不该假红。"""
    design, theme = _mirror()
    design["colors"]["page-ink"] = "#0A0A0BFF"
    assert gate.compare(design, theme) == []


def test_modern_space_separated_rgb_is_the_same_color() -> None:
    """``rgb(255 255 255 / .07)`` 与 ``rgba(255, 255, 255, .07)`` 等价。"""
    design, theme = _mirror()
    theme["--color-rule"] = "rgb(255 255 255 / 0.07)"
    assert gate.compare(design, theme) == []


def test_last_theme_declaration_without_semicolon_is_parsed() -> None:
    """CSS 最后一条声明可以不带分号；漏掉它就会漏检。"""
    assert gate.parse_theme("@theme {\n  --color-rule: #123456\n}") == {"--color-rule": "#123456"}


def test_all_theme_blocks_are_merged() -> None:
    """Tailwind v4 允许多个 @theme 块（如 ``@theme inline``）；后面的覆盖必须可见。"""
    css = (REPO_ROOT / gate.INDEX_CSS).read_text(encoding="utf-8")
    theme = gate.parse_theme(css + "\n@theme inline {\n  --color-surface-page: #0b0b0b;\n}\n")
    assert theme["--color-surface-page"] == "#0b0b0b"
    design, _ = _mirror()
    _assert_drift(gate.compare(design, theme), "colors.page-ink", "--color-surface-page")


# ── 红：DESIGN.md 一侧漂移 ──────────────────────────────────────────────────


def test_design_color_value_drift_is_red() -> None:
    design, theme = _mirror()
    design["colors"]["page-ink"] = "#0B0B0B"
    _assert_drift(gate.compare(design, theme), "colors.page-ink", "--color-surface-page")


def test_design_missing_color_is_red() -> None:
    design, theme = _mirror()
    del design["colors"]["signal-now"]
    _assert_drift(gate.compare(design, theme), "colors.signal-now", "缺这个颜色")


def test_design_extra_color_is_red() -> None:
    design, theme = _mirror()
    design["colors"]["state-info"] = "#123456"
    _assert_drift(gate.compare(design, theme), "colors.state-info", "未映射")


# ── 红：index.css 一侧漂移 ──────────────────────────────────────────────────


def test_css_color_value_drift_is_red() -> None:
    design, theme = _mirror()
    theme["--color-surface-page"] = "#0b0b0b"
    _assert_drift(gate.compare(design, theme), "colors.page-ink", "--color-surface-page")


def test_css_undeclared_color_is_red() -> None:
    design, theme = _mirror()
    theme["--color-legacy"] = "#123456"
    _assert_drift(gate.compare(design, theme), "--color-legacy", "未声明")


def test_radius_drift_is_red() -> None:
    design, theme = _mirror()
    theme["--radius-control"] = "7px"
    _assert_drift(gate.compare(design, theme), "rounded.control", "--radius-control")


def test_typography_weight_drift_is_red() -> None:
    design, theme = _mirror()
    theme["--text-title--font-weight"] = "500"
    _assert_drift(gate.compare(design, theme), "typography.title.fontWeight", "600", "500")


def test_label_role_is_compared_against_meta_token() -> None:
    """DESIGN 的 ``label`` 与 CSS 的 ``meta`` 是同一组：meta 改了必须红。"""
    design, theme = _mirror()
    theme["--text-meta"] = "11px"
    _assert_drift(gate.compare(design, theme), "typography.label.fontSize", "--text-meta")


def test_missing_font_stack_is_red() -> None:
    design, theme = _mirror()
    del theme["--font-mono"]
    _assert_drift(gate.compare(design, theme), "typography.machine.fontFamily", "--font-mono")


def test_extra_font_stack_is_red() -> None:
    design, theme = _mirror()
    theme["--font-display"] = '"Another", sans-serif'
    _assert_drift(gate.compare(design, theme), "--font-display", "未声明的字体栈")


def test_typography_size_drift_is_red() -> None:
    design, theme = _mirror()
    theme["--text-body"] = "14px"
    _assert_drift(gate.compare(design, theme), "typography.body.fontSize", "--text-body")


def test_typography_line_height_drift_is_red() -> None:
    design, theme = _mirror()
    theme["--text-code--line-height"] = "1.8"
    _assert_drift(gate.compare(design, theme), "typography.code.lineHeight", "--text-code--line-height")


def test_typography_letter_spacing_drift_is_red() -> None:
    design, theme = _mirror()
    theme["--text-title--letter-spacing"] = "0em"
    _assert_drift(gate.compare(design, theme), "typography.title.letterSpacing", "--text-title--letter-spacing")


def test_extra_hyphenated_text_role_is_red() -> None:
    """``--text-body-sm`` 这类连字符角色也算「多出来的排版组」，不能被正则漏掉。"""
    design, theme = _mirror()
    theme["--text-body-sm"] = "12px"
    _assert_drift(gate.compare(design, theme), "--text-body-sm", "未声明的排版组")


# ── 红：解析失败也失败闭合 ──────────────────────────────────────────────────


def test_broken_frontmatter_fails_closed(tmp_path: Path) -> None:
    root = _write_root(tmp_path, design="# 没有 frontmatter\n")
    _assert_drift(gate.check(root), "DESIGN.md 解析失败")


def test_invalid_yaml_fails_closed_with_a_clean_message(tmp_path: Path) -> None:
    """YAML 语法错也不能抛 traceback：按漂移报出来，才是门禁该有的失败方式。"""
    root = _write_root(tmp_path, design="---\ncolors: [unclosed\n---\n")
    _assert_drift(gate.check(root), "DESIGN.md 解析失败", "YAML")


def test_missing_theme_block_fails_closed(tmp_path: Path) -> None:
    root = _write_root(tmp_path, css="/* 没有 @theme */\n")
    _assert_drift(gate.check(root), "index.css 解析失败")


# ── 接线：守卫必须被真的跑起来 ─────────────────────────────────────────────


def test_gate_is_wired_into_pre_commit_ci_and_agents_md() -> None:
    config = yaml.safe_load(PRE_COMMIT.read_text(encoding="utf-8"))
    hooks = [hook for repo in config["repos"] for hook in repo["hooks"]]
    hook = next((item for item in hooks if item["id"] == "design-token-mirror"), None)
    assert hook is not None, "pre-commit 缺 design-token-mirror 钩子（守卫会退化成手动脚本）"
    assert ".venv311/Scripts/python.exe" in hook["entry"]
    assert "scripts/check_design_tokens.py" in hook["entry"]
    assert hook["language"] == "system"
    assert hook["pass_filenames"] is False
    pattern = re.compile(hook["files"])
    assert pattern.search(gate.DESIGN_MD), "钩子 files 正则没覆盖 DESIGN.md"
    assert pattern.search(gate.INDEX_CSS), "钩子 files 正则没覆盖 frontend/src/index.css"

    ci = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))
    commands = "\n".join(
        step.get("run", "")
        for step in ci["jobs"]["backend-test"]["steps"]
        if isinstance(step, dict)
    )
    assert "scripts/check_design_tokens.py" in commands, "ci.yml backend-test 没跑设计 token 守卫"

    # 锚「验证入口」里的原样命令（反斜杠 + .venv311）：任务路由行那句是正斜杠 `python`，
    # 子串会误命中，删掉验证入口那行测试仍绿——这正是第一版评审抓到的假绿。
    agents_text = AGENTS_MD.read_text(encoding="utf-8")
    assert ".venv311\\Scripts\\python.exe scripts\\check_design_tokens.py" in agents_text, (
        "AGENTS.md「验证入口」没记守卫命令"
    )
