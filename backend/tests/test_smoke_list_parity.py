"""C23: Windows 冒烟清单在 AGENTS.md 与 ci.yml 两处必须逐条相同。

两份清单（AGENTS.md「验证入口」的本地命令 + ci.yml 的 backend-windows-smoke
job）此前只靠「两边保持同一清单」的文档约定维持一致，没有任何机械守卫；
#254 的三个兄弟 PR 各往两处同点追加测试文件时，合并冲突正落在这两行，解冲突
靠人肉逐行核对——丢一行 = Windows 冒烟面静默缩水，而 CI 仍是绿的。

本守卫纯文本解析两个文件，毫秒级、平台无关：集合不相等或文件不存在即红。
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
    """ci.yml 里 backend-windows-smoke job 的整段文本（到下一个 job 键为止）。"""
    text = CI_YML.read_text(encoding="utf-8")
    start = text.index("  backend-windows-smoke:")
    end = text.index("\n  frontend-build:", start)
    return text[start:end]


def test_agents_md_carries_exactly_one_smoke_command() -> None:
    lines = _agents_md_command_lines()
    assert len(lines) == 1, (
        f"AGENTS.md 里含 backend/tests/*.py 清单的命令行应有且仅有一条，实测 {len(lines)} 条：{lines}"
    )


def test_windows_smoke_list_is_identical_in_both_places() -> None:
    agents_files = set(_FILE_TOKEN.findall(_agents_md_command_lines()[0]))
    ci_files = set(_FILE_TOKEN.findall(_ci_windows_smoke_block()))

    assert agents_files, "AGENTS.md 的 Windows 冒烟命令里解析不到测试文件"
    assert ci_files, "ci.yml backend-windows-smoke job 里解析不到测试文件"
    assert agents_files == ci_files, (
        "Windows 冒烟清单双址漂移："
        f"仅 AGENTS.md 有 {sorted(agents_files - ci_files)}；"
        f"仅 ci.yml 有 {sorted(ci_files - agents_files)}"
    )


def test_every_smoke_list_entry_exists_on_disk() -> None:
    entries = set(_FILE_TOKEN.findall(_agents_md_command_lines()[0])) | set(
        _FILE_TOKEN.findall(_ci_windows_smoke_block())
    )
    missing = sorted(rel for rel in entries if not (REPO_ROOT / rel).is_file())
    assert not missing, f"冒烟清单里的文件不存在：{missing}"
