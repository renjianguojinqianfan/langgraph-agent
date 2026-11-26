# 使用手册

> 面向日常使用。架构与 REST / SSE 契约见 [architecture.md](architecture.md)，安全边界见
> [security.md](security.md)，UI 细节见 [frontend-design.md](frontend-design.md)，工程门禁与
> 任务路由见 [AGENTS.md](../AGENTS.md)。

## 1. 启动、工作区与工具

- **启动目录即工作区**：在哪个目录敲 `qianfan`，新任务就在哪个目录读写、执行命令、加载那里的
  `AGENTS.md` 与 `.agents/skills`；页面地址带 `?dir=` 时以它为准（[ADR 0010](adr/0010-launch-dir-workspace-and-model-profiles.md)）。
  左栏底部可切换工作区：只换历史与新建任务的落点，不重启后台，`?dir=` 深链保持。MCP 服务仍是
  整个后台共用一份，工作目录是后台的启动目录，不随任务切换。
- **历史与删除**：默认只显示当前目录，可切「全部」；任务头标出目录，续说回到任务自己的目录，
  目录被删后只剩记录、续说返回 409。历史可逐条或按目录删除：只删 `tasks.json` 记录、checkpoint、
  trace 与 `data/changes/<task_id>/` 变更 sidecar，**不动工作区文件**；运行中的任务会被拒绝（409），
  这些任务名下的后台进程会一并终止。
- **文件工具**：`read` / `write` / `edit` / `ls` / `glob` / `grep`，路径限定在任务工作区内，
  读出根、写出根与不存在的文件都会被拒绝；写成功返回绝对路径，工作区文件列表能看到。
- **`shell`**：Windows 默认 PowerShell 7（`pwsh`），POSIX 默认 `bash`；Windows 上显式传
  `shell="bash"` 走 Git Bash。缺指定 Shell 时给可读错误，不会换另一种 Shell 重试。命令以工作区根为
  cwd、拿白名单环境变量、默认 30s 超时（可传 1–600s）；停止 / 超时回收本次调用建立的进程组或
  Windows Job Object，包括调用返回后仍在跑的后台子进程（自行 `setsid` 脱离的进程不受管）。每流只保留
  输出尾部 30KB，没有 CPU / 内存 / 磁盘 / 进程数上限。
- **`web_search`**：搜索源 `duckduckgo` / `serpapi` / `exa`；国内网络下 duckduckgo 可能不可达，
  设 `search_provider=exa` 并导出 `EXA_API_KEY`。
- **MCP**：stdio 子进程只继承白名单环境变量——**环境变量里的** LLM / 云 Key 不会被继承，文件仍可读；
  额外变量用 `mcp_servers[].env` 显式声明。MCP 工具只在 `mcp` 能力包启用时连接。
- **子代理**：`spawn_subagent` 单入口，`explore`（只读）/ `execute` 两档；实际工具面 =
  固定档 ∩ 已启用能力包 ∩ 父任务已挂载工具，只能收窄，不能扩权（[architecture §4.6](architecture.md#46-子任务)）。
- **失败回传**：工具调用只执行一次，不自动重试、不熔断；失败或被规则拒时，事件面与模型面各有一份
  可读原因。

## 2. 共享后台

- **一台机器一个后台**：后台是独立进程并持有 `data/server.json` 单实例锁。日常 `qianfan` 发现健康
  实例就复用、否则 detach 拉起，等 `/health` 就绪后打开浏览器并退出启动器——**关掉启动它的终端不会
  停后台**。第二次在别的目录敲 `qianfan`，它把浏览器开到已在跑的后台、定位到当前目录，然后退出，
  不另起进程（`--port` 只对第一次生效）。
- **停止与状态**：`qianfan stop` 请求锁文件里的 instance，服务端校验后优雅收尾（拒绝新运行 → 停任务 →
  等 worker → 落盘 → 请求退出）；instance 不匹配被拒且不杀进程。`qianfan status` 输出 pid / port /
  mode / instance / started_at / healthy，无服务时非 0。
- **开发后台**：`python start.py` 起的前台开发后台没有可编程退出钩子，`qianfan stop` 会明确拒绝（409），
  请回开发终端 Ctrl-C；开发后台仍随开发终端退出，且与 `qianfan` 互斥。手工
  `uvicorn backend.main:app` 一律被拒：没有 `QIANFAN_INSTANCE` 会绕过单后台 / 回环 host / Key 三道
  守卫，请用 `qianfan`、`qianfan serve` 或 `python start.py`。
- **host**：只允许回环（`127.0.0.1` / `localhost` / `::1`），`0.0.0.0` 等非回环值在启动期直接拒绝；
  开发态 Vite 代理固定 `127.0.0.1`（只用 IPv4 回环）。
- **已知限制（待修）**：`host=::1` 时，日常 `qianfan` 的 ensure 探测与浏览器地址仍固定
  `127.0.0.1`，`qianfan serve` 也固定 `127.0.0.1`；`python start.py` 的 uvicorn 跟随 host，但
  Vite 代理仍只走 IPv4。修好前请保持 `host=127.0.0.1`，或等后续修复。
- **前端产物**：`frontend/dist` 由后端同源托管；产物缺失或比 `frontend/src` 旧时 `qianfan` 会提示
  先构建。

## 3. 终端（qianfan repl）

```bash
qianfan repl
```

终端是**共享后台的薄客户端**：确保 / 连接已经在跑的后台，不打开浏览器、不另起运行时、不持任务锁，
只走现有 REST / SSE；首条输入创建任务，之后继续说话沿同一 `task_id` 续跑，和网页看到的是同一个任务。
**关闭终端只断连，运行中的任务继续**；停止任务用 `/stop` 或网页上的停止按钮。

- **命令**：`/new` 新任务（仅当当前任务已停下）、`/tasks` 任务列表、`/use <id>` 接入已有任务（前 8 位
  前缀即可）、`/stop` 停止当前任务、`/resume` 续跑已停下的任务、`/mode plan|build`、`/model <id>`、
  `exit` / `quit` 退出。**Ctrl-C 分两处**：提示符处直接退出；等待任务时先停当前任务、回提示符（不退出）。
- **启动与连接**：先做与网页入口同一套 auth / 回环 host / 插件预设校验，再确保后台可达；已有健康后台
  直接复用（`--preset` 只影响本次拉起，不改变已跑后台）。后台未启动时由本次运行 detach 拉起并等
  `/health` 就绪。`start.py` 起的开发后台也可直接连接，它的锁与退出仍由开发终端管理。目录 B 里敲
  `qianfan repl`，新建任务归 B；`/use` 接回的任务仍回到它自己的目录。
- **认证材料**：bearer token 只在 `Authorization` 头与内存里；本地 secret 登录时只放进
  `POST /api/auth/token` 的请求体。两者都不进 URL、命令行输出或日志。
- **断线不重放**：服务不可达、认证过期（401）、后台准入拒绝（409）都打印原因；断线或重连后先同步
  权威任务快照，**不自动重发**创建 / 续说等可能已成功的修改请求。
- **输出**：渲染任务 / 步骤 / 工具 / 模型调用摘要 / final answer 与「停下」「出错」两类终态，纯
  `print`、不引 Rich / TUI；模型调用只打一行摘要与读取指针
  `/api/tasks/<task_id>/trace?type=llm_call&call_id=<call_id>`。**思考块（reasoning）只在网页端显示**。
  入口把 stdout / stderr `reconfigure(encoding="utf-8", errors="replace")`，Windows GBK 控制台不会因
  emoji / CJK 崩溃。
- **不重开交互确认闸门**：危险动作仍由设置里的 `policy_rules` 规则表在钩子处**当场拒绝**（ADR 0008
  决策 6），终端也不弹 y/n。

## 4. 计划、续说与续跑

- **两模式**：一条 loop、一个 `agent` 节点；模式只换 system prompt 和工具面。`plan` = 只读
  `explore` 档（派发时也拒绝档外工具），`build` = 全部已挂载工具。默认 `build`，开关黏性。
- **停下的三种落点**：本轮最后一次 `agent` 调用没有工具调用（正常轮末）；收到停止请求或轮内步数到
  `max_steps`；前两者进 `finish`、状态 `INTERRUPTED`。LLM 错误进 `FAILED`。用户可见状态三值
  `RUNNING` / `INTERRUPTED` / `FAILED`（`PENDING` 只是创建到 worker 抢到之间的瞬间），没有「完成」。
- **续说**：`POST /api/tasks/{id}/messages` 带新消息，沿同一 `thread_id` / checkpoint 继续；正常轮末、
  停止、`FAILED` 都可以续说（前提是有 checkpoint）。
- **续跑**：`POST /api/tasks/{id}/resume` 不带新消息，只接受已停下（`INTERRUPTED`）且存在 checkpoint
  的任务；不满足这两个条件返回 409。
- **计划文件**：计划模式下模型把计划写成 Markdown，系统存到任务目录的
  `.qianfan/plans/<task_id>.md`（唯一来源）。计划区显示原文、可整份编辑
  （`PUT /api/tasks/{id}/plan` 写回文件），用外部编辑器改同样生效；执行时计划以一条消息进入对话，
  下一条消息按文件当前内容跑。任务目录被删后续说 / 续跑返回 409。
- **checkpoint**：`SqliteSaver` + `durability="sync"`；格式 v2，v1 旧库按
  [迁移文档](migration-langgraph-1x.md) §2.4 拒绝挂载。
- **步数上限**：`max_steps` 按**轮**计，不是整段对话累计。

## 5. 模型与 Provider

- **v2 配置**：Provider（地址 / 协议 / Key）+ 模型目录（一个 Provider 下挂多个模型条目），设置页按
  Provider 卡片 + 模型目录展示。旧扁平 `/api/models` 投影与 CRUD 已移除（ADR 0013），设置页只读
  `GET /api/providers` + `/api/providers/{id}/models`。
- **预置与模板**：后端预置的 Provider 种子是 **DeepSeek**；「添加模型提供商」对话框另有内置的
  **OpenCode Go** 模板（预填 Provider ID、名称、地址、协议与环境变量名，不预置模型、不写存储），
  配置与排障见 [opencode-go.md](opencode-go.md)。
- **协议**：支持 **OpenAI 兼容** 与 **Anthropic 原生**（非流式）；Anthropic 的「获取可用模型」
  暂不支持。`thinking` / `reasoning_effort` 可配置，OpenAI 兼容路径经 `extra_body` 透传；
  Anthropic 路径只接受 thinking 关闭 / reasoning_effort 为空——**写面**（设置页 / API）会 400；
  手改 `data/models.json` 不经过写面校验，运行时才抛 `ValueError`（见
  [ADR 0011](adr/0011-model-config-two-protocols.md)）。
- **Key 来源**：界面粘贴（存 `~/.qianfan/credentials.json`，仓库外，明文）优先；其次配置里写的
  环境变量名（如 `LLM_API_KEY` / `DASHSCOPE_API_KEY`）。接口只回「已设 / 未设」与来源（粘贴 /
  环境变量 / 无），不回 Key 本身。保存 Key 失败时界面显示后端返回的原因与下一步（#244）。
- **选择与切换**：新建任务默认用全局默认，创建前也可先选一套；任务里可中途换模型，下一条消息生效；
  子代理与压缩摘要跟随父任务的模型。
- **采样参数**：挂在模型条目上、Provider 可给默认值——`temperature`（缺省 0.2）、`max_tokens`、
  `thinking` / `reasoning_effort`（限 `low` / `medium` / `high`）；参数可显式清空回落到
  Provider / 缺省。
- **坏文件与旧格式**：旧格式 `data/models.json`（无 `schema_version` 的扁平 profile）不再迁移：
  加载时明确拒绝且原文件字节不变，请在设置页重新添加 Provider 与模型。JSON 解析失败、结构不是对象或
  未来版本会 quarantine 后 seed；v2 里单条非法 provider / 条目只丢弃该条。**非 UTF-8 文件目前可能
  直接报解码错误**，后续修复会按坏文件处理。
- **明文与条款**：粘贴的 Key 明文落盘，同账号的 `shell` 读得到；Coding Plan 条款通常不允许自建
  客户端调用，用「自定义」后果自担。

## 6. 能力包与预设

- **六个能力包**：`files` / `shell` / `skills` / `web-search` / `subagent` / `mcp`；工具面与注入面
  按包声明装配。
- **三档预设**：`minimal`（files + shell）、`research`（files + shell + skills + web-search +
  subagent + mcp，当前与 full 同一面）、`full`（全部，**默认 = 原行为**）。
- **保存与生效**：保存态在 `data/plugins.json`，格式 `{preset, overrides}`；overrides 叠在 preset 上
  （`true` 强制开 / `false` 强制关），由设置页「能力」区或 `PUT /api/plugins` 写入，**重启后台生效**。
- **一次性覆盖**：`qianfan --preset <id>`（网页后台）或 `qianfan repl --preset <id>`（终端），不写
  文件。优先级：**CLI（本次运行）> `data/plugins.json`（文件存在即权威）> `PLUGINS_PRESET` 环境变量
  （仅文件缺失时回退）> 默认 `full`**。
- **真关**：关掉的包不实例化、不进 schema / dispatch / 注入、不连 MCP（不承诺模块不被 import）；
  子代理实际面 = 固定档 ∩ 已启用包 ∩ 父任务已挂载工具。
- **错误口径**：未知 preset / 包 id 启动即错；`data/plugins.json` 的 JSON 语法 / 校验错误会指出路径
  与修法。**CLI 一次性覆盖不豁免坏文件**——文件非法时即使带 `--preset` 也启动即错（文件仍是权威配置，
  不允许静默绕过；先删除或修好文件）。**非 UTF-8 的 `plugins.json` 目前可能直接报解码错误**，后续修复
  会转成同样的可读错误。
- **`GET /api/plugins`**：`saved` 是文件内容（含 `exists`），`active` 是实际装配面；文件存在且
  preset / overrides 与 active 不同时 `restart_required=true`。

## 7. 技能与命令菜单

- **技能发现**：按 home 层 `~/.agents/skills` + 任务目录 `.agents/skills` 两层发现，同名合并成一行；
  选中后插入 `使用技能「name」：` 引用，由 `load_skill` 读两层。没有 `@file` 语法。
- **输入框命令**：输入 `/` 唤起命令菜单（`/new` `/stop` `/plan` `/build` `/model` `/resume`
  `/tasks` `/help`），↑ / ↓ 移动、Enter / Tab 执行。
- **技能菜单**：`/` 菜单在命令之后列出两层技能（按 `/` 后的文字过滤）；选中只插入引用文本，不直接执行。

## 8. 观测与排障

- **SSE + trace**：SSE 事件流 + JSONL trace；主任务 / 子代理 / 压缩的模型调用按 HTTP 尝试写入同一
  trace（脱敏请求、完整响应 / 错误、实际 usage，未返回则未知），SSE 只带轻量摘要与读取指针。
- **前端 Trace 区**：按模型调用展开读取请求 / 响应 / 错误并标记截断或未完成，也可回放 / 导出完整
  trace。终端只打印一行调用摘要与 `/api/tasks/<task_id>/trace?type=llm_call&call_id=<call_id>` 指针。
- **常见排障**：
  - **缺 `frontend/dist` / 产物过期**：`qianfan` 会提示；在 `frontend/` 下 `npm ci && npm run build`。
  - **Key 写失败**：Key 文件在仓库外的 `~/.qianfan/`；进程被沙箱 / 权限边界挡住写该目录时，界面
    显示后端返回的原因与下一步（#244）——用普通终端启动 `qianfan`，或改用该配置的环境变量。
  - **401**：`auth_enabled=true` 时 token 为空 / 占位会拒绝启动；REST 只认 `Authorization: Bearer`，
    `?token=` 只给 SSE 与文件下载两个浏览器原生入口。
  - **409**：续跑要求 `INTERRUPTED` + checkpoint；删除运行中任务、续说目录已被删的任务、
    停止 `start.py` 开发后台都会返回 409，按提示先停止 / 恢复目录 / 回开发终端。
  - **Shell 选择**：缺 `pwsh` 或 Git Bash 时给可读错误；可在配置里设 `pwsh_path` / `bash_path`。
  - **搜索源**：duckduckgo 不可达时切 `search_provider=exa` 或 `serpapi` 并配好 Key。
  - **文件预览失败**：按状态给原因——0 连不上后台、400 路径越界、401 登录失效、404 任务或文件不存在、
    409 任务工作区不存在、415 二进制不可预览、200 响应无法解析，其余状态显示 HTTP 码与后端原因。
