#!/usr/bin/env python3
"""commit-msg 钩子（#114）：格式阻断 + 票号提醒。由 .pre-commit-config.yaml 调用。

四条规则：
  1. 消息非空
  2. 主题行 ≤72 字符
  3. 主题符合 conventional 前缀——与本仓实际提交惯例一致
     （docs(agents): … / docs(100 步 3.5): … / Merge pull request …）
  4. 非 docs 类且全文无 #N：只打印**提醒**不阻断——docs 小修无票号直推是既定
     惯例（docs/agents/issue-tracker.md「Release conventions」），一刀切拦死
     只会逼所有人 --no-verify，钩子就废了。

Merge 提交整条放行（不 squash 是 workflow 步 6 的既定做法，且 GitHub 生成的
"Merge pull request #N from owner/branch" 主题天然超 72 字符且无前缀）。
"""

import os
import re
import sys

PREFIXES = (
    "feat", "fix", "docs", "chore", "refactor",
    "test", "perf", "build", "ci", "style", "revert",
)
SUBJECT_RE = re.compile(r"^(%s)(\([^)]*\))?!?: .+" % "|".join(PREFIXES))
TICKET_RE = re.compile(r"#\d+")
SUBJECT_MAX = 72


def evaluate(message: str) -> tuple[bool, list[str], bool]:
    """纯判定：返回 (是否放行, 错误列表, 是否需要票号提醒)。"""
    subject = next((line for line in message.splitlines() if line.strip()), "")

    # Merge 提交整条放行：主题由 GitHub 生成，既长又无 conventional 前缀。
    if subject.startswith("Merge "):
        return True, [], False

    errors: list[str] = []
    if not subject.strip():
        errors.append("提交消息为空")
    if len(subject) > SUBJECT_MAX:
        errors.append(
            f"主题行 {len(subject)} 字符 > {SUBJECT_MAX}：'{subject[:SUBJECT_MAX]}…'"
        )
    if subject.strip() and not SUBJECT_RE.match(subject):
        errors.append(
            "主题行不符合 conventional 前缀，应为 "
            f"<{'|'.join(PREFIXES)}>(<scope>)?: <描述>"
            "（feat / fix / docs / chore / refactor / test / perf / build / ci / style / revert）"
        )
    if errors:
        return False, errors, False

    head = subject.split(":", 1)[0].split("(", 1)[0]
    remind = head != "docs" and not TICKET_RE.search(message)
    return True, [], remind


def main() -> int:
    # 真实 commit-msg 调用：pre-commit 传入提交消息文件（.git 下）。
    # `pre-commit run --all-files` 会把全仓文件列表当 argv——那不是消息文件，
    # 找不到 .git 下的消息就跳过（全量模式的冒烟由真实提交承担，见 #114 AC6）。
    candidates = [
        arg for arg in sys.argv[1:]
        if "COMMIT_EDITMSG" in arg or "/.git/" in arg or arg.startswith(".git" + os.sep)
    ]
    if candidates:
        msg_path = candidates[0]
    elif os.path.isfile(".git/COMMIT_EDITMSG"):
        msg_path = ".git/COMMIT_EDITMSG"  # 主检出（.git 是目录）
    else:
        print("commit-msg 钩子：未找到提交消息文件（pre-commit run --all-files 模式）— 跳过")
        return 0
    with open(msg_path, encoding="utf-8") as f:
        message = f.read()

    ok, errors, remind = evaluate(message)
    if not ok:
        for err in errors:
            print(f"commit-msg 拒绝：{err}", file=sys.stderr)
        return 1
    if remind:
        print(
            "提醒（不阻断）：非 docs 类提交建议带票号（#N）——workflow 约定 PR 与提交带票号；"
            "docs 小修无票号直推除外。"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
