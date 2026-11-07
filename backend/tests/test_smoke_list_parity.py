"""C23 / D-14: Windows 冒烟清单双址一致 + Windows-only 标记文件必须进清单。

两份清单（AGENTS.md「验证入口」的本地命令 + ci.yml 的 backend-windows-smoke
job）此前只靠「两边保持同一清单」的文档约定维持一致，没有任何机械守卫；
#254 的三个兄弟 PR 各往两处同点追加测试文件时，合并冲突正落在这两行，解冲突
靠人肉逐行核对——丢一行 = Windows 冒烟面静默缩水，而 CI 仍是绿的。

两条腿都是纯文本解析，毫秒级、平台无关：

1. 双址集合逐条相同（原有三条断言，不放宽）；
2. D-14 标记扫描：backend/tests/test_*.py 里出现 os.name / sys.platform /
   WinDLL / taskkill 任一标记时，该文件必须已在清单，除非命中显式白名单
   （路径 -> 理由）。

**盲区（有意写明，不宣称覆盖）**：扫描只看 backend/tests/test_*.py 的文本。
test_test_isolation.py 与 test_server_lock.py 自身不含这四类标记——它们的
Windows 分支分别在 conftest.py:98-105（os.name=="nt" 临时根探测）与
backend/server_lock.py:129-162（ctypes/WinDLL）；这两个文件进清单靠 D-13 的
单独 PR + 3 连跑证据，标记扫描覆盖不到它们，也不该被当成它们已覆盖的证明。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_MD = REPO_ROOT / "AGENTS.md"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# 只匹配 pytest 命令行里的实参形态（backend/tests/<name>.py）；
# ci.yml job 注释与 AGENTS.md 正文里提到的文件名都不带这个前缀，天然不会误收。
_FILE_TOKEN = re.compile(r"backend/tests/[A-Za-z0-9_]+\.py")

# ci.yml 的 job 键（2 空格缩进）；steps / 注释行的缩进或首字符都不匹配。
_JOB_KEY = re.compile(r"^  [A-Za-z0-9_-]+:", re.MULTILINE)

# D-14：Windows-only 标记。子串扫描（含注释 / 文档字符串）是刻意的——出现在注释里
# 也说明该文件与平台分支有关；是否真的该进清单由白名单显式裁定，不静默放过。
_WINDOWS_ONLY_MARKERS = ("os.name", "sys.platform", "WinDLL", "taskkill")

# 已知「带标记但有意不进冒烟清单」的显式例外：repo 相对路径 -> 理由。
# 当前为空（6 个既有 marker 文件都已在清单）。新增例外必须写清为什么，别把扫描架空。
_WINDOWS_ONLY_ALLOWLIST: dict[str, str] = {}


def _agents_md_command_lines() -> list[str]:
    """AGENTS.md 里的 Windows 冒烟 pytest 命令行（预期有且仅有一条）。

    锚 `-m pytest`：正文里引用测试文件名的行（如「测试隔离」表格行提到
    conftest.py）不是命令，不能算进来。
    """
    text = AGENTS_MD.read_text(encoding="utf-8")
    return [
        line
        for line in text.splitlines()
        if "-m pytest" in line and _FILE_TOKEN.search(line)
    ]


def _ci_windows_smoke_block() -> str:
    """ci.yml 里 backend-windows-smoke job 的整段文本（到下一个 job 键为止）。

    边界按「下一个 2 空格缩进的 job 键」扫描，不硬钉某个 job 名：中间插入
    新 job 不会静默扩块，既有 job 改名也不会炸出看不懂的 ValueError。
    """
    text = CI_YML.read_text(encoding="utf-8")
    start = text.index("  backend-windows-smoke:")
    boundary = _JOB_KEY.search(text, start + 1)
    assert boundary is not None, "ci.yml 里 backend-windows-smoke 之后找不到下一个 job 键"
    return text[start : boundary.start()]


def _smoke_files() -> tuple[set[str], set[str]]:
    """两址清单：(AGENTS.md 命令里的文件, ci.yml job 里的文件)。"""
    lines = _agents_md_command_lines()
    assert lines, "AGENTS.md 里找不到 Windows 冒烟 pytest 命令（含 backend/tests/*.py 的那行）"
    return set(_FILE_TOKEN.findall(lines[0])), set(_FILE_TOKEN.findall(_ci_windows_smoke_block()))


# 守卫自身就是扫描器的实现：_WINDOWS_ONLY_MARKERS 常量必然字面包含这些标记，
# 扫自己恒真且没有意义，故从扫描面排除；其余 test_*.py 一个不漏（含未来新增）。
_MARKER_SCAN_EXCLUDED = frozenset({"backend/tests/test_smoke_list_parity.py"})


def _test_files() -> list[str]:
    """``backend/tests/test_*.py`` 的 repo 相对路径（posix 形态，排除守卫自身）。"""
    tests_dir = REPO_ROOT / "backend" / "tests"
    return sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for path in tests_dir.glob("test_*.py")
        if path.relative_to(REPO_ROOT).as_posix() not in _MARKER_SCAN_EXCLUDED
    )


def _windows_only_markers(text: str) -> set[str]:
    """纯函数：文本里命中的 Windows-only 标记集合。"""
    return {marker for marker in _WINDOWS_ONLY_MARKERS if marker in text}


def _marker_scan_violations(
    texts: dict[str, str],
    smoke_files: set[str],
    allowlist: dict[str, str] | None = None,
) -> dict[str, set[str]]:
    """纯函数：带标记、但不在清单也不在白名单里的文件 -> 命中的标记集合。

    ``allowlist`` 省略时用模块级 ``_WINDOWS_ONLY_ALLOWLIST``；自测可注入合成表。
    """
    exempt = _WINDOWS_ONLY_ALLOWLIST if allowlist is None else allowlist
    violations: dict[str, set[str]] = {}
    for rel, text in texts.items():
        markers = _windows_only_markers(text)
        if markers and rel not in smoke_files and rel not in exempt:
            violations[rel] = markers
    return violations


def test_agents_md_carries_exactly_one_smoke_command() -> None:
    lines = _agents_md_command_lines()
    assert len(lines) == 1, (
        f"AGENTS.md 里含 backend/tests/*.py 清单的命令行应有且仅有一条，实测 {len(lines)} 条：{lines}"
    )


def test_windows_smoke_list_is_identical_in_both_places() -> None:
    agents_files, ci_files = _smoke_files()

    assert agents_files, "AGENTS.md 的 Windows 冒烟命令里解析不到测试文件"
    assert ci_files, "ci.yml backend-windows-smoke job 里解析不到测试文件"
    assert agents_files == ci_files, (
        "Windows 冒烟清单双址漂移："
        f"仅 AGENTS.md 有 {sorted(agents_files - ci_files)}；"
        f"仅 ci.yml 有 {sorted(ci_files - agents_files)}"
    )


def test_every_smoke_list_entry_exists_on_disk() -> None:
    agents_files, ci_files = _smoke_files()
    missing = sorted(rel for rel in agents_files | ci_files if not (REPO_ROOT / rel).is_file())
    assert not missing, f"冒烟清单里的文件不存在：{missing}"


# ── D-14：Windows-only 标记扫描 ──────────────────────────────────────────

# 现状锚：这 6 个既有 marker 文件都已在清单里（自测③逐个验它们确实带标记）。
_EXPECTED_MARKER_FILES = frozenset(
    {
        "backend/tests/test_bash_tool.py",
        "backend/tests/test_gitignore_visibility.py",
        "backend/tests/test_p2_mcp.py",
        "backend/tests/test_static_guard.py",
        "backend/tests/test_workspace.py",
        "backend/tests/test_workspace_files_api.py",
    }
)


def test_marker_scan_flags_a_marked_file_missing_from_the_list() -> None:
    """自测①：合成「带标记但不在清单」必须报红（否则扫描是空转）。"""
    violations = _marker_scan_violations(
        {"backend/tests/test_not_listed.py": 'if os.name == "nt":\n    pass\n'},
        smoke_files=set(),
    )

    assert violations == {"backend/tests/test_not_listed.py": {"os.name"}}


def test_marker_scan_honours_the_explicit_allowlist() -> None:
    """自测②：白名单条目（路径 -> 理由）豁免；别的带标记文件仍然报红。"""
    allowlist = {"backend/tests/test_not_listed.py": "covered by conftest instead"}

    assert (
        _marker_scan_violations(
            {"backend/tests/test_not_listed.py": 'if os.name == "nt":\n    pass\n'},
            smoke_files=set(),
            allowlist=allowlist,
        )
        == {}
    )
    assert _marker_scan_violations(
        {"backend/tests/test_other.py": 'if sys.platform == "win32":\n    pass\n'},
        smoke_files=set(),
        allowlist=allowlist,
    ) == {"backend/tests/test_other.py": {"sys.platform"}}


def test_every_marker_file_is_covered_by_the_smoke_list() -> None:
    """自测③：6 个既有 marker 文件确带标记、确在清单里，且全仓无未覆盖违规。"""
    agents_files, ci_files = _smoke_files()
    smoke = agents_files | ci_files
    texts = {
        rel: (REPO_ROOT / rel).read_text(encoding="utf-8") for rel in _test_files()
    }

    missing_from_list = sorted(_EXPECTED_MARKER_FILES - smoke)
    assert not missing_from_list, f"既有 marker 文件不在冒烟清单：{missing_from_list}"
    for rel in _EXPECTED_MARKER_FILES:
        assert rel in texts, f"锚点文件不存在：{rel}"
        assert _windows_only_markers(texts[rel]), f"{rel} 不再含 Windows-only 标记，请更新锚点"
    assert _marker_scan_violations(texts, smoke) == {}
