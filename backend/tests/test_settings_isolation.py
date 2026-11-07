"""D-18: backend/tests 里所有 Settings(...) 直建点必须显式传 _env_file。

pydantic-settings 默认会读进程当前目录的 ``.env``；离线套件必须与本机配置
无关（AGENTS.md「测试隔离」）。因此 D-18 要求**仅 backend/tests** 的每个
``Settings(...)`` 调用都显式传 ``_env_file``：

* ``None`` 是离线约定（不读 .env）；
* 允许任意值（例如显式路径）——本守卫只钉「关键字存在」，不替业务判断。

范围：只扫 ``backend/tests/**/*.py``（含子目录；生产代码不在此守卫面）。用 AST 而不是
文本正则：``Settings(`` 出现在字符串 / 注释里不会误报。

工厂 seam：pydantic v2 的 ``dataclass_transform`` 让 mypy 看不到运行时可接受的
``_env_file``（``pydantic-settings`` 2.16.1，已 inspect 核实），而已注解函数体
又不允许 ``type: ignore`` / ``noqa``。因此 conftest 提供**未注解**的
``make_isolated_settings(**overrides)`` 作为唯一 seam（内部仍是显式
``Settings(_env_file=None, ...)``）；本守卫的机械面不因此放松——每个**直接**
``Settings(...)`` 调用仍必须显式带 ``_env_file``。

已知限制：AST 只认 ``Settings(...)`` / ``<obj>.Settings(...)`` 的 Name/Attribute
调用；``from backend.config import Settings as S`` 这类**别名调用**不在检测面
（当前仓库 0 处；新增别名会让本守卫漏检，需同步扩展）。
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = REPO_ROOT / "backend" / "tests"


def _is_settings_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "Settings"
    if isinstance(func, ast.Attribute):
        return func.attr == "Settings"
    return False


def _missing_env_file_calls(sources: dict[str, str]) -> dict[str, list[int]]:
    """纯函数：{repo 相对路径: 源码} -> {路径: 缺 _env_file 的行号}。"""
    violations: dict[str, list[int]] = {}
    for rel, source in sources.items():
        tree = ast.parse(source)
        lines = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and _is_settings_call(node)
            and "_env_file" not in {kw.arg for kw in node.keywords}
        ]
        if lines:
            violations[rel] = sorted(lines)
    return violations


def _repo_sources() -> dict[str, str]:
    return {
        path.relative_to(REPO_ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(TESTS_DIR.rglob("*.py"))
    }


def _settings_call_count(sources: dict[str, str]) -> int:
    total = 0
    for source in sources.values():
        total += sum(
            1
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call) and _is_settings_call(node)
        )
    return total


def test_settings_call_without_env_file_is_flagged() -> None:
    """合成缺参源码必须被检出（否则守卫是空转）。"""
    sources = {
        "backend/tests/test_synthetic.py": (
            "from backend.config import Settings\n"
            "s = Settings(use_mock_llm=True)\n"
        )
    }
    assert _missing_env_file_calls(sources) == {"backend/tests/test_synthetic.py": [2]}


def test_settings_call_with_env_file_is_accepted() -> None:
    """显式 _env_file（None 或任意路径）都通过；含 **base 形态。"""
    sources = {
        "backend/tests/test_synthetic.py": (
            "from backend.config import Settings\n"
            "a = Settings(_env_file=None, use_mock_llm=True)\n"
            "b = Settings(_env_file='/tmp/settings', use_mock_llm=True)\n"
            "c = Settings(_env_file=None, **{'use_mock_llm': True})\n"
            "d = Settings()\n"
        )
    }
    assert _missing_env_file_calls(sources) == {"backend/tests/test_synthetic.py": [5]}


def test_settings_call_with_only_dict_unpack_is_still_flagged() -> None:
    """fail-closed：``Settings(**base)`` 没有字面 ``_env_file`` 关键字 → 报红。

    这是刻意的（宁可误报不漏检）：字典解包在 AST 层无法证明键里含 ``_env_file``；
    工厂 seam 内部同样写成显式的 ``Settings(_env_file=None, ...)``。
    """
    sources = {
        "backend/tests/test_synthetic.py": (
            "from backend.config import Settings\n"
            "base = {\"use_mock_llm\": True}\n"
            "s = Settings(**base)\n"
        )
    }
    assert _missing_env_file_calls(sources) == {"backend/tests/test_synthetic.py": [3]}


def test_settings_scan_finds_calls_it_walks() -> None:
    """扫描到的 Settings 调用数 > 0（防空转），并核对计数口径。"""
    sources = {
        "backend/tests/test_a.py": "Settings()\n",
        "backend/tests/test_b.py": "Settings(_env_file=None)\n",
    }
    assert _settings_call_count(sources) == 2


def test_repo_tests_have_no_missing_env_file() -> None:
    """真实仓库：backend/tests 全部 Settings(...) 都显式传了 _env_file。"""
    sources = _repo_sources()
    violations = _missing_env_file_calls(sources)
    assert _settings_call_count(sources) > 0
    assert not violations, (
        "backend/tests 的 Settings(...) 缺少 _env_file（D-18）："
        + "; ".join(f"{rel}:{lines}" for rel, lines in sorted(violations.items()))
    )
