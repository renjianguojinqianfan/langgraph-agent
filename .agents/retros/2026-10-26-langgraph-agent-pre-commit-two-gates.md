# Retro 记录：本机 pre-commit 两道闸（2026-10-26）

> 接 `.agents/handoffs/2026-10-26-pre-commit-two-gates.md`（gitignored 工作稿，不随仓库分发）。
> 本文件记录可复现的事实、修法与回滚；**仓库源码零改动**，只动了本机 `.git/hooks` 与用户环境变量。
> 状态：两道闸均已解除并做过端到端提交验证。**观察与证据，不是长期授权。**

## 结论

1. **闸 1（缓存/数据库写不进）**：`setx PRE_COMMIT_HOME "E:\code\demo\.precommit-cache"` 已写入用户环境；它只影响之后启动的进程。本会话（Qoder 21:01 启动）仍解析到 `C:\Users\DELL\.cache`，重启 agent 后才继承；期间用 per-command 前缀。缓存目录实测可写。
2. **闸 2（脏树提交失败）**：根因是 `.git/hooks/post-checkout` 与 `post-commit` 两个 **10 字节空钩子**。它们让 git 在 pre-commit 的 stash 阶段派生 `sh.exe`；而 `.venv311` 的解释器被 HIPS 限制，其子孙进程创建 MSYS 共享对象被拒（`NtCreateDirectoryObject ... 0xC0000022`）。把两个空钩子挪开即通，**与提权 MSYS、并发 agent 无关**（交接文档的假设 1 已证伪）。
3. 验证：脏树 `pre_commit hook-impl` rc=0；真实 `git commit --allow-empty`（脏树）四钩子 Skip、commit-msg Passed、提交成功；探针提交已回滚，HEAD 仍 `a6bcfe7`。

## 证据链

### 1. 失败发生在 stash 的 enter，不是 restore

交接文档写「钩子 stash 成功后 restore 时」；traceback 显示实际是 `staged_files_only` 进入 stash 上下文后、**任何一个钩子运行之前**：

```
File ".../pre_commit/staged_files_only.py", line 81, in _unstaged_changes_cleared
    cmd_output_b(*_CHECKOUT_CMD, env=no_checkout_env)
Command: ('E:/Git/mingw64/libexec/git-core\\git.EXE', '-c', 'submodule.recurse=0', 'checkout', '--', '.')
stderr: 0 [main] sh (23636) E:\Git\usr\bin\sh.exe: *** fatal error
        - NtCreateDirectoryObject(\BaseNamedObjects\msys-2.0S5-b491248b697284ab): 0xC0000022
```

`git checkout` 返回 66，pre-commit 直接抛 `CalledProcessError`，钩子清单一条都没跑。日志里先出现 `Stashing unstaged files to ...` 再失败，容易误读成 restore。

### 2. 唯一触发点：两个空钩子

`GIT_TRACE=1` 跑 `git checkout -- AGENTS.md`（脏树），git 只派生一个子进程：

```
run-command.c: trace: run_command: .git/hooks/post-checkout <old> <new> 0
... sh.exe: *** fatal error - NtCreateDirectoryObject(...) 0xC0000022
```

两个钩子的内容都是 10 字节 `#!/bin/sh\n`（`od -c` 实证），无任何行为。挪开后同一 checkout **不再派生 sh**，pre-commit 的 stash/restore 正常。

### 3. HIPS 限制跟着 `.venv311` 解释器走

同一台机器、同一 shell、同一时刻的区分实验：

| 谁派生 `sh.exe` | 结果 |
|---|---|
| bash（Git Bash 自身） | ok |
| `sh.exe` → `sh.exe`（嵌套） | ok |
| node.exe | ok |
| `.venv311/Scripts/python.exe` | **FAIL 0xC0000022** |
| uv 基础解释器 3.11.15（`AppData/Roaming/uv/...`） | ok |
| `E:\python\python.exe`（3.13） | ok |

同组解释器写 `C:\Users\DELL`：

| 解释器 | 写用户目录 |
|---|---|
| `.venv311/Scripts/python.exe` | `PermissionError` |
| uv 基础 3.11.15 | WRITE_OK |
| `E:\python\python.exe` | WRITE_OK |

二进制不同，不是同一个 exe 换了路径：

| 文件 | 大小 | sha256 前 8 |
|---|---|---|
| `.venv311/Scripts/python.exe`（venv launcher） | 262144 | `4cca2027` |
| uv 基础 3.11.15 | 91648 | `ae7e9694` |
| `E:\python\python.exe` | 105696 | `7c2fc2b3` |

**推断**：HIPS 的规则针对 `.venv311` 这个 venv 解释器（或它作为 launcher 的身份），不是「所有 python.exe」，也不是「所有 MSYS」。交接文档的假设 1（提权 MSYS 进程占共享对象）被证伪：若共享对象被高完整性进程占住，bash/node 派生的 sh 也会失败；实测都正常。假设 2（HIPS）成立，并收窄到具体解释器。

**为什么干净树提交能过**：不触发 stash；`.git/hooks/pre-commit` 与 `commit-msg` 是 git 直接从 bash 派生 sh 后执行的，不在 venv python 的子孙树里，所以不受这条 HIPS 规则影响。

## 修法（本机，可逆）

1. **缓存**（交接文档已做，本次复核）：`setx PRE_COMMIT_HOME "E:\code\demo\.precommit-cache"`。回滚：`setx PRE_COMMIT_HOME ""`。注意 `setx` 只影响新进程；已在跑的 Qoder / opencode 需要重启才继承。
2. **空钩子**（本次实施）：

   ```bash
   mv .git/hooks/post-checkout .git/hooks/post-checkout.disabled-20261026
   mv .git/hooks/post-commit  .git/hooks/post-commit.disabled-20261026
   ```

   回滚：把两个 `.disabled-20261026` 文件改回原名。
   - 为什么不是改 pre-commit：`staged_files_only` 是框架行为，没有 `--no-stash`；不 patch `site-packages`。
   - 为什么不加 HIPS 白名单：安全产品规则不在仓库可控范围；空钩子无功能，移除触发点比登记例外更小。
   - `pre_commit install` 只装 `pre-commit` + `commit-msg`，不会重装这两个空钩子；若未来某个工具再次生成，同法处理。

## 验证记录（2026-10-26）

- 解析探针：本会话 `PRE_COMMIT_HOME` → `C:\Users\DELL/.cache`（未继承 setx）；显式前缀后，缓存写入 `E:\code\demo\.precommit-cache`。
- 脏树 `pre_commit hook-impl --hook-type=pre-commit`：rc=0，4 个钩子 Skip，`Restored changes from ...`。
- 真实提交：脏树 + `git commit --allow-empty -F <msg>` → 4 Skip + `commit-msg 校验 ... Passed` → 提交 `52d78fb`；`git reset --soft HEAD~1` 回滚，HEAD 回到 `a6bcfe7`，`AGENTS.md` 已还原。
- 未跑：全量 pytest / ruff / mypy（本次不改仓库源码，且这些钩子在 md-only 提交里本来就 Skip）。工作区原有的未跟踪 `pytest-of-DELL/` 未动。

## 仍存在 / 复发条件

- HIPS 对 `.venv311` 解释器的限制**没有解除**：该解释器写 `C:\Users\DELL` 仍被拒，它派生的 MSYS `sh` 仍会 0xC0000022。本次只是移除了提交路径上的唯一触发点。
- 若以后在钩子里加入 shell 入口、或让 git 从 venv python 子孙里派生 `sh`（如新的 `post-*` 钩子、`core.fsmonitor` 脚本、过滤器命令），问题会复发。优先保持钩子 entry 为 python/node 直接入口。
- 换机 / 重装安全软件 / 换解释器路径后，先跑两条探针：`GIT_TRACE=1 <git checkout 命令>` 看是否派生 sh；`.venv311/Scripts/python.exe -c "subprocess.run([sh,...])"` 看是否 0xC0000022。

## 回滚清单

1. `mv .git/hooks/post-checkout.disabled-20261026 .git/hooks/post-checkout`
2. `mv .git/hooks/post-commit.disabled-20261026 .git/hooks/post-commit`
3. （可选，恢复旧缓存路径）`setx PRE_COMMIT_HOME ""`

回滚后脏树提交会回到 0xC0000022；干净树提交不受影响。
