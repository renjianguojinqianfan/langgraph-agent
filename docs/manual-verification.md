# 真模型网页验收（10 个场景）

> 发布前 / 换供应商时**在网页端逐条手动跑**；CI 不跑真模型（ADR 0008「三笔账」2）。
> 每条记录日期与所用配置名即可，不把模型名与时间写进 README（数字与型号必腐）。
> 结果表的「证据」列只写仓库内路径；证据根是 `docs/assets/verification/<日期>/`（截图 + 脱敏结果 JSON）。
> 结果 JSON 可用 `.venv314\Scripts\python.exe scripts\export_task_evidence.py <task_id>` 导出：
> 默认写到当天证据目录，仓库根 / 用户主目录 / 其他本机绝对路径分别替换为 `<repo-root>` / `<user>` / `<workspace>`（门禁在册占位符，过 `check_no_machine_paths`）。
> 前置：`frontend` 已构建、`qianfan` 能启动、`~/.qianfan/credentials.json` 或环境变量里有可用 Key。
> 启服务统一用 `qianfan`（或开发态 `python start.py`）；**不要手工 `uvicorn backend.main:app`**——
> 没有 `QIANFAN_INSTANCE` 实例标识会被启动守卫拒绝（会绕过单后台 / host / Key 守卫，#259）。

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
   `[{"tool":"bash","pattern":"rm\\s+-rf","reason":"destructive shell command"}]`），重启后台；
   先在任务工作目录（bash 的 cwd）建一个受控 canary 目录。提示里**明确要求** agent 用 bash 删除它（如「这是受控的规则命中
   测试：请直接用 bash 执行 `rm -rf canary-<时间戳>`，保持原命令，不要换成其他方式」）——不强制
   时模型会自行规避，根本没产生被拒调用，场景等于空跑。
   判据：该次调用 `skipped`，结果里带 `{block, reason}`；canary 目录仍在（命令没有实际执行）；
   模型随后换路完成任务或说明需要人工执行。

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
   `$!` 可能为空（见 `docs/agents/verification-notes.md` §6）：回显为空时用系统进程表按命令行取 PID，
   不以回显为空判定进程没起来。
   判据：工具调用远早于进程结束返回；停止后按进程表核对该 PID 不再存活（子进程树一并回收）。

9. **子代理 / MCP / 联网搜索各一次 + 中途换模型**
   分别派一个子代理、调用一个 MCP 工具、搜一次网；再在一个任务中途切换模型并续说。
   判据：三条路径各成功一次（trace/步骤里有对应记录）；切换后下一条消息使用新配置（trace 的 `model_profile` 变化）。

10. **预设对比（minimal vs full 同题，P5-1 使用）**
    把 `data/plugins.json` 写成 `{"preset": "minimal", "overrides": {}}`（或设置页「能力」区选 minimal）并重启后台；
    用同一道题执行一次任务；再改成 `{"preset": "full", "overrides": {}}` 重启，用同一道题执行第二次。
    判据：两次任务都能完成；`GET /api/plugins` 的 `active` 面与页面展示一致（minimal 无 skills / web_search /
    spawn_subagent / MCP 工具）；minimal 轮不出现被关闭工具的调用；切回 full 后工具面恢复。
    **P4-3 交付场景；P5-1（2026-11-09）真机执行，结果见结果表。**

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
| 1 | 2026-11-09 | 默认（真模型） | 通过：`write` + `bash(git init && git status)` 成功；文件在任务目录，`git status` 列出为未跟踪 | `p5-scenario1-5.json`，任务 `3b81858a` |
| 2 | 2026-11-09 | 默认 | 通过：计划 v1 → 网页端改为 v2；执行只落 `human-edited-p5.txt`，原计划文件未创建，`.qianfan/` 不进 `git status` | `p5-scenario2.json` / `p5-scenario2-plan-v1.png` / `p5-scenario2-final.png`，任务 `070b4ada` |
| 3 | 2026-11-09 | 默认 | 通过：`sleep 90` 运行中停止（`bash:failed`），同一任务续说后补齐 `stop-end-p5.txt` / `continued-p5.txt` | `p5-scenario3.json` / `p5-scenario3-stopped.png` / `p5-scenario3-resumed.png`，任务 `3b81858a` |
| 4 | 2026-11-09 | 默认 + 内置 `policy_rules` | 通过：`rm -rf canary-p5` → `skipped` + 理由；canary 保留；模型改用 write/edit，未换删除方式 | `p5-scenario4.json`，任务 `070b4ada` |
| 5 | 2026-11-09 | 默认 | 通过：bash 写的 `bash-made-p5.txt` 可在文件面板预览，下载保存后内容为 `P5-bash-ok` | `p5-scenario5-preview.png` / `p5-scenario1-5.json`，任务 `3b81858a` |
| 6 | 2026-11-09 | 默认 | 通过：A/B 列表各自隔离；B 目录第二次 CLI 启动交接同一后台并打开 `?dir=B`；从「全部」续 A 任务，写入落 A 不落 B | `p5-scenario6.json` / `p5-scenario6-all-view.png`，任务 `b1815a59` / `3b81858a` |
| 7 | 2026-11-09 | 默认 | 通过（含遗留）：重启后历史在；停下任务续说 `after-restart-p5.txt`；运行中硬杀后台重启后转 INTERRUPTED，续跑补齐 `orphan2-end-p5.txt` / `resume-after-kill-p5.txt`。**遗留：断点步骤留在 `running`（工具 `skipped`），该轮显示「执行中」**（已由 PR #317 修复；复跑见下组） | `p5-scenario7.json` / `p5-scenario7-resumed.png` / `p5-scenario7-stale-running.png`，任务 `119b8030` |
| 8 | 2026-11-09 | 默认 | 通过：后台 `sleep 600` 命令立即返回 `BG_PID=119`；停止后 119 与前台 120 两个进程均消失 | `p5-scenario8.json`，任务 `c2db922e` |
| 9 | 2026-11-09 | 默认 + Exa；MiMo V2.6 Flash（Token Plan） | 通过（MCP 未配置）：子代理 `spawn_subagent` success；Exa `web_search` success；Composer 选 MiMo 完成 `write` + `bash` 真跑；同一任务中途切 MiMo 续说，trace 后两步 `model_profile` 变为 MiMo 条目；`step` 档案上游 400，未作为本轮切换目标；MCP 未配置（`/api/mcp/servers` 为空） | `p5-scenario9-exa-search.json` / `p5-scenario9-exa-search.png` / `p5-scenario9-mimo.json` / `p5-scenario9-mimo-run.png` / `p5-scenario9-mimo-switch.json`，任务 `0055c384` / `693ff155` / `29f82c5f` / `874f0ecd` |
| 10 | 2026-11-09 | `minimal` → `full`（重启生效） | 通过：minimal 生效面 2 包 7 工具，同题只用 `write` + `bash` 完成；full 恢复 6 包 10 工具，同题完成；页面「当前生效」与 `GET /api/plugins` 的 `active` 一致。保存/重启走等价 `PUT /api/plugins`（设置页分段控件在电脑控制下点击未命中） | `p5-scenario10-minimal.json` / `p5-scenario10-minimal-settings.png` / `p5-scenario10-full.json`，任务 `690aeace` / `874f0ecd` |
| 1 | 2026-11-09（复跑） | 默认（deepseek-flash，真模型） | 通过：`write` 建 `p5r-scenario1.txt`；`bash(git init && git status)` success；文件在任务目录、`git status` 列为未跟踪；3 步 2 次调用全 done | `p5r-scenario1.json` / `p5r-scenario1.png`，任务 `eb508663` |
| 2 | 2026-11-09（复跑） | 默认 | 通过：计划 v1 → `PUT /plan` 改为 v2（文件名改 `p5r-human-edited.txt`）；切执行只落该文件；`.qianfan/` 不进 `git status` | `p5r-scenario2.json` / `p5r-scenario2-plan.json` / `p5r-scenario2.png`，任务 `ce09b82a` |
| 3 | 2026-11-09（复跑） | 默认 | 通过：`sleep 90` 运行中停止（`bash:failed`，`command stopped`）；同一任务续说创建 `p5r-stop-end.txt` | `p5r-scenario3.json` / `p5r-scenario3-stopped.png` / `p5r-scenario3-resumed.png`，任务 `a58207c3` |
| 4 | 2026-11-09（复跑） | 默认 + `.env` `policy_rules` | 通过（受控提示强制命中）：`rm -rf canary-p5r` → `skipped`，`error=destructive filesystem or OS command…`；canary 保留；模型转人工执行建议 | `p5r-scenario4.json` / `p5r-scenario4.png`，任务 `3e84f52a` |
| 5 | 2026-11-09（复跑） | 默认 | 通过：bash（非 `write`）创建的 `p5r-bash-made.txt` 出现在文件列表，预览内容 `P5R-BASH-OK`（与场景 4 同一任务第二轮） | `p5r-scenario5.json` / `p5r-scenario5-preview.png`，任务 `3e84f52a` |
| 6 | 2026-11-09（复跑） | 默认 | 通过：A/B 列表各自隔离；B 目录第二次 CLI 启动交接同一后台并 exit 0；从「全部」打开 A 任务续说，`p5r-a-continued.txt` 落 A 不落 B | `p5r-scenario6-a.json` / `p5r-scenario6-b.json` / `p5r-scenario6-handoff.json` / `p5r-scenario6-all-view.png`，任务 `eb508663` / `0a03eda9` |
| 7 | 2026-11-09（复跑） | 默认 | 通过（两处修复复验）：重启后历史在；停下任务续说创建 `p5r-restart-stop.txt`；运行中硬杀重启后转 INTERRUPTED，续跑完成后断点步骤 `failed`（调用 `skipped`）、其余步骤 done，轮次头「已完成」，`p5r-orphan-begin/end.txt` 齐 | `p5r-scenario7a.json` / `p5r-scenario7a-*.png` / `p5r-scenario7b-orphan.json` / `p5r-scenario7b-orphan.png` / `p5r-scenario7b-resumed.json` / `p5r-scenario7b-resumed.png`，任务 `b0f91ae2` / `e7cdeecd` |
| 8 | 2026-11-09（复跑） | 默认 | 通过：后台 `sleep 600` 命令立即返回；停止后后台 PID 31656 与前台 `sleep 60` PID 29184 均消失（`$!` 在本工具 shell 返回空，PID 以进程表核对） | `p5r-scenario8.json` / `p5r-scenario8-processes.json`，任务 `a50b5bf5` |
| 9 | 2026-11-09（复跑） | 默认 + 本地 MCP echo + Exa；MiMo V2.6 Flash | 通过（含 MCP）：`spawn_subagent` success（子任务 `list-files` completed）；`mcp__echo__echo` success；`web_search` success；同一任务中途切 MiMo 后，trace 第 5–6 步 `model_profile=eeda03bb9980`（前三步 deepseek） | `p5r-scenario9.json` / `p5r-scenario9-subagent-mcp-search.png` / `p5r-scenario9-model-switch.json` / `p5r-scenario9-model-switch-trace.json` / `p5r-scenario9-model-switch.png`，任务 `c9184515` / `095fa421` |
| 10 | 2026-11-09（复跑） | `minimal` → `full`（重启生效） | 通过：minimal 生效面 `files`+`bash`（7 工具），同题只用 `write` 完成；full 恢复 6 包 12 工具（含 MCP），同题完成；设置页「当前生效」与 `GET /api/plugins` 的 `active` 一致；测后删除 `data/plugins.json` 恢复默认 full | `p5r-scenario10-minimal*.json` / `p5r-scenario10-minimal-settings.png` / `p5r-scenario10-full*.json` / `p5r-scenario10-full-settings.png`，任务 `83df90b6` / `2f352d3f` |

> 2026-11-09 复跑在 PR #317 合并后的 master 上进行：网页由本机 Chrome + Playwright CDP 驱动、真模型；场景 9 的 MCP 用仓库内 `backend/tests/mcp_echo_server.py`（`MCP_SERVERS` 仅注入启动进程，不写仓库配置）。场景 4 第一轮模型拒绝执行删除命令、未产生 `skipped`，改用明确的受控测试提示后命中规则，证据取命中轮。已知 SSE 回放竞态（重开已停下任务短暂把末步显示为运行中 / 工具数翻倍）已由 PR #318 修复（回放归属 + 终态守卫）；`docs/frontend-design.md` §6 的回放手测清单待真机走查。

> 证据根：2026-10-21 及更早轮次在 `docs/assets/verification/2026-10-21/`；2026-10-22 在 `2026-10-22/`；2026-10-27（新版界面 + B1）在 `docs/assets/verification/2026-10-27/`。JSON 只留任务/步骤/工具状态与脱敏摘录，不含 Key 与完整对话。
> 场景 6 的 A/B 路径按后端记录的绝对路径核对（Windows 反斜杠），写入位置以文件系统实际落点为准。
> 场景 9 的搜索使用 `SEARCH_PROVIDER=exa`（本机 DuckDuckGo 不可达，见 S2 #175）。
> 场景 3 / 8 的「停止」在续说轮点击网页端按钮：首次运行期间 SSE 回放会把头部状态显示成「等待中」，停止按钮不可见。**2026-10-22 复核：仍存在**——首轮回放里唯一携带任务状态的事件是 `task_created`（`status=PENDING`，创建时发出）；后端开工时把任务改成 `RUNNING` 但不发事件（`backend/services/task_manager.py:698`），前端只有 `task_resumed`（续说 / 续跑）会把任务置 `RUNNING`（`frontend/src/store/taskStore.ts:66-73,124-126`）。SSE 自动重连（页面不重新挂载）时必现；首次挂载取决于 GET 快照与回放的先后（竞态）。停止按钮仅在 `RUNNING` 时渲染（`frontend/src/components/TaskHeader.tsx:55`）。**2026-10-27：F1 已修**——`task_created` 回放不再覆盖已有快照；`step_start` 遇 PENDING 时重取一次快照且只合并状态字段。回归测试见 `frontend/src/store/reduceTask.test.ts`；**F9 已于 2026-10-27 在网页端重连复测：头部保持「运行中」、停止按钮在**（证据 `docs/assets/verification/2026-10-27/scenario3-reconnect.png`）。
> 场景 6 的交接步骤在 2026-10-22（#180 F1 修复后）用真实 CLI 复跑：第二进程在退出前同步调用浏览器，URL 落在 `docs/assets/verification/2026-10-22/scenario6-handoff.json`。
> 场景 8 原命令只回显随机标记而非 PID；时间戳与心跳数据在 JSON 里，表格如实记为「部分」。
> 2026-10-27 轮次（新版界面）：本机 Git Bash 被安全软件拦截（`msys-2.0 … NtCreateDirectoryObject … 0xC0000022`），`bash` 调用失败，停止时记为 `skipped`；场景 3 的停止-续说判据不依赖 bash 成功执行，场景 7 的孤儿任务在首步前被杀、续跑沿用轮首 checkpoint。UI 面（停止、续说、任务头状态、SSE 重连）均按判据通过。
> B1 轮次的坏 Key 用环境变量注入（`B1_BAD_KEY`）：本机 `~/.qianfan/credentials.json.tmp` 写入被**进程权限边界**拒绝——沙箱外写同一目录成功、目录 ACL 实测正常（owner FullControl），因此这是「受限进程/沙箱」而不是 ACL 损坏（归因已于 #244 更正）。qianfan 侧现在把该失败转成 4xx + 可读原因；这不改变 B1 的失败-续说判据。
> 场景 6 的 A/B 工作区建在 `.qoder/tmp/qa2026-*`（仓库内、gitignored）；文件落点用文件系统核对——W3 忽略规则下工作区文件面不会列出被 ignore 的路径。
> 2026-11-09 轮次（真机电脑控制）：启动时若 `NO_PROXY` 含 `[::1]`，httpx 会抛 `InvalidURL: Invalid port: ':1]'` 使后台启动失败；本次只在启动进程内把该项规范为 `::1` 后继续，未改系统或仓库配置。**已由 PR #317 在启动装配处规范化修复（含 `[host]:port` 去端口）**。
> 硬中断重启：任务转 INTERRUPTED 且可续跑，但断点步骤留在 `running`（其工具为 `skipped`），页面该轮显示「执行中」（`p5-scenario7.json`）；已停下任务重开时短暂「执行中 / 工具数翻倍」来自页面缓存，刷新快照后恢复（`p5-replay-regression.png`）。**2026-11-09 复跑：断点步骤由启动对账 / 终态写入收尾为 `failed`，轮次头按末步显示「已完成」（PR #317）；SSE 回放竞态由 PR #318 修复（回放归属 + 终态守卫；重开已停下任务不再翻倍 / 翻回运行中）。**
> 设置页「能力」区的自定义分段控件在电脑控制下点击未命中（点「测试」会落到相邻「参数」），保存 / 重启改走等价 `PUT /api/plugins`；重启后页面「当前生效」与 `GET /api/plugins` 的 `active` 一致。
> MiMo 使用 Token Plan 地址 `https://token-plan-cn.xiaomimimo.com/v1`（官方开放平台地址对该订阅 Key 返回 401）；首轮 MCP 本机未配置（场景 9 该子项记未运行），复跑用仓库内 MCP echo 补齐。
> 测试工作区在仓库内 `.qoder/tmp/p5-20261109/`（gitignored）；下载核验目录属本机配置，未写入文档。
