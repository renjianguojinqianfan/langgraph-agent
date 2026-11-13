# 验证环境笔记：沙箱 / 预演合并 / 临时 worktree / 真机手测配方

> 触发词：**沙箱 / 预演 / 临时树 / full-access / 假红 / 真机手测 / CDP / 后台 PID** → 本文件（AGENTS.md「验证入口」的
> 前置指针，渐进披露、不内联）。2026-10-31 retro C24 立档：素材来自 #254 三切片交付
> （实施 / 评审两条会话各把同一批环境问题重新诊断一次）与 2026-11-02 的一次全量实测。
> 本文件记**环境判据与配方**，不记现役测试计数（数字必腐，以实跑为准）。

## 1. 沙箱矩阵：受限模式下必败的测试类

harness（dsh / opencode / qoder）在受限模式下会挡住 MSYS 运行时与仓库外临时目录，
离线回归的假红分**两层**，签名不同、处置不同：

| 层 | 签名 | 影响面 |
|---|---|---|
| bash 假红 | 退出码 `3221225794` + stderr `*** fatal error - couldn't create signal pipe, Win32 error 5` | `test_bash_tool.py` 整文件 + 依赖 bash 的 e2e |
| 临时根被拒 | `PermissionError: [WinError 5]`，路径形如 `%TEMP%\<harness>-*\qianfan-tests-*\data` | 几乎所有用 `tmp_path` / 模块级 `TestClient` lifespan 的用例（2026-11-02 dsh 默认沙箱实测 **562 个 error**） |

- bash 层的完整判据、机理与已排除项见 [`docs/harness-bash-false-red.md`](../harness-bash-false-red.md)（那篇管 bash；临时根层是本篇补的——`conftest.py` 把测试根钉到检出外，沙箱恰好不让建）。
- **定性协议（探针先行 + 对照实验）**：先在 harness 里跑 bash spawn 探针（`subprocess.run([bash, '-c', 'echo ok'])`，看退出码与 stderr 签名）分清机器问题还是 harness 血统；再跑全量。同一提交在 harness 外（自己终端 / CI）绿、harness 内红 → **红归环境，不归代码**；未做体外对照的红不得写进结论。
- **处置**：dsh 用 `danger-full-access`（限制不认路径，换解释器位置无用）；opencode / qoder 可换仓库外解释器（限制按路径）。代码侧无需改动。

## 2. 四态记录要求

报告完成时区分 **通过 / 跳过 / 未运行 / 产物验收**；harness 内跑出的红一律标注「疑似环境」并附体外对照，`exit 0` ≠ 全验收。

## 3. 临时树 / 预演合并跑门禁配方

在临时 worktree 里复现 CI 确切命令时的已知墙与绕法（2026-10-31 opencode 会话连撞四墙 + #254 实施侧经验）：

- **路径**：worktree 放普通盘符路径；别放 harness 临时区。git-bash 的 `/tmp` ≠ Windows `%TEMP%`，取值用 `cygpath -w /tmp` 或直接在 pwsh 里读 `$env:TEMP`（retro C21/C30 同源坑）。
- **pre-commit**：entry 用仓库相对 `.venv311`，临时树里找不到 → 临时树自备 `.venv311`（junction 主检出）或回主检出跑门禁；跳过时 `--no-verify` 并在 PR 里记理由。
- **摘除 junction 共享主检出的临时树（危险动作）**：`git worktree remove` 的递归删除会**穿过 junction 删空主检出的真实目录**（`.venv311` / `frontend/node_modules`）；`cmd /c rmdir` 的退出码**不能当判据**——Git Bash 传参把路径切碎时它静默失败、链接还在（2026-10-27 事故，2026-10-29 复做成功）。安全序列三步，每步可证伪：① 用落盘的 `.ps1`（别在 Git Bash 里内联 PowerShell，`$_` 与反斜杠会被切碎）调 `[System.IO.Directory]::Delete(<junction 路径>)`——它只摘 reparse point、不跟随；② 断言 `Get-ChildItem <trees> -Recurse -Attributes ReparsePoint | Measure-Object` **计数为 0**；③ 才 `git worktree remove --force`。摘前先杀掉用它的进程（vite / uvicorn）；判「链接已摘」只认 `dir /AL` 或 reparse 计数。误删后需重建 `.venv311` 与前端 `node_modules`（`npm ci`）。
- **ruff**：拒绝在沙箱临时路径建 cache → `--no-cache`。
- **mypy**：cache 指主检出 → `--cache-dir=<主检出>/.mypy_cache`。
- **pytest**：tempfile 探测失败 → 回主检出跑，或用 `danger-full-access`。
- **脚本写文件不内联**：harness 对长内联命令的转写会出歧义，先落盘再执行。

## 4. full-access 批处理约定

一次批准内把「targeted → 3 连跑 → 全量」串成一条命令，把批准次数压到最少。基线参考：2026-10-31 五轮全量 ≈ 23 分钟 + 13 次批准；2026-11-02 单次全量 227.69s（full-access 下）。

## 5. 一次可复现的干净基线（2026-11-02，dsh + danger-full-access，master `def1470`）

```bash
.\.venv311\Scripts\python.exe -m pytest backend/tests/ -q      # 全绿（计数以实跑为准）
.\.venv311\Scripts\python.exe -m ruff check backend scripts start.py   # All checks passed
.\.venv311\Scripts\python.exe -m mypy                        # no issues found
.\.venv311\Scripts\python.exe scripts\check_doc_links.py     # 相对链接全部可解析
cd frontend; npm run typecheck; npm test; npm run check:sse; npm run build
```

**对照（同一提交、dsh 默认沙箱）**：5 failed + 181 passed + 562 errors——两层假红签名全中，
代码零回退。结论：**harness 内不给 full-access 时，任何红都不能信**；看到红先对照本节签名，
别急着改代码。

## 6. 真机手测：网页驱动与后台进程取证（2026-11-09 P5-1 retro）

**应用内浏览器不可用时的网页驱动**：browser-use 的页面在未挂载 / 隐藏时视口为 0×0，截图与点击报
`NATIVE_BROWSER_VIEWPORT_UNAVAILABLE`（环境问题，不是页面 bug）。换本机 Chrome + CDP +
playwright-core，**一条命令跑一个动作**：

1. 起独立配置的 Chrome（不碰日常配置与登录态）：
   `chrome --remote-debugging-port=9222 --user-data-dir=<仓库外临时目录> http://127.0.0.1:8000`。
2. 驱动脚本用 `chromium.connectOverCDP("http://127.0.0.1:9222")`，取
   `contexts()[0].pages()[0]`，按 label / role / 可见文本定位；`NODE_PATH` 指向装有
   `playwright-core` 的 `node_modules` 的**上一级**（任一前端项目 `npm i playwright-core` 即可）。
3. 动作拆成 `goto` / `fill` / `click` / `select` / `screenshot` / `evaluate` 等单步脚本，
   跑完 `process.exit(0)`。长驻脚本在 harness 里容易被信号打断；失败就重连再跑下一步。
4. 留证用 `page.screenshot({path})`；判据做元素级断言（状态 / 文本 / 计数），不要整图像素 diff。

**后台 PID 取证**：应用内 bash 工具里的 `$!` 可能为空（`start` / 子 shell 的作业表不保证），
不能拿「回显为空」判定进程没起来。用系统进程表核对（`tasklist /FI "PID eq <pid>"` 或 Win32
进程查询），停止后再查一次确认消失；证据里记 PID 与两次查询结果。
