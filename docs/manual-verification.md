# 真模型网页验收（9 个场景）

> 发布前 / 换供应商时**在网页端逐条手动跑**；CI 不跑真模型（ADR 0008「三笔账」2）。
> 每条记录日期与所用配置名即可，不把模型名与时间写进 README（数字与型号必腐）。
> 结果表的「证据」列只写仓库内路径；证据根是 `docs/assets/verification/<日期>/`（截图 + 脱敏结果 JSON）。
> 前置：`frontend` 已构建、`qianfan` 能启动、`~/.qianfan/credentials.json` 或环境变量里有可用 Key。

## 场景与判据

1. **执行模式新建文件 + bash `git init && git status`**
   新建任务（执行模式）让 agent 建一个文件，再用 bash 在任务目录跑 `git init && git status`。
   判据：文件出现在任务目录；`git status` 能列出它；任务停下且步骤时间线里两条工具调用都是 `success`。

2. **计划文件 → 手工改文件 → 切执行**
   计划模式下让 agent 产出计划；用计划面板（或外部编辑器）改计划并保存；切到执行模式让它照改后的计划做。
   判据：计划版本递增；后续动作体现手工改的内容；`.qianfan/` 不出现在用户的 `git status` 里。

3. **跑到一半停止 → 续说**
   让 agent 做一件需要多步的事，运行中按「停止」；再发一条消息继续。
   判据：停止后状态为「停下」；续说沿同一任务/对话继续，不新建任务；最终产物完整。

4. **危险规则 → 调用被拒、模型换路**
   在 `.env` 的 `policy_rules` 里配一条命中 `bash` 的规则（如
   `[{"tool":"bash","pattern":"rm\\s+-rf","reason":"destructive shell command"}]`），重启后台，
   让 agent 执行该命令。
   判据：该次调用 `skipped`，结果里带 `{block, reason}`；模型改用其他方式完成任务；命令没有实际执行。

5. **工作区文件列表看得到 bash 写的文件**
   让 agent 用 bash（不是 `write` 工具）在任务目录写一个文件。
   判据：「工作区文件」列表出现该文件，可预览/下载。

6. **多项目（A / B 两个目录，一个后台）**
   在 A 目录启动 `qianfan`，新建任务；在 B 目录再启动一次（应交接给同一后台并定位到 B）。
   判据：A 的列表只显示 A 的任务；B 的列表只显示 B 的任务；从「全部」打开 A 的任务续说，写入仍落在 A。

7. **关掉再开：历史、停下可续、跑一半可续跑**
   关闭 qianfan 再启动。先让一个任务停下后重启并续说；再让一个任务在运行中被关闭，重启后继续。
   判据：历史仍在；停下任务可直接续说；被中断的运行在重启后能沿 checkpoint 继续，产物落在原任务目录。

8. **bash 起后台进程：调用马上返回，停止时被收（F3）**
   让 agent 用 bash 起一个长睡进程并回显 PID，随后直接回答；按「停止」。
   判据：工具调用远早于进程结束返回；停止后该 PID 不再存活（子进程树一并回收）。

9. **子代理 / MCP / 联网搜索各一次 + 中途换模型**
   分别派一个子代理、调用一个 MCP 工具、搜一次网；再在一个任务中途切换模型并续说。
   判据：三条路径各成功一次（trace/步骤里有对应记录）；切换后下一条消息使用新配置（trace 的 `model_profile` 变化）。

## 结果记录

| # | 日期 | 配置 / 模型 | 结果 | 证据 |
|---|---|---|---|---|
| 1 | 2026-10-21 | 默认（deepseek-v4.1-flash） | 通过：`write:success` + `bash(git init && git status):success` | `scenario1.png` / `scenario1.json`，任务 `b9b3f2ee` |
| 2 | 2026-10-21 | 默认 | 通过：计划 v1 → 人工改成 v3；`edited-by-human` 被执行，`.qianfan/` 不进 `git status` | `scenario2.png` / `scenario2.json`，任务 `d771829c` |
| 3 | 2026-10-21 | 默认 | 通过：`sleep 30` 中途停止（`bash:failed`），同一任务续说后创建 `stop-continued.txt` | `scenario3-stopped.png` / `scenario3.png` / `scenario3.json`，任务 `3353c791` |
| 4 | 2026-10-21 | 默认 | 通过：`policy_rules` 命中 `rm -rf` → `skipped` + 理由；canary 目录未删；模型改写成 `policy-blocked-ok.txt` | `scenario4.png` / `scenario4.json`，任务 `63481ed3` |
| 5 | 2026-10-21 | 默认 | 通过：`bash-made.txt` 出现在工作区文件列表（20 字节） | `scenario5.png` / `scenario5.json`，任务 `2cf1218d` |
| 6 | 2026-10-21 | 默认 | 通过：A/B 各自列表；B 第二次启动交接同一后台；从「全部」续 A 任务，`a-continued.txt` 落在 A 而非 B。交接步骤 2026-10-22 在 #180 F1 修复后复跑 | `scenario6.png` / `scenario6.json` / `scenario6-handoff.txt`，任务 `2cf1218d` / `d7c3a8d0`；复跑 `docs/assets/verification/2026-10-22/scenario6-handoff.json` |
| 7 | 2026-10-21 | 默认 | 通过：重启后当前目录 10 条历史（重启时点全部 11 条）仍在；停下任务可续说（`after-restart.txt`）；RUNNING 孤儿重启后转 INTERRUPTED，悬空调用被清扫后从断点续跑（`kill-resume.txt`） | `scenario7.png` / `scenario7.json`，任务 `8ca45bc4` / `2cf1218d` |
| 8 | 2026-10-21 | 默认 | 部分：后台命令立即返回（`C7BG2-1982`），心跳文件在停止后不再增长；前景 `sleep 600` 被 `task stop requested` 终止。未记录 PID，进程树停止以心跳停止为证 | `scenario8.png` / `scenario8.json`，任务 `b55af32a` |
| 9 | 2026-10-21 | 默认 + qwen3.8-flash | 通过：MCP `echo:hello-c7-mcp`；Exa 搜索返回 `langgraph==1.2.0`；子代理 `read-notes` completed；中途切换 trace 记录 `default → 2556f5d79830`，第 4 步起用 qwen3.8-flash | `scenario9.png` / `scenario9.json`，任务 `7f8204d1` |
| 2 | 2026-10-27 | 默认（deepseek-v4.1-flash） | 通过（新版界面）：计划 v1 → 网页端编辑保存 v2（文件名改 `human-edit2.txt`）；执行阶段只落该文件；`.qianfan/` 不进 `git status` | `scenario2-3.json` / `scenario2-plan-v1.png` / `scenario2-plan-edited.png` / `scenario2-final.png`，任务 `665b68cb` |
| 3 | 2026-10-27 | 默认 | 通过（bash 受本机限制，见下）：运行中点停止 → 已停下；同一任务续说创建 `continued3.txt`，最终回答 198 字；SSE 重连后头部仍「运行中」、停止按钮在（#189） | `scenario2-3.json` / `scenario3-reconnect.png` / `scenario3-stopped.png` / `scenario3-final.png`，任务 `e46f194f` |
| 6 | 2026-10-27 | 默认 | 通过（UI 部分）：A / B 两个工作区各自列表；「全部」按目录分组；从「全部」打开 A 续说，`a2.txt` 落在 A。CLI 交接步骤沿用 2026-10-22 证据，未重跑 | `scenario6.json` / `scenario6-*.png`，任务 `25c55dcc` / `f1477ef0` |
| 7 | 2026-10-27 | 默认 | 通过：重启后历史仍在；停下任务续说 `a3.txt`；RUNNING 孤儿重启后转 INTERRUPTED，续说被接受并完成（6 步，回答 7 字） | `scenario7.json` / `scenario7-*.png`，任务 `25c55dcc` / `a940d160` |
| B1 | 2026-10-27 | 坏 Key 档案（`B1_BAD_KEY`，env）→ 默认 | 通过：401 `invalid_api_key` → FAILED；失败态计划可编辑；切默认档案续说，同一任务完成、`error` 清空 | `b1-failed-continue.json` / `b1-failed.png` / `b1-plan-editable.png` / `b1-continued.png`，任务 `1da34aa6` |

> 证据根：2026-10-21 及更早轮次在 `docs/assets/verification/2026-10-21/`；2026-10-22 在 `2026-10-22/`；2026-10-27（新版界面 + B1）在 `docs/assets/verification/2026-10-27/`。JSON 只留任务/步骤/工具状态与脱敏摘录，不含 Key 与完整对话。
> 场景 6 的 A/B 路径按后端记录的绝对路径核对（Windows 反斜杠），写入位置以文件系统实际落点为准。
> 场景 9 的搜索使用 `SEARCH_PROVIDER=exa`（本机 DuckDuckGo 不可达，见 S2 #175）。
> 场景 3 / 8 的「停止」在续说轮点击网页端按钮：首次运行期间 SSE 回放会把头部状态显示成「等待中」，停止按钮不可见。**2026-10-22 复核：仍存在**——首轮回放里唯一携带任务状态的事件是 `task_created`（`status=PENDING`，创建时发出）；后端开工时把任务改成 `RUNNING` 但不发事件（`backend/services/task_manager.py:698`），前端只有 `task_resumed`（续说 / 续跑）会把任务置 `RUNNING`（`frontend/src/store/taskStore.ts:66-73,124-126`）。SSE 自动重连（页面不重新挂载）时必现；首次挂载取决于 GET 快照与回放的先后（竞态）。停止按钮仅在 `RUNNING` 时渲染（`frontend/src/components/TaskHeader.tsx:55`）。**2026-10-27：F1 已修**——`task_created` 回放不再覆盖已有快照；`step_start` 遇 PENDING 时重取一次快照且只合并状态字段。回归测试见 `frontend/src/store/reduceTask.test.ts`；**F9 已于 2026-10-27 在网页端重连复测：头部保持「运行中」、停止按钮在**（证据 `docs/assets/verification/2026-10-27/scenario3-reconnect.png`）。
> 场景 6 的交接步骤在 2026-10-22（#180 F1 修复后）用真实 CLI 复跑：第二进程在退出前同步调用浏览器，URL 落在 `docs/assets/verification/2026-10-22/scenario6-handoff.json`。
> 场景 8 原命令只回显随机标记而非 PID；时间戳与心跳数据在 JSON 里，表格如实记为「部分」。
> 2026-10-27 轮次（新版界面）：本机 Git Bash 被安全软件拦截（`msys-2.0 … NtCreateDirectoryObject … 0xC0000022`），`bash` 调用失败，停止时记为 `skipped`；场景 3 的停止-续说判据不依赖 bash 成功执行，场景 7 的孤儿任务在首步前被杀、续跑沿用轮首 checkpoint。UI 面（停止、续说、任务头状态、SSE 重连）均按判据通过。
> B1 轮次的坏 Key 用环境变量注入（`B1_BAD_KEY`）：本机 `~/.qianfan/credentials.json.tmp` 写入被**进程权限边界**拒绝——沙箱外写同一目录成功、目录 ACL 实测正常（owner FullControl），因此这是「受限进程/沙箱」而不是 ACL 损坏（归因已于 #244 更正）。qianfan 侧现在把该失败转成 4xx + 可读原因；这不改变 B1 的失败-续说判据。
> 场景 6 的 A/B 工作区建在 `.qoder/tmp/qa2026-*`（仓库内、gitignored）；文件落点用文件系统核对——W3 忽略规则下工作区文件面不会列出被 ignore 的路径。
