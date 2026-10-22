---
status: accepted
---

# 日常形态：启动目录即工作区、共用一个后台、多套模型配置

> **本文是 [ADR 0009](0009-minimal-agent-chapter.md) 之后的产品形态补充决策**：2026-10-19 用户拍板（一轮 grilling 逐题走完）。
> **2026-10-22 已交付**：现役以 `AGENTS.md`「跨任务安全边界」表 + 代码为准。切片、验收与风险见
> [实施规划](../specs/qianfan-daily-form-plan.md)；本文只记决策，不复制切片细节（沿用 ADR 0008 / 0009 的「文档分工」）。

## 背景

ADR 0009 定了「`qianfan` 一条命令启动」，但没有回答「在哪儿干活」「用哪个模型」：

- 工作区固定在仓库里的 `data/artifacts`（`backend/config.py:59-60` 的 `data_dir` / `artifacts_dir` 与 `:128-133` 的派生路径，按 `PROJECT_ROOT` 解析），没有办法指到别处——要在自己的项目里用，得先把项目拷进去。
- 所有任务混在一个 `data/tasks.json` 里；每个进程启动时读一次、每次保存整份覆盖（`backend/services/persistence.py:44,81-95`），两个进程共用就会互相吃掉任务。
- 模型只认 `.env` 里的一套，启动时读一次（`config.py:200-203` 的 `lru_cache`），全进程一个客户端（`backend/services/task_manager.py:199`），网页上没有任何设置面。

用户这一章的用途：**学习和展示为主，日常顺手也不能少**；只有自己、只在这台 Windows（Git Bash）上用；展示对象是 GitHub 访客（README + 截图）与自己学习（代码和文档讲清原理）。

## 决策

1. **工作区 = 启动目录，并且属于任务**：在哪个目录敲 `qianfan`，新任务就在哪个目录读写、跑 bash、读那里的 `AGENTS.md` 与技能目录。目录记在任务上：续说时回到任务自己的目录（界面标明是哪个目录），目录不在了就只能看。不加 `--workspace` 参数（先 `cd`）。MCP 服务仍是整个后台共用一份，工作目录是后台的启动目录、不随任务切换（`backend/core/mcp/client.py:48,202`），README 写明。先例：pi 取启动目录（`packages/coding-agent/src/main.ts:594`）、codex 无 `--cd` 时取当前目录（`codex-rs/cli/src/cloud_config.rs:42`）。
2. **历史列表默认只列当前目录的任务，可切「全部」**。先例：pi 选择器默认「Current Folder」可切「All」（`session-selector.ts:131,713`）、codex `resume` 默认按目录过滤、`--all` 看全部（`cli/src/main.rs:360-362`）、opencode 默认按当前子路径（`packages/tui/src/context/sync.tsx:160-172`）。旧任务没有目录字段，统一记为仓库的 `data/artifacts`。
3. **一台机器共用一个后台**：第二次敲 `qianfan` 发现已有后台在跑，就把浏览器开到它、定位到新目录，不另起进程；`tasks.json` 因此始终只有一个写入者。关掉启动它的终端 = 所有任务停下（之后可续跑）。先例：codex 共享后台发现已运行即报 `AlreadyRunning`（`app-server-daemon/src/lib.rs:408-418`）、opencode 从 v1 的「每次一个服务」改成 v2 的共享服务（`cli/src/server-process.ts:152-169`）。端口默认 8000、`--port` 可改，被别的程序占用就报错提示；`--no-browser` 关自动开浏览器。
4. **计划文件路径改为 `<任务目录>/.qianfan/plans/<task_id>.md`**，目录里放一个只含 `*` 的 `.gitignore`（pytest / ruff 缓存目录的做法），不出现在用户项目的 `git status` 里。计划的其余语义（系统替模型存文件、计划模式严格只读、整份重写、删了算无计划、执行模式下模型改文件算更新）全部照 ADR 0009 决策 7 / 8 与决策日志 §0.4。先例：opencode 把计划放在项目内 `.opencode/plans/`（`packages/opencode/src/session/session.ts:331-335`）。
5. **搜索与文件列表跳过 `.git` 与 `.gitignore` 忽略的文件**：作用于 `glob` / `grep` 与界面的工作区文件列表；`read` 点名读仍可读。由 git 自己判断（本章本来就要求 Git Bash），不加依赖；非 git 目录只跳过 `.git`。先例：pi 的 find / grep「Respects .gitignore」（`packages/coding-agent/src/core/tools/find.ts:78`、`grep.ts:78`）。
6. **安全姿态不变，外加三条**：照 ADR 0009 决策 4 / 5（只让本机访问、危险规则默认空表、bash 拿账号权限、工作目录不是围墙）。在用户主目录或盘根启动**不检查**，README 写清（pi 的立场；codex 的目录信任提示 `tui/src/onboarding/trust_directory.rs` 被否）。删除 `backend/Dockerfile` 与 `docker-compose.yml`（不用 Docker；compose 监听 `0.0.0.0` 即审计 F1）；`start.py` 只作改前端时的热重载开发启动。
7. **多套模型配置，任务中途可换**：设置页管理多套「地址 + 模型 + Key」；内置预设只有 DeepSeek，其余用「自定义」。只支持 **OpenAI 兼容**接口（所列各家都提供）。思考内容（`reasoning_content`）现已原样回传（`backend/core/agent/nodes.py:334-335,379-380,438-439`；DeepSeek 带工具时不回传会报 400）；中途换模型后，历史里带着上一家的思考内容，要实测下一家不报错，必要时在换模型时剥离。有一个全局默认，任务可以中途换、下一条消息生效、正在跑的那一步不变；子代理跟随父任务的模型；trace 每次调用记下所用模型。先例：dsh（`packages/client/ui-model-selection/README.md:14`）、opencode（`packages/opencode/src/session/prompt.ts:663-687`）、pi（`src/core/agent-session.ts:637`）都是「全局默认 + 会话中途可换」。现有 `.env` 的 `LLM_*` 自动成为第一套配置。
8. **Key 两种来源都留**：每套配置可以引用一个环境变量名（界面显示「已设 / 未设」），也可以在界面里粘贴，存进**仓库外**的 `~/.qianfan/credentials.json`；先用粘贴的那份，没有再读环境变量。环境变量只读启动时继承的那份。模型配置清单本身（不含 Key）存在仓库 `data/` 里。先例：dsh 凭据文件 > 启动时环境变量（`packages/credentials/credentials-local/README.md:71-78`）、pi `auth.json` > 环境变量（`docs/models.md:23`）、opencode `auth.json`（`packages/opencode/src/auth/index.ts:10,79`）——三家都允许落盘明文，都没用系统钥匙串。
9. **Coding Plan 不做预设**：百炼、智谱、火山方舟的 Coding Plan 条款禁止在自建客户端 / 应用后端里调用（违规可能停订阅、封 Key），Kimi Code 仅限交互式使用且禁止伪造客户端标识；只有 MiniMax M Plan 写明任意 OpenAI / Anthropic 协议工具可用。用户可以用「自定义」自行填写，后果自担；README 写明原因。
10. **前端先保后端**：本章沿用现有样式，只做最小接线（工作区文件列表、计划区原文显示 + 整份编辑、「当前目录 / 全部」切换、任务目录标注、模型设置页、输入框旁的模型下拉）。Markdown 排版、[beautiful-ui](https://github.com/slev12397/beautiful-ui)、#54 重设计放下一章一起定。
11. **文档治理**：README 只用中文，形如「是什么 + 一条命令 + 一张截图」。过时文档移到 `docs/archive/`、同 PR 改掉仓库内引用（issue 里的外链会断，已接受）；ADR 不移动，退役的原地加标记。全量文档审计单开一张票，排在 C7 之后。

## 被否的选项

| 题 | 选定 | 被否 | 否掉的理由 |
|---|---|---|---|
| 用途 | 学习展示 + 日常顺手 | 本章就当实验台 | 插件 / 预设留下一章（ADR 0009 决策 1） |
| 用户 | 只有自己、这台 Windows | 跨平台；给别人用 | 跨平台要把审计 F2 从 N/A 变成要修；给别人用 = 分发那一章 |
| 交互面 | 终端启动、网页对话 | 全终端（像 pi）；两套都要 | 现有网页直接复用，新活最少 |
| exe | 本章不做 | 本章就做 | 要搬数据目录、打包前端、验证冻结，和 `uv tool install -e .` 效果一样 |
| 工作区 | 启动目录 | 固定 `data/artifacts`；默认固定 + 参数指定 | 日常要拷项目进去，不顺手 |
| 跨目录续说 | 在任务自己的目录续 | 只能看；每次问（codex 式） | 每次问会让计划与改动留在旧目录、对不上 |
| 启动两次 | 共用一个后台 | 只许一个、报错退出；多个各自独立 | 多个独立要先把 `tasks.json` 换成多写者存储，改动最大 |
| 计划文件 | 项目内 `.qianfan/plans/` | 项目根 `plans/`；qianfan 自己的数据目录 | 根目录被污染；放工作区外则执行模式下模型改不了、ADR 0009 决策 7 要改 |
| 忽略文件 | 跳过 `.git` + `.gitignore` | 固定名单；全部列出 | 名单补不全；全部列出在带 `node_modules` 的项目里很慢 |
| 启动位置 | 不检查 | 打印警告；目录信任提示 | 信任之后权限也不变小，徒增一层界面 |
| 界面 | 不重设计、最小接线 | 本章做 #54；答案渲染 Markdown | 用户：「前端先不急，先确保后端功能稳定」 |
| 旧启动方式 | 删 Docker 两件，`start.py` 留作开发 | 全删；都留并标注 | 都留则 GitHub 上三种启动方式并存且 F1 仍在 |
| 过时文档 | 移到 `docs/archive/` | 顶部标「历史」原地留；直接删 | 用户要目录干净 |
| 模型切换 | 全局默认 + 任务中途可换 | 只一个全局；建任务时选定 | 两个任务不能并用不同模型；回看不知当时用的哪个 |
| 接口格式 | 只 OpenAI 兼容 | 同时支持 Anthropic | 所列各家都给 OpenAI 兼容地址 |
| 预设 | 只 DeepSeek + 自定义 | 常用七家；保留 openai / ollama | 用户：「内置个 deepseek 的即可」 |
| Key 来源 | 环境变量 + 界面粘贴落盘 | 只认环境变量（启动读 / 实时读注册表）；Windows 凭据管理器 | 用户看过 dsh / opencode / pi 三家都「两样都留」后选定；凭据管理器要加依赖且同账号 bash 仍取得到 |

## 与 ADR 0009 的关系

**不动**：工具面（决策 2 / 3）、只让本机访问（决策 5）、计划交给执行阶段的方式（决策 8）、运行时部件（决策 9）、界面原则（决策 11）、「本章不分发」与 `uv tool install -e .`（决策 10）。

**修订**（随改代码的那一片落，不提前改）：

- 决策 4 「子进程只放行环境变量白名单，密钥不进 bash」：环境变量白名单照旧；但粘贴进 `~/.qianfan/credentials.json` 的 Key **bash 读得到**，这句改为如实描述（随模型配置那一片落）。
- 决策 6 「整个工作区、不分任务」：改为「当前任务所在目录、跳过 `.git` 与被忽略的文件」（随工作区文件那一片落）。
- 决策 7 计划路径 `plans/<task_id>.md` → `.qianfan/plans/<task_id>.md`（随计划文件那一片落）。
- 「最简 agent 由什么组成」表的「模型连接」「启动」两行（随对应片落）。

**`AGENTS.md`「跨任务安全边界」的「密钥」行**改写为：Key 只存在仓库外的凭据文件或环境变量里；不进仓库、日志、trace 与 SSE；界面只显示「已设 / 未设」；`.env` 的 `llm_api_key` 仍保持留空（随模型配置那一片落）。

## 三笔账（明说的代价）

1. **安全**：工作区从仓库里的沙箱目录变成你真实的项目，bash 与写工具直接作用在项目上，撤销靠 git；粘贴进界面的 Key 以明文落在 `~/.qianfan/`，模型用 bash 能读到——这是 pi / opencode / dsh 的共同立场，README 写清。
2. **复杂度**：任务多两个字段（目录、模型）；后台要同时服务多个目录；单实例要有锁文件与交接，进程崩溃后要能识别残留的锁。
3. **订阅**：手上的 Coding Plan 多半不能合规地用在 qianfan 上，只能用按量 Key 或 MiniMax M Plan。

## 重开条件

- 给别人用、跨平台或打包 exe——属分发那一章，另起 ADR。
- 要支持 Anthropic 原生协议或账号订阅登录（OAuth）。
- 某家 Coding Plan 条款放开第三方客户端，再议预设。
- 需要多个后台并行（例如不同端口各跑一套）——先换掉 `tasks.json` 的单写者存储。
- 工作区要回到「固定沙箱目录」，或计划文件要搬出工作区。
