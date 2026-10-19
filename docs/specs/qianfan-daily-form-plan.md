# qianfan 日常形态：全库实施规划（草案）

> **状态：现役规划**（2026-10-20 随 D0 落库；用户批准开 D0 PR）。基线 `master` = `0a28035`。对外入口：spec [#166](https://github.com/renjianguojinqianfan/langgraph-agent/issues/166)。
> 决策权威是 [ADR 0010](../adr/0010-launch-dir-workspace-and-model-profiles.md)（产品形态补充）与 [ADR 0009](../adr/0009-minimal-agent-chapter.md)（本章目标）；
> 本文只管**切片、验收、风险**，不重复决策理由。原切片表（[决策日志](../capability-registry-rescope.md) §0.9）的 C4–C7 在本文里**重排并扩充**，
> 票面以本文 §4 为准；§0.9 只加一行指针，正文不动。
> 实施仍按 [`docs/agents/workflow.md`](../agents/workflow.md)：qoder 实施 + `code-review` 自评审 + `neat-freak` 收尾 + PR；opencode 独立评审；评审侧 / 用户合并。

## 1. 一页结论

- **串行 11 片，一片一个 PR**（几乎每片都碰 `task_manager.py` / `nodes.py` / `config.py`，并行必撞，沿用 G-30）：

```
D0 文档 → C4 命令 → S1 bash 后台进程 → W1 目录属于任务 → W2 共用后台 → W3 跳过忽略文件
       → C5 工作区文件 → C6 计划文件 → M1 模型配置·后端 → M2 模型配置·界面 → C7 收尾 → D1 文档全量审计
```

- **先保后端**：前端只做最小接线（沿用现有样式），Markdown 排版、beautiful-ui、#54 放下一章。
- **零新增依赖**：git 判断忽略文件走子进程（本章本来就要求 Git Bash）；锁文件、凭据文件、模型清单都是标准库 JSON。
- **四处要你单独点头的副作用**：删 CI 的 `docker-build` job（C4）；改写 `AGENTS.md`「密钥」不变量（M1）；关 #148（S1 合并后）；所有 GitHub 开票 / 改票（§9 草稿，G-32）。

## 2. 当前架构证据（改动点）

只列本规划要动的地方；行号锚在 `0a28035`。

| 面 | 现状 | 证据 |
|---|---|---|
| 启动 | 两进程：`start.py` 起 `uvicorn --reload` + `npm run dev`，不开浏览器、不 build 前端、不探测已运行实例；后端不托管 `frontend/dist`；`pyproject.toml` 头注「不是可安装包」、无 `[project]` | `start.py:47-53,74-84`；`backend/main.py:54-80`（无 StaticFiles）；`pyproject.toml:1-14` |
| 前端取 API | 同源相对路径 `/api`；开发态靠 vite 代理到 8000 | `frontend/src/api/client.ts:14`；`frontend/vite.config.ts:8-15` |
| 跨域 | 留空或 `*` 都回退成 `*`（审计 F6） | `backend/config.py:193-194` |
| Docker | `Dockerfile` + `docker-compose.yml`（监听 `0.0.0.0`，审计 F1）+ CI `docker-build` job | `backend/Dockerfile:1-17`；`docker-compose.yml:1-22`；`.github/workflows/ci.yml:12,137-146` |
| 工作区根 | **全局**：唯一来源 `artifacts_dir` → `artifacts_path`（按 `PROJECT_ROOT`）；文件工具在 `__init__` 定死根；bash / 技能工具每次调用读 settings；注入块每个 runtime 算一次 | `config.py:18,59,130-135`；`backend/core/tools/file_io.py:72`；`bash.py:305,313`；`skill_tool.py:66-68`；`backend/core/agent/inject.py:49` |
| 工具实例 | `TaskManager.__init__` 建一次，所有任务与子代理共用；子代理从 `tm._tools` 选面 | `backend/services/task_manager.py:189`；`registry.py:55-57`；`subagent.py:299-301,348-356` |
| MCP | 后台启动时连一次，子进程 cwd 默认 = 进程 cwd | `task_manager.py:194,380-429`；`backend/core/mcp/client.py:48,202` |
| 「当前任务」上下文 | bash 的停止检测借用回滚快照模块的 contextvar | `task_manager.py:372-377`；`backend/services/snapshots.py:59-74`；设置点 `task_manager.py:514,837,1038`、`subagent.py:315-316` |
| 任务记录 | `Task` 无目录、无模型字段；`tasks.json` 是整份数组，启动读一次、每次保存整份原子替换，**只有进程内锁** | `backend/api/schemas.py:108-131`；`backend/services/persistence.py:39-125` |
| 按任务可切的字段（模板） | 只有 `mode`：`PUT /api/tasks/{id}/mode` → `set_mode`，每轮播种 | `routes.py:119-137`；`task_manager.py:923-936,526,879,1067` |
| 计划 | 结构化 `Task.plan` / `PlanStep`、`PUT /plan`、`plan_update`；计划块拼在 system 尾部 | `schemas.py:40-50,116,138-145`；`routes.py:140-156`；`task_manager.py:938-977`；`nodes.py:140-219,302-340`；`prompts.py:19-75` |
| 产物与回滚 | `registers_artifact` 登记、产物路由、快照 capture / restore / cleanup、回滚接口与按钮 | `nodes.py:565-586`；`task_manager.py:1106-1195`；`snapshots.py` 全文件；`routes.py:183-202,256-294`；`frontend/src/components/ArtifactList.tsx`、`TaskHeader.tsx:22-109` |
| 模型 | 只 OpenAI 兼容；预设 openai / deepseek / ollama；设置 `lru_cache` 启动读一次；**全进程一个客户端**（runtime、子代理、压缩摘要共用）；思考内容已原样回传 | `backend/core/llm/openai_compat.py:43-150`；`config.py:37-44,205-208`；`task_manager.py:188,548,850,1048`；`subagent.py:351`；`nodes.py:194,334-335,379-380,438-439` |
| 文件遍历 | glob `base.glob`、grep `sorted(base.rglob("*"))`、ls `iterdir`，**无任何跳过清单** | `file_io.py:385-422,425-527,530-586` |
| bash 后台进程（F3） | shell 一退出就跳出轮询；两个读管道线程各 `join(timeout=5)` 串行 ≈ 10 秒；shell 已退出时 `_kill_tree` 直接返回，后台子进程不被收；Windows 无进程组 | `bash.py:186-187,311-325,339-360` |
| SSE 事件面 | 事件名是任意字符串；权威表在 architecture §3.4；`check:sse` 四处对齐 | `backend/services/event_bus.py:34-48`；`docs/architecture.md:352-392`；`frontend/scripts/check-sse-event-registry.mjs` |

## 3. 目标形态与差异

| 类别 | 内容 |
|---|---|
| **保留** | 一条 loop、两模式、状态三值、10 个工具 + MCP、只让本机访问（127.0.0.1 + Host 检查）、危险规则钩子、续说 / 续跑、注入 / 驱逐 / 压缩、trace、登录（默认关）、离线模拟模型、`start.py`（只作开发） |
| **新增** | `qianfan` 命令（同源托管 + 开浏览器 + `--port` / `--no-browser`）；任务的 `workspace` 与 `model` 字段；按任务取根目录的运行上下文；「当前目录 / 全部」列表过滤；单实例锁文件与交接；忽略文件判断；`.qianfan/plans/`；模型配置清单 + 凭据文件 + 设置接口 + 设置页 + 模型下拉 |
| **删除** | `Dockerfile` / `docker-compose.yml` / CI `docker-build`；产物账本与回滚快照（C5）；结构化计划（C6）；预设 openai / ollama（M1，DeepSeek + 自定义取代）；跨域「留空 = `*`」 |
| **迁移** | 旧任务补 `workspace = data/artifacts`（读 `tasks.json` 时补齐）；`.env` 的 `LLM_*` 自动成为第一套模型配置；「当前任务」contextvar 从 `snapshots.py` 搬出（W1，给 C5 删快照让路）；全局单客户端 → 按配置缓存的多客户端（M1） |
| **数据位置** | 不变：`data/`、`.env`、traces、checkpoint 仍在仓库（editable 安装）。新增：`data/server.json`（锁）、`data/models.json`（模型清单，不含 Key）、`~/.qianfan/credentials.json`（粘贴的 Key，仓库外）、`<任务目录>/.qianfan/plans/` |

## 4. 切片

每片的通用判据：`ruff check backend scripts` 与 `mypy` 0 错、`pytest backend/tests/ -q` 全绿（既有测试只增不减，删测试逐条写明对应哪个已删功能）；动到前端的片 `npm run typecheck` / `npm run build` 通过；动到 SSE 事件的片同步 architecture §3.4 并跑 `npm run check:sse`；动到工具面 / 钩子 / 密钥的片评审走 `security-review`。

### D0 文档：本规划落库（docs PR）

- 内容：ADR 0010（`proposed` → `accepted`）、本文、`docs/security-audit-2026-10-19-bash.md`（#148 审计原文，现本地未跟踪）；`docs/agents/domain.md` 的 ADR 清单加 0010；`docs/capability-registry-rescope.md` §0.9 表头下加一行「排序与扩充见 `docs/specs/qianfan-daily-form-plan.md`」；`AGENTS.md` 顶段 ADR 0009 指针旁补 0010。
- 判据：只动上述文件；CI docs 面绿。

### C4 `qianfan` 命令（#159，校准）

- 内容：`pyproject.toml` 包信息与 `[project.scripts] qianfan`（头注同步改）；后端同源托管 `frontend/dist`（路径走一个配置字段，默认 `PROJECT_ROOT/frontend/dist`，便于以后分发时搬）；缺 `dist` 给可读提示；自动开浏览器（`--no-browser` 关）；`--port`，端口被占报错提示；**F6**：跨域留空不再回退 `*`；**删** `backend/Dockerfile`、`docker-compose.yml` 与 CI `docker-build` job（⚠ 改 CI，PR 正文单列）。工作区本片**不动**（仍是 `data/artifacts`，W1 才改）。
- 依赖装法：开工先实测 `uv tool install -e .` 配 `--with-requirements` / `--overrides` / `--excludes` 哪种组合能复现 `requirements.txt` 版本（决策日志 §0.4），实测命令写进 PR。
- 判据：干净环境装出的版本与 `requirements.txt` 一致、`qianfan` 无 ImportError；在仓库外任意目录敲 `qianfan` 浏览器自动打开、只听 127.0.0.1；跨域留空时只放自身来源（新测试）。
- 随片文档：README 启动段（日常 `qianfan`、开发 `start.py`、删 Docker 段）；`.env.example` 的 CORS 注释；`pyproject.toml` 头注。

### S1 bash 后台进程不拖住调用（新票，引用 #148 F3）

- 内容：shell 退出后不再无界等待继承管道（`bash.py:339-360`）；停止 / 超时时连后台子进程一起收（Windows 需要能收「shell 已退出、孙进程还活着」这种情形，`bash.py:186-187` 现在直接返回）；读管道线程不泄漏。实现手段（Job Object / 进程组 / 有界 drain）在票里定，先写红测试。
- 判据：`sleep 60 &` 这类命令调用立即返回（额外开销 < 1 秒量级，按实测写进 PR）；停止任务后后台进程不存活；调用返回后无残留读线程；Windows 本机实测 + Linux CI 平台相关测试按 `os.name` 取夹具。
- 随片文档：README「这不是安全边界」一节按审计改准——**F7**「停止杀整棵进程树」按修后事实写、「只支持本机」照实写；**F8** 工作区外（`data/`、`.git`、`.env`）bash 可读写；**F4** 无 CPU / 内存 / 磁盘上限。合并后（你点头）关 #148。

### W1 工作区属于任务（新票）

- 内容：
  - `Task` 加 `workspace`（绝对路径）；建任务时取「当前目录」——由网页地址里的目录参数带来，没有参数时用后台的启动目录；读 `tasks.json` 时给旧任务补 `data/artifacts`。
  - 新增「运行上下文」（当前任务 id + 工作区根，contextvar），把 `snapshots.py:59-74` 的 `current_task_id` 搬进来；文件工具、bash、技能工具、注入块一律从运行上下文取根，不再在 `__init__` 定死（`file_io.py:72`）；子代理继承父任务的目录。
  - 列表接口按目录过滤（默认当前目录、`all` 看全部）；续说回到任务自己的目录，目录不存在时拒绝续说（新增一类 409，写明原因）。
  - 前端：历史列表「当前目录 / 全部」切换、任务头标出目录。
  - MCP 保持后台共用一份（ADR 0010 决策 1），README 写明其工作目录不随任务。
- 判据：两个临时目录各建一个任务，断言 read / write / edit / ls / glob / grep / bash / load_skill / 注入的 `AGENTS.md` 都落在各自目录（含子代理）；旧格式 `tasks.json` 读入后字段补齐；目录被删的任务续说返回 409。
- 随片文档：`GLOSSARY.md`「工作区」重写；README 工作区段（在真实项目里干活、撤销靠 git、在主目录启动不拦）；`AGENTS.md` 两层注入说明里「工作区根」改为「任务目录」。

### W2 一台机器共用一个后台（新票）

- 内容：`data/server.json`（pid、端口、启动时间、一次性标识）原子写入；`qianfan` 启动时若已有存活且 `/health` 标识对得上的后台，就把浏览器开到 `http://127.0.0.1:<端口>/?dir=<当前目录>` 并退出；锁残留（进程已死 / 标识对不上）则接管；端口被非 qianfan 程序占用报错。`start.py` 的开发后台同样认这把锁（两者不能同时写 `data/`）。
- 判据：A、B 两个目录先后敲 `qianfan`，只有一个后台进程、浏览器各自定位；杀掉后台后再敲能接管残留锁；`start.py` 在 qianfan 运行时拒绝启动后台（或反之），给出提示。
- 随片文档：README（第二次启动的行为、关掉终端 = 全部停下）。

### W3 搜索与文件列表跳过忽略文件（新票）

- 内容：`glob` / `grep` 跳过 `.git` 与 `.gitignore` 忽略的文件：git 仓库内用 `git ls-files --cached --others --exclude-standard` 一次取清单，非 git 目录只跳过 `.git`；`ls` 与点名 `read` 不变；后续 C5 文件列表复用同一判断。注意 `.qianfan/` 自带 `*` 忽略，计划文件因此不出现在搜索与文件列表里（计划区单独显示，属预期）。
- 判据：临时 git 仓库里 `node_modules/`、被忽略文件、`.git/` 不出现在 glob / grep 结果；非 git 目录行为不变；既有上限（500 条 / 1MB）照旧。
- 随片文档：工具说明（tool description）写明「跳过 .gitignore」。

### C5 工作区文件（#160，校准）

- 内容：照票面——「产物」栏换「工作区文件」列表（预览 / 下载 / 路径锁 / 拒二进制 / 大文件截断 / 流式下载），删产物账本与回滚快照（按钮、接口、`artifact_created` / `task_rollback`、`snapshot_*` 配置、启动清理）。校准两点：范围 = **当前任务的目录**且复用 W3 的忽略判断；删 `snapshots.py` 前确认 W1 已把「当前任务」上下文搬走。
- 判据：票面原判据 + `check:sse` 过 + 文件列表里看得到 bash 写的文件。
- 随片文档：照 §0.9 C5 行（ADR 0004 退役、ADR 0008 第 8 条改写等）；另加 ADR 0009 决策 6 指向 ADR 0010 的修订标注。

### C6 计划文件（#161，校准）

- 内容：照票面，校准两点：路径改为 `<任务目录>/.qianfan/plans/<task_id>.md`，首次创建 `.qianfan/` 时写入只含 `*` 的 `.gitignore`；计划区**显示原文**（等宽、不排版）+ 整份编辑——票面「排版后的 Markdown」改口径，Markdown 排版放下一章。
- 判据：票面原判据（含 #153 回归测试）+ 用户项目的 `git status` 不出现 `.qianfan/`。
- 随片文档：照 §0.9 C6 行；ADR 0009 决策 7 路径指向 ADR 0010。

### M1 模型配置·后端（新票）

- 内容：
  - 模型清单 `data/models.json`（id、名称、地址、模型名、环境变量名、是否预设），**不放进 `Settings`**（避开 `lru_cache`）；首次启动由 `.env` 的 `LLM_*` 生成第一套；预设只留 DeepSeek，删 openai / ollama 预设。
  - 凭据 `~/.qianfan/credentials.json`：粘贴的 Key 按配置 id 存；取 Key 顺序 = 粘贴 > 环境变量（启动时继承的那份）；接口只回「已设 / 未设」，Key 不进日志、trace、SSE、`tasks.json`。
  - `Task` 加 `model`（配置 id），建任务取全局默认；`PUT /api/tasks/{id}/model`（照 `mode` 的模式），下一条消息生效；子代理、压缩摘要用任务的模型；trace 每次调用记模型。
  - 客户端按配置缓存，替换 `tm._llm` 单例（`task_manager.py:188,548,850,1048`、`subagent.py:351`、`nodes.py:194`）。
  - 设置接口：列出 / 新增 / 修改 / 删除配置、设全局默认、「测试」（发一个最小请求）。
  - 中途换模型：历史里的 `reasoning_content` 对下一家是否报错，真模型实测；报错就在换模型时剥离。
- 判据：离线测试覆盖取 Key 顺序、Key 不出现在任何接口响应 / 日志 / trace（断言）、换模型下一条生效、子代理继承；真模型实测 DeepSeek ↔ 另一家中途互换各一次。
- 随片文档：`AGENTS.md`「密钥」行按 ADR 0010 改写（⚠ 不变量变更，PR 正文单列）；README 模型一节（设置页、Key 两种来源、粘贴的 Key bash 读得到、Coding Plan 条款说明）；`.env.example` 的 `LLM_*` 注释改为「只用于生成第一套配置」；ADR 0009 决策 4 与「组成」表「模型连接」行的修订标注。

### M2 模型配置·界面（新票）

- 内容：沿用现有样式做最小设置页（列表、新增 / 编辑 / 删除、Key 输入框只写不读、环境变量名、已设 / 未设、测试、设为默认）；输入框旁加模型下拉（照 `ModeSwitch` 的做法）；任务头显示当前模型。
- 判据：`typecheck` / `build` 过；网页上完成「新增一套 → 测试 → 设为默认 → 新任务用它 → 中途切回」。
- 随片文档：README 截图位留到 C7。

### C7 收尾（#162，校准）

- 内容：照票面，校准三点：真模型场景从 5 个扩到 **9 个**（§6）；残影检查词加 Docker / 快照 / 回滚 / `PlanStep` / openai 与 ollama 预设；ADR 修订核对加 ADR 0010 列出的四处 + `AGENTS.md`「密钥」行；README 放一张现有界面截图。
- 判据：9 个场景逐个跑通并写进 `docs/` 的清单；关 #155。

### D1 文档全量审计（新票，C7 之后）

- 内容：`docs/` 全部条目 + 根目录文档（README / OVERVIEW / GLOSSARY / AGENTS）逐篇判「现役 / 过时」；过时的 `git mv` 到 `docs/archive/`，同 PR 改掉仓库内引用；ADR 不移动，退役的原地加标记；`docs/agents/domain.md` 文件树同步。
- 判据：仓库内无指向旧路径的链接（脚本扫一遍）；现役文档与代码一致（抽查到 `file:line`）。

## 5. 文档面

| 文档 | 动作 | 哪片 |
|---|---|---|
| `README.md` | 收成「是什么 + 一条命令 + 一张截图」，只用中文；启动 / 工作区 / 共用后台 / 模型 / 安全段随片改 | C4、S1、W1、W2、M1、C7 |
| `AGENTS.md` | ADR 0010 指针（D0）；两层注入改「任务目录」（W1）；「密钥」行改写（M1）；两模式行计划路径（C6） | D0、W1、C6、M1 |
| `GLOSSARY.md` | 「工作区」重写；`plan` 词条路径；新增「模型配置」 | W1、C6、M1 |
| ADR 0009 | 决策 4 / 6 / 7 与「组成」表两行加指向 0010 的修订标注，正文不改 | C5、C6、M1 |
| ADR 0008 / 0003 / 0004 | 照 §0.9 原计划 | C5、C6 |
| `docs/architecture.md` §3.4 | 删 `artifact_created` / `task_rollback` / `plan_update`；M1 若新增事件同步 | C5、C6、M1 |
| `.env.example` | CORS、`snapshot_*` 删除、`LLM_*` 注释 | C4、C5、M1 |
| `docs/capability-registry-rescope.md` | §0.9 加一行指针，正文冻结 | D0 |
| 其余 `docs/` 历史文档 | 本章不逐篇改，统一交 D1 判定归档 | D1 |

## 6. 验收：9 个真模型场景（C7 跑，网页手动）

原有 5 个（G-28）：① 执行模式新建文件 + bash `git init && git status`；② 出计划文件 → 改文件 → 切执行 → 按改后的做；③ 跑到一半停止 → 续说接着跑；④ 配一条危险规则 → 调用被拒、模型换路；⑤ 工作区文件列表看得到 bash 写的文件。

新增 4 个（ADR 0010）：⑥ 多项目——A 目录敲 `qianfan`，工作区是 A、列表只显示 A 的任务；B 目录再敲，同一个后台、浏览器定位到 B；从「全部」点开 A 的任务续说，改动落在 A。⑦ 关掉 qianfan 再打开：历史还在，停下的任务能续说，跑一半被关的能续跑。⑧ bash 起后台进程：调用马上返回，停止任务时后台进程被收（F3 回归）。⑨ 派一个子代理、调一个 MCP 工具、搜一次网，各跑通一次；中途换一次模型，下一条消息用的是新模型。

## 7. 风险与未决

| # | 风险 | 应对 | 在哪片 |
|---|---|---|---|
| R1 | `mcp` 要求 `uvicorn>=0.31.1`，仓库钉 `<0.31`，`uv tool install -e .` 可能装不出钉住的版本 | 开工先做依赖实验（§0.4），不通就停下回报，不擅自抬版 | C4 |
| R2 | 有工具仍读全局根，任务目录「看着生效、其实漏一个」 | W1 判据逐工具断言（含子代理、注入）；grep 全仓 `artifacts_path` 读点清零到运行上下文 | W1 |
| R3 | 删快照会顺带打断 bash 停止检测（共用 contextvar） | W1 先搬走，C5 删除前复核 `task_manager.py:372-377` | W1、C5 |
| R4 | MCP 工作目录不随任务 | 本章接受并写进 README；需要时另开票 | W1 |
| R5 | `start.py` 开发后台与 qianfan 同时写 `data/` 会丢任务 | 两者共用 W2 的锁 | W2 |
| R6 | 锁残留 / Windows pid 复用 | 锁里放一次性标识，用 `/health` 回读比对，不只看 pid | W2 |
| R7 | 在真实项目里 bash 与写工具直接改文件 | README 写明撤销靠 git；不加信任提示（ADR 0010 决策 6） | W1 |
| R8 | 大仓库里每次 grep 都跑一次 `git ls-files` 太慢；嵌套仓库 | 先实测本仓与一个带 `node_modules` 的项目；慢再做按调用缓存 | W3 |
| R9 | 中途换模型后历史不兼容（思考字段、工具调用 id、上下文窗口不同） | M1 真模型互换实测；压缩阈值是否要随模型变，实测后定 | M1 |
| R10 | 粘贴的 Key 明文落盘、bash 可读 | 放仓库外、Windows 用户目录默认只本人可访问；不进日志 / trace / SSE 的断言测试；README 写明 | M1 |
| R11 | 删 CI job 是流水线变更 | PR 正文单列，等你点头 | C4 |
| R12 | Coding Plan 条款 | 不做预设，README 写明；自定义后果自担 | M1 |
| R13 | 改前端后忘了重新 build，`qianfan` 看到旧界面 | 缺 / 旧 `dist` 给提示；README 写开发流程 | C4 |
| R14 | 归档移动打断 issue 里的外链 | 已接受；仓库内链接同 PR 改 | D1 |

**未决（不阻塞本章）**：下一章做什么（前端 beautiful-ui / #54 二选一、Markdown 库、插件 / 预设、分发）；beautiful-ui 是否取代 #54 的视觉方向。

## 8. 不做什么（及重开触发）

| 不做 | 触发再议 |
|---|---|
| exe / PyPI / 给别人用 | 要给别人用或换电脑——分发那一章，另起 ADR |
| 远程访问 | 同上（ADR 0009 重开条件） |
| Anthropic 原生协议、账号订阅登录（OAuth） | 要接 Anthropic 自家 API 或账号订阅 |
| Coding Plan 预设 | 条款放开第三方客户端 |
| Markdown 排版、beautiful-ui、#54、终端对话（#20） | 下一章前端 |
| 插件 / 预设（#67） | 插件那一章 |
| 危险规则表搬进设置页 | 你觉得改 `.env` 不顺手时 |
| `--workspace` 参数、从接口拉模型列表、目录信任提示、系统钥匙串 | 日常用出痛点再加 |
| 多个后台并行、MCP 按任务目录 | 先换掉 `tasks.json` 单写者存储 / 有真实需要 |

## 9. GitHub 动作草稿（G-32：你说「执行」再执行）

- **已执行**：spec 票 [#166](https://github.com/renjianguojinqianfan/langgraph-agent/issues/166)（2026-10-20，经 `/to-spec` 发布，标签 `spec` + `ready-for-agent`）——本文的需求面与测试接缝以它为对外入口，后续新票挂在它下面。
- **改票（末尾追加校准段，原文一字不删）**：#159 C4（F6、删 Docker 与 CI job、工作区本片不动）；#160 C5（范围 = 任务目录 + W3 判断、依赖 W1）；#161 C6（`.qianfan/plans/` 路径、原文显示）；#162 C7（9 个场景、残影词、ADR 0010 核对、截图）；#155 父票（切片重排，链到本文）。
- **开新票**：S1、W1、W2、W3、M1、M2、D1，票面按 §4 对应小节写，标签按 `docs/agents/triage-labels.md`。
- **#148**：S1 合并后评论审计结论（链到审计文档）并关闭。
- **PR**：D0 走 docs 分支 + PR（G-33：不提交 `prototype/`、`.agents/`、`S4-test-deletions.md`）。
