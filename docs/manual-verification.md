# 真模型网页验收（9 个场景）

> 发布前 / 换供应商时**在网页端逐条手动跑**；CI 不跑真模型（ADR 0008「三笔账」2）。
> 每条记录日期与所用配置名即可，不把模型名与时间写进 README（数字与型号必腐）。
> 结果表的「证据」列只写文件名；本机证据根是 `%TEMP%\qianfan-evidence\`（Windows），按场景目录查看（如 `C7/`）。
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
   在设置里配一条命中 `bash` 的危险动作规则（如命令包含 `rm -rf`），让 agent 执行该命令。
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
| 1 | 2026-10-20 | 默认（deepseek-v4.1-flash） | 通过：`write:success` + `bash(git init && git status):success` | `C7/scenario1.png`，任务 `538d5387` |
| 2 | 2026-10-20 | 默认 | 通过：计划 v2；手工追加的 `EDITED-BY-HUMAN` 被执行，文件落盘 | `C7/scenario2-*.png`，任务 `75056984` |
| 3 | 2026-10-20 | 默认 | 通过：`sleep 25` 中途停止（`bash:failed`），同一任务续说后创建 `stop-continued.txt` | `C7/scenario3-*.png`，任务 `e968733e` |
| 4 | 2026-10-20 | 默认 | 通过：`policy_rules` 命中 `rm -rf` → `skipped` + 理由；canary 目录未删；模型改用 `echo policy-blocked-ok` 成功 | `C7/scenario4-rmrf-blocked.png` |
| 5 | 2026-10-20 | 默认 | 通过：`bash-made.txt` 出现在工作区文件列表（9 字节） | `C7/scenario5.png`，任务 `4d484ac1` |
| 6 | 2026-10-20 | 默认 | 通过：A/B 各自列表；B 第二次启动交接同一后台；从「全部」续 A 任务，`a-continued.txt` 落在 A 而非 B | `C7/scenario6-*.png` |
| 7 | 2026-10-20 | 默认 | 通过：重启后 7 条历史仍在；停下任务可续说（`after-restart.txt`）；RUNNING 孤儿重启后转 INTERRUPTED 并续跑（`kill-resume.txt`） | `C7/scenario7-*.png`，任务 `177b4b25` |
| 8 | 2026-10-20 | 默认 | 通过：后台命令立即返回（`C7BG2-1532`），停止前 PID 存活、停止后消失 | `C7/scenario8*.png`，任务 `d4fec841` |
| 9 | 2026-10-20 | 默认 + qwen3.8-flash | 通过：MCP `echo:hello-c7-mcp`；Exa 搜索返回结果；子代理 `summarize-notes` completed；中途切换 trace 记录 `default → 0792b93b260c`，下一条用 qwen3.8-flash | `C7/scenario9-*.png`，任务 `11a0773b` / `e08a43c5` / `d2ce274c` / `30f0d915` |

> 场景 6 的 A/B 路径按后端记录的绝对路径核对（Windows 反斜杠），写入位置以文件系统实际落点为准。
> 场景 9 的搜索使用 `SEARCH_PROVIDER=exa`（本机 DuckDuckGo 不可达，见 S2 #175）。
> 场景 9 的子代理第一次因工作区没有 README 返回「无法完成」（status 仍是 completed），改用 `notes.txt` 重跑成功；表中记录的是成功那次。
