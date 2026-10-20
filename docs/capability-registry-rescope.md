# 能力包线 re-scope 取证（D6）

> 状态：**已拍板**——§0 决策日志记全局规划访谈（2026-10-18 – 10-19）的逐条结论；权威以 [ADR 0009](adr/0009-minimal-agent-chapter.md) 为准，本节退为过程记录。§1 起是 2026-10-17 的证据与候选处置，与 §0 冲突处以 §0 为准。
> 时点：2026-10-17，基线 `master` @ `bd3d6a5`（S0–S6 已合入）。
> 范围：能力包线（#67 / #78 / #79 / #81 / #86 / #118）及其活 spec；顺带记录被同一批旧概念污染的票面。
> 方法：读 16 张开放票正文 + `docs/specs/*` + `docs/adr/0008` + `docs/agents/domain.md`，用 `rg` 对照现役代码。

## 0. 决策日志（全局规划访谈，2026-10-18 起）

> **工作日志**：逐条记访谈过程；结论已按 G-31 落 [ADR 0009](adr/0009-minimal-agent-chapter.md)（取代 G-3），权威以 ADR 为准。
> 比方（全程沿用）：核 = 相机机身；可拆装的能力 = 镜头；镜头接到机身上的统一接口 = 卡口。

| # | 日期 | 结论 | 理由 | 被否选项 |
|---|---|---|---|---|
| G-1 | 2026-10-18 | 「心脏小」= **耦合小**：机身只认卡口，不点名任何具体功能。尺子从「行数」换成「机身（含装配段）里点名具体功能的地方」：现状 8 + 3 处（§0.3），目标 0；加卡口后核总行数可能略涨，接受 | 用户 2026-09-24 的原话式结论「功能数量不是罪，焊死才是」 | ① 代码行数少（pi 式：复杂度挪进宿主，开关并未变容易）；③「这一章少做」未单独表态，留给方向题的范围取舍 |
| G-2 | 2026-10-18 | 唯一真实场景：**从最小机身起步，一支支加镜头，看每加一支行为怎么变**（看得清 / 比得了），人在网页上手动对比 | 用户选 (b) | (a) 为变轻而关掉不用的；(c) 加新能力（点不出名字 →「加功能不改核」不是当前需求）；(d) 第三方插件（进程内全权限代码，没有信任模型）；(e) 拿去展示 |
| G-3 | 2026-10-18 | 结论先记在本节，**暂不写 ADR**；界面与功能理清后再落 | 用户：「还没理清楚最终的界面和功能」 | 现在开 ADR 0009；另建 `docs/global-plan-decisions.md`（落点日后可挪） |
| G-4 | 2026-10-18 | 10 张方向无关的票按 §0.1 处置；GitHub 上的关票 / 改票面**暂不执行**（#54 / #113 / #20 的重写取决于界面与功能） | 用户「按表执行」，理解为按表定处置 | — |
| G-5 | 2026-10-18 | 文档小修只做 3 条，攒到访谈结束一次提交（推送 / PR 届时再问）：`docs/archive/pivot-plan-build.md:3` 改 S0–S6；README 33 / 34 / 44 行与 GLOSSARY 27 / 41 行补 `/api` 前缀（README:33 的 `POST /resume` 实为 `/api/tasks/{id}/resume`）；`docs/specs/p0-b-completion-verification.md` 加退役抬头。另 3 条作废：`second-tier` spec 头部已写明 A、B 全退役；「约 50 行」在仓库文件里无落点；`domain.md:36-39` 已写明冻结只覆盖已交付增量。两处过时代码注释（`config.py:38` 的 `mock` provider、`openai_compat.py:125-126` 的确认闸门）另开小票，不混进 #153 | 用户「按你说的做」 | 交接文档原列 6 条全做 |
| G-6 | 2026-10-18 | 开关形态**参照 dsh**：插件可以启用 / 停用，内置几套固定的**预设**组合（用户原话「模式」，记作「预设」，避免和计划 / 执行两模式撞名）。拨在哪一层（整台服务 / 每个任务）、在哪改（配置文件 / 设置页）未定 | 用户：「大概就是像 dsh 一样，可以启用不同的插件，一开始也可以预设几种默认固定的模式」 | 第二轮 Q6 推荐的「每个任务开局选」未被采纳，层级留给第三轮 |
| G-7 | 2026-10-18 | 看行为变化靠**步骤视图**就够（今天的界面：每步调了什么工具、拿回什么）；不做「每次调用发给模型的完整请求」视图，trace 不加请求事件。**原则：研究用也要把自己当用户，界面不能杂乱** | 用户：「iii 有点太重了，ii 就够了……在界面上不能太过杂乱」 | 旅程视图（太重）；两任务并排比；只看结果 |
| G-8 | 2026-10-19 | **本章目标：先做出一个相对稳定、最简、像 pi 一样能启动的 agent**；插件 / 预设机制（G-6）推后到之后的章节，本章不做 | 用户：「Q9 先不做……最紧急的是先做出一个相对稳定最简的 agent，就像 pi 一样可以启动」 | 本章就做插件层级（第三轮 Q9 的 A / B / C） |
| G-9 | 2026-10-19 | **新增 bash 工具，工具面模仿 pi**（read / write / edit / bash + MCP）；git、OpenAPI 专用工具删除，`code_exec` / `http_request` 并进 bash。逐个工具的去留在第四轮定 | 用户：「pi 只有 read，write，edit，bash 和 mcp 等四五个工具，我觉得可以模仿」；pi / dsh 都没有 git / OpenAPI 专用工具 | 保留 git / OpenAPI 专用工具 |
| G-10 | 2026-10-19 | **知识库整块删除**：`kb_query` / `memory_search`、产物自动入库、「📚 知识库」面板、`/api/kb*` 接口、`kb_*` 配置 | 用户选 (1)；pi / dsh 都没有知识库 | 留成默认关的插件；改成自动注入上下文 |
| G-11 | 2026-10-19 | #113 由「重写」改为**关闭**（GitHub 执行待点头） | G-7 否掉了旅程视图 | 重写 |
| G-12 | 2026-10-19 | **启动形态 = 网页，一条命令起来**，像 `dsh web`；用户举例的命令名是 `qianfan`。打包方式与命令名待第五轮 | 用户：「b；就像 dsh 的启动方式是 dsh web 一样……比如我在终端输入 qianfan 就能启动」 | 终端对话界面（#20 留后）；两者都要 |
| G-13 | 2026-10-19 | 工具：**保留 web_search、子代理、MCP**；删除 `code_exec` / `http_request`（并进 bash）、git ×7、OpenAPI、知识库 ×2、插件目录自动加载与示例插件 `example_echo`。ls / glob / grep / `load_skill` 是否也在「其余删」之内，待第五轮确认 | 用户：「web-search，子智能体，mcp 保留，其余删」 | 第四轮建议的「删 web_search、删子代理」（被用户否） |
| G-14 | 2026-10-19 | **产物账本、回滚快照都删**；「产物」栏换成「工作区文件」列表：整个工作区、不分任务、能预览下载、不登记不去重，bash 写的文件也在。撤销改动以后靠 git | 用户选 (2)；pi 两样都没有，dsh 只有可选的交付物插件、没有回滚；加了 bash 后两样都只认 write / edit，留着是「看着有、其实不全」 | 都删不补；只删回滚 |
| G-15 | 2026-10-19 | **删除「重新规划」**：输入栏按钮、`POST /api/tasks/{id}/replan`、replan 提示词与重试逻辑，以及 README / architecture 里的说明。计划模式本身保留（ADR 0008） | 用户：「重新规划不符合我的想法，直接先删掉」 | 保留 |
| G-16 | 2026-10-19 | 其余运行时部件（存档、注入、驱逐、压缩、危险规则、重试熔断、步数上限、trace、登录）**先对照 pi / dsh 再定去留**；对照结果见 §0.5，去留在第五轮 | 用户：「存档等其他部件先参照 dsh 和 pi，决定去留」 | — |
| G-17 | 2026-10-19 | #67 系列：#67 **保留**，改写成「以后那一章：插件 + 预设（G-6）」；#78 / #79 / #81 / #86 / #118 **关闭**；`docs/specs/capability-package-architecture.md` 加退役抬头。GitHub 执行待点头 | 用户「按表执行」；插件已推后（G-8） | 全部关闭；按旧形态重写 |
| G-18 | 2026-10-19 | ls / glob / grep / `load_skill` **留下**。最终工具面：read、write、edit、bash、ls、glob、grep、load_skill、web_search、spawn_subagent（10 个）+ MCP；计划模式只读档 = read、ls、glob、grep、load_skill、web_search（6 个） | bash 进不了计划模式与只读子代理，这四个是那两处的「眼睛」；`read` 只能读工作区，用户目录的技能只有 `load_skill` 读得到；pi 也带 grep / find / ls | 一并删除（计划模式只剩 read + web_search） |
| G-19 | 2026-10-19 | 子代理执行档**不带 bash**；ADR 0008 第 7 条的修订只是从两档里删掉 git ×4、知识库 ×2，不新增 | 子代理权限边界由 #47 / #74 反复收口过 | 执行档加 bash |
| G-20 | 2026-10-19 | **默认只让本机访问**：监听 `127.0.0.1`、跨域只放行自己的页面，和 bash 同一个 PR 落地；dsh 式「每次启动随机令牌」留作后续加固 | 用户选 (a)；加 bash 后现默认值（`0.0.0.0` + 跨域 `*` + 登录关）等于对局域网与任意网页开放命令执行 | 随机令牌一起做；维持现状 |
| G-21 | 2026-10-19 | 运行时部件按 §0.5：**留**存档 / 停下 / 续说 / 续跑（含悬空调用清扫）、上下文注入、大结果驱逐、历史压缩、危险规则钩子、每轮步数上限、trace 文件、计划 / 执行两模式、登录（默认关）；**删**辅助模型、工具自动重试与熔断 | 用户「按表执行」；pi / dsh 都没有工具重试与熔断，也都没有专用摘要模型 | 全留；连计划模式一起删（= 重开 ADR 0008） |
| G-22 | 2026-10-19 | `qianfan` 走 **Python 命令**（`[project.scripts]` + `uv tool install`），后端同源托管 `frontend/dist` 并自动开浏览器；命令名暂定 `qianfan`（用户两次用这个名字举例）。要不要「分发给别人用」、走 PyPI / npm / exe 哪条，待第七轮 | 用户选 (a)；deepagents CLI、open-swe、Open WebUI 的共同做法（§0.6） | 学 deer-flow 多进程脚本；改用 `langgraph dev` + 托管 Studio |
| G-23 | 2026-10-19 | **#147 关闭**（bash 进四件套后按工具面分档失去意义；「默认问人」被 ADR 0008 第 6 条排除；安全口径照 D1 (b)：不拦、写清楚、要隔离用容器）；**#148 改写**为「bash 工具安全审计」，排在 bash 之后，先摘 `ready-for-agent`；**#145 保留**（删 git 工具后点名 `git_*` 的旧规则变死规则）；README「这不是安全边界」段补一句：bash 拿的是你账号的权限，要隔离就放容器 / 虚拟机。GitHub 执行待点头 | 用户「按表执行」 | 保留 #147 的工具面档位 |
| G-24 | 2026-10-19 | 用户要求：**计划应当是一份文件，用户改的是那份文件**，不是现在框里的一行行短句。形态待第七轮（依据：别家 agent 的计划形态调研 + 现状截图）。ADR 0008「与 pi 的有意分歧」表里「结构化的 `Task.plan`（逐条状态、界面上直接编辑）」一行届时需修订；不动单 loop 与两模式 | 用户：「计划模式如果产出了计划文件我应该修改的是计划这个文件，而不是现在这样一个框里的一段文字」 | 维持结构化列表 + 单行输入框 |

> **Q26（稳定的验收）**：用户只对计划场景提出改法（G-24），其余三条未反对，暂按「四条照收、计划场景随 G-24 改写」记，第七轮复述时确认。

| # | 日期 | 结论 | 理由 | 被否选项 |
|---|---|---|---|---|
| G-25 | 2026-10-19 | **本章不分发**：只用 `uv tool install -e .` 装给自己。分发放下一章，主渠道 PyPI + uv（对方只需装 uv）；npm / exe 以后按需在外面套壳。本章写代码不新增「依赖源码目录」的路径 | 用户选 (a)；分发要先搬配置与数据（`config.py:18,31`）、打包前端、解决 Windows 上找 shell（§0.8），会拖住「稳定」 | 本章就发 PyPI；本章就做 exe / npm |
| G-26 | 2026-10-19 | **本章不支持远程访问**：README 不写远程段；Host 检查（G-35）只认本机地址，远程本来就连不上，所以第六轮 Q28 的远程模式（改监听 + 扩 Host 允许名单 + 必须开登录，否则拒绝启动）整块不做；只在 `.env.example` 的 `HOST` 旁加一行注释「本章只支持本机访问」 | 用户：「先不开远程访问，README 里写一小段是否会导致 README 信息过多」 | 做远程模式；README 写远程段 |
| G-27 | 2026-10-19 | **计划由系统替模型存成文件**：模型把计划写成 Markdown 放在回复里、用约定标记包住，系统认出后存成文件；计划模式仍严格只读，ADR 0008 第 7 条不动；改计划时模型整份重写 | 用户选「甲」；少一道路径白名单，只读边界不开口子 | 模型自己写文件、计划模式放行一个路径（opencode / kimi / Claude Code 式） |
| G-28 | 2026-10-19 | 「稳定」验收四条确认：① 门禁全绿；② `qianfan` 零报错起来、自动开浏览器、只听 127.0.0.1；③ 真模型网页跑 5 个固定场景（计划场景 = 出计划文件 → 网页或自己的编辑器里改 → 切执行 → 按改后的文件做）；④ 删掉的东西不留残影 | 用户「确认」 | — |
| G-29 | 2026-10-19 | **计划以一条消息进入对话**（不再钉在系统提示尾部）：codex / dsh / kimi 式。压缩挤掉或文件被改之后怎么补，第九轮在问 | 用户选 (B)，否掉推荐的 (A)「钉在系统提示里」 | (A) 钉在系统提示（#119 现行）；(C) 只给路径（opencode 式） |
| G-30 | 2026-10-19 | **本章七片，串行**，第一个 PR = C1；切片见 §0.9 | 用户「按表执行」；几乎每片都改 `task_manager.py` / `nodes.py` / `config.py`，并行必撞 | 并行；先修 #153 |
| G-31 | 2026-10-19 | **ADR 0009 现在写**（`docs/adr/0009-minimal-agent-chapter.md`），进本次 docs PR；ADR 0008 的修订与 0003 / 0004 的退役标记**随改代码的那一片落**；本节退为过程记录，权威以 ADR 0009 为准。取代 G-3 | 用户「现在写」；G-3 的前提（界面与功能理清）已满足 | 等全部切片做完再写 |
| G-32 | 2026-10-19 | GitHub 动作**先出草稿**，用户说「执行」再执行 | 用户选 (c) | 现在全做；只关票改票 |
| G-33 | 2026-10-19 | 文档走 **docs 分支 + PR**；不提交 `prototype/`、`.agents/`、`S4-test-deletions.md` | 用户「pr」 | 直推 master |
| G-34 | 2026-10-19 | 补 G-29 的缺口：**只在两个时机检查——每轮开始前（用户发新消息）与每次压缩之后**；文件与对话里那份不一致、或那份已被挤掉，就**删掉旧的那条、在末尾放最新一份**（对话里始终只有一份计划），并留一行灰字「计划已更新到第 N 版」。任务跑着时改文件，下一条消息生效 | 用户选 (1′)；压缩后从源头补回有先例（Claude Code 补计划，kimi / hermes 补 todo），先删旧份再补有先例（hermes）；本仓默认截断压缩会把计划整条丢掉，不补不行（§0.6b） | (1) 每次调用模型前比对（无先例，一轮中途换计划）；(3) 不补（opencode / codex / dsh 靠摘要留影子，本仓截断不留） |
| G-35 | 2026-10-19 | **补记第六轮 Q24（原漏记）**：加 **Host 检查**——只接受 Host 为 `127.0.0.1` / `localhost` 的请求（Starlette 自带 `TrustedHostMiddleware`，一行配置、不加依赖），堵 DNS 重绑定；和 bash、只听本机同一个 PR（C3） | Q24 推荐「加」；用户追问「加了之后不适合分发吧」，第七轮答「分发不受影响：每人在自己电脑上用 localhost 打开，只挡远程访问」后未再反对；第八轮切片表 C3「加 Host 检查」获「按表执行」（G-30）。PR #154 评审时发现漏记 | 不加（只靠监听 127.0.0.1 + 跨域收紧） |

> **G-24 后续（已定）**：文件放工作区 `plans/<task_id>.md`、计划区换成整份 Markdown（显示排版 + 一个大文本框编辑）、去掉逐步状态——第七轮推荐、用户未反对，第八轮切片表 C6 获「按表执行」（G-30）。**执行阶段怎么拿到计划**被用户追问（「计划在确认后需要每次都重新输入上下文吗？不应该这样吧」），由 G-29 / G-34 定。事实：模型调用不保留记忆，每次都要重发「系统提示 + 至今对话」；默认压缩策略只留最近 10 条消息、更早的换成占位符（`backend/core/agent/context.py:402-422`，`config.py:62,64`），系统提示不在压缩范围内（`nodes.py:269`）。

> **现状截图（2026-10-19，离线模拟模型，数据隔离在 OS Temp，不进仓库）**：计划区是对话流里的一块卡片，3 行单行输入框 + 每行一个「待办 / 进行中 / 完成」下拉 + 「+ 添加一步」「保存计划」；聊天气泡只剩「计划已更新（3 步）。」，模型写的计划正文不保留。原因在 `backend/core/agent/prompts.py:26-29`（要求只返回 JSON 字符串数组）与 `backend/core/agent/nodes.py:271-311`（只取每项的一行描述）。

> **连带（G-8 – G-10，落 ADR 时要写）**：ADR 0008 第 7 条需**修订**（不是重开）——计划模式的只读档要去掉 4 个 git 工具和 2 个知识库工具。本章方向接近交接文档的 B（只做最小 agent），但插件叙事不退役，只是推后；旅程视图已否。
> **连带（G-13 – G-15）**：子代理保留，ADR 0008 第 7 条「子 agent 保留」不动，ADR 0003 不因子代理而退役。ADR 0004 退役，ADR 0008 第 8 条改写为「交付 = 停下时去看工作区里的文件」，AGENTS.md「产物登记语义」一行删除，P1-B spec 加退役抬头。
> **连带（G-21）**：工具重试与熔断删除后 ADR 0003 失去对象，加退役标记；AGENTS.md「熔断与工具挂载」一行改写；`tool_*` 5 项与 `aux_llm_*` 6 项配置删除；工具调用记录去掉 `retries` / `circuit_open`。D1 定案 (b)（危险规则默认空表、不拦，`docs/archive/research-danger-policy-across-agents.md` §6.4）**继续有效**，bash 不附默认黑名单。

> **更正（2026-10-18）**：第二轮把「产物账本」「回滚快照」划进机身是错的——pi 与 dsh 的核里都没有这两样（pi 只在示例里用 `git stash` 做存档点；dsh 的 `deliverables` 是可选插件，只进 web-app 组合，也没有回滚）。归属改由第三轮决定。dsh 事实取自本机克隆 `687f282e86`（`dsh-v0.2.1-rc.2`），**比 `docs/archive/research-dsh-plugin-core.md` 记的 `d743267388` 新**，引用旧文行号前要复核。

### 0.1 票面处置（G-4）

| 票 | 处置 | 理由 |
|---|---|---|
| #153 | 保留，**并入 C6**（G-30 否掉「先修 #153」；G-34） | 已上线 bug，S5 网页验收实未完成；优先级说明、回归测试与网页重验随 C6 落 |
| #148 | **改写**为「bash 工具安全审计」，排在 bash 落地之后；先摘 `ready-for-agent`（G-23） | 审计对象从 `code_exec` 换成 bash |
| #147 | **关闭**（G-23） | bash 进四件套后按工具面分档失去意义 |
| #145 | 保留（G-23） | 删 git 工具后点名 `git_*` 的旧规则会变死规则 |
| #141 | 保留，独立 chore | 「冻结区解除」只是历史理由 |
| #106 | **C5 合并时关闭**（G-14） | 产物账本随 C5 删除，票面失去对象（原定「改票面后保留」）；C1–C4 期间 bug 仍在，故不提前关 |
| #117 | 关闭 | 对照的两节点形态与 live 脚本已删；材料 B 的 cache 实测是「要不要改成单 agent + 模式切换」的门槛，该形态已由 ADR 0008 定下并落地，门槛失去作用，不另开 |
| #113 | **关闭**（G-11） | 旅程视图被 G-7 否掉 |
| #54 | 重写票面 | 留四栏 / token / a11y；删确认弹窗 / COMPLETED / 冻结区；补计划区、模式开关、停下 / 续说 |
| #20 | 重写票面 | 一条 loop + 两模式 + 停下 / 续说；交互式终端有人在场，不碰 ADR 0008 |
| #67 | 保留，改写成「以后那一章：插件 + 预设（G-6）」（G-17） | 想法没作废，留一张票占位，排在最简 agent 之后 |
| #78 / #79 / #81 / #86 | 关闭（G-17） | 地图、Batch 0、四批迁移、minimal profile 都建立在旧四节点与件清单上 |
| #118 | 关闭（G-17） | 三轴参照材料留在票里，本节留链接 |

### 0.2 由 G-1 / G-2 推出的约束（后续轮次不得违背）

- 对比只靠人工：做成自动跑分 = 恢复无人值守评测 = 重开 ADR 0008（其「重开条件」第 2 条）。
- 「关」必须是真关：关掉的镜头不能还有工具摆在模型面前、还往上下文里加东西，否则对比不干净。现成反例：关知识库后 `kb_query` / `memory_search` 仍在——`backend/core/tools/registry.py:137-139` 全量建工具，`backend/services/task_manager.py:209-210` 只摘 `spawn_subagent`。
- 只装本仓自己的代码：不做第三方插件，插件机制不当沙箱。

### 0.3 焊线现状（G-1 尺子的基线，2026-10-18 清点）

- 装配段点名 8 个具体功能：子代理、知识库、辅助模型、回滚快照、trace 在 `backend/services/task_manager.py:35-47` 直接 import；MCP / OpenAPI / git 在 `task_manager.py:196-203` 经 `_load_*` 懒加载。
- 节点里 3 处：借子代理的只读档（`backend/core/agent/nodes.py:112`）、拼 trace 路径（`nodes.py:210-211`）、专认 `spawn_subagent`（`nodes.py:676-681`）。
- 开关全是 `.env` 布尔（`backend/config.py:86-138`），网页与后端都没有设置面。辅助模型与 checkpoint 的「关掉」和「开着但没配好 / 被拒挂」是同一个空值；OpenAPI 两种情况都不留记录。

### 0.4 实现语义（已定）

设计树已走完（2026-10-19）。PR #154 复核确认三处实现语义（用户同意，2026-10-19）：

- **C6 计划文件被删**：算「无计划」——文件是唯一来源，下次核对时把对话里那份也删掉。
- **C6 模型写计划文件**：不拦，算计划更新，照 G-34 换上新版；优先级说明因此写成「以计划文件当前内容为准」。
  工具层守卫只能挡 `write` / `edit`，bash 不受约束（D1 (b)），拦不彻底。
- **C4 依赖装法**：开工实测 `uv tool install -e .` 配 `--with-requirements` / `--overrides` / `--excludes` 的哪种组合；
  判据 = 干净环境装出的版本与 `requirements.txt` 一致、`qianfan` 无 ImportError；实测命令写进 C4 PR 正文。

### 0.9 本章切片（G-30）

> **2026-10-20 起 C4 及以后的排序与内容以 [`docs/specs/qianfan-daily-form-plan.md`](specs/qianfan-daily-form-plan.md) §4 为准**（[ADR 0010](adr/0010-launch-dir-workspace-and-model-profiles.md)，spec #166）；本节正文冻结为 C1–C3 的过程记录，不再改。

串行，一片一个 PR；每片按 `docs/archive/pivot-plan-build.md` §纪律走（删测试逐条写明删的是哪个已删功能的测试；`AGENTS.md` 局部改动随片；C3 / C5 / C6 合前手跑一次真模型）。动到 SSE 事件的片同步 `docs/architecture.md` §3.4 并跑 `npm run check:sse`（改 architecture 会触发全套 CI）。

| 片 | 内容 | 随片落的文档 |
|---|---|---|
| C1 删功能 | 知识库（两工具、产物自动入库 `kb_auto_index_artifacts`、面板、`/api/kb*`、`kb_*` 配置）、git 工具 ×7、OpenAPI、插件自动加载与 `example_echo`、「重新规划」（按钮、接口、提示词与重试）、辅助模型；只读档与子代理两档去掉 git ×4、知识库 ×2 | ADR 0008 第 7 条修订；README「能力」段（知识库 / Git / OpenAPI / 插件 / 重新规划 / 辅助模型）；`AGENTS.md`「熔断与工具挂载」行的「Git / OpenAPI 由 task_manager 显式追加」半句 |
| C2 删重试与熔断 | 工具只跑一次、失败理由交给模型；删 `tool_*` 5 项、记录字段 `retries` / `circuit_open`、`tool_circuit_open` 事件与前端显示 | ADR 0003 退役；`AGENTS.md` 顶段「熔断」与「熔断与工具挂载」行；`GLOSSARY.md` 运行时概念的「熔断」；`docs/agents/domain.md` 的 0003 live 行；README「韧性」行；architecture §3.4 删 `tool_circuit_open` |
| C3 bash + 只听本机 | 新增 bash（工作目录 = 工作区根、环境变量白名单、超时、停止时杀进程树、输出截断、Windows 找 Git Bash）；删 `code_exec` / `http_request`；默认值改到位：`config.py` 与 `.env.example` 的 `host` → `127.0.0.1`、`cors_origins` → 自身来源；Host 检查（G-35）；`.env.example` 的 `HOST` 旁注「本章只支持本机」、`policy_rules` 示例的 `code_exec` 改 `bash` | README「工具」行（代码执行 / HTTP → bash）；README **新增**「这不是安全边界」一节（现无此节，`GLOSSARY.md` 危险动作条与 `research-danger-policy` §6.4 已指向它） |
| C4 `qianfan` 命令 | pyproject 包信息与 `[project.scripts]`；后端同源托管 `frontend/dist`；自动开浏览器；`uv tool install -e .`（依赖安装方式见 §0.4） | README 启动段；`pyproject.toml` 头注「不放 `[build-system]`、本仓不是可安装包」同步改 |
| C5 工作区文件 | 「产物」栏换「工作区文件」列表（预览 / 下载；文件端点的路径锁与二进制 / 大小限制可沿用 #54 §6 的设计）；删产物账本与回滚快照（按钮、接口、`artifact_created` / `task_rollback` 事件、`snapshot_*` 配置、启动清理） | ADR 0004 退役；ADR 0008 第 8 条改写；`AGENTS.md`「产物登记语义」行删除；P1-B spec 加退役抬头；`GLOSSARY.md` 的 ADR 0004 指针、「交付」与 `artifact` 条；README 身份段的「停下时人看产物」与「回滚」行；`docs/agents/domain.md` 的 0004 live 行；architecture §3.4 删两事件 |
| C6 计划文件 | 计划模式回复里的标记 → 存工作区 `plans/<task_id>.md`；计划区换整份 Markdown（显示 + 编辑）；执行阶段按 G-29 / G-34（计划以一条消息进对话，每轮开始前与每次压缩后核对，删旧补新，留一行灰字「计划已更新到第 N 版」）；优先级说明（#153）；删 `Task.plan` 列表、`PlanStep`、`PUT /api/tasks/{id}/plan`、`plan_update`、状态下拉、system 尾部计划块；§0.4 两问已定 | ADR 0008「与 pi 的有意分歧」表「计划进度」行修订；`AGENTS.md` 两模式行与任务路由行；`GLOSSARY.md` 的 `plan` 词条与模式条；README「计划可改」行；architecture §3.4 删 `plan_update` |
| C7 收尾 | 文档一致性（非归档文档无残影，归档例外按 S6 口径：`docs/archive/pivot-plan-build.md` S6 行）；ADR 0008 三处修订与 ADR 0003 / 0004 退役标记已落；5 个固定场景真模型网页验收（G-28） | 关本章父票（#153 由 C6 关） |

#148（bash 安全审计）在 C3 合并后做。

### 0.10 下一步（按顺序）

1. **docs PR**（本节 + ADR 0009 + 两份调研 + G-5 小修 + 两份 spec 退役抬头）——判据：PR 开出、CI 无关（docs 不进 CI）、用户或评审侧合并。
2. **GitHub 动作**——草稿在会话交接目录、不入库，执行后以 GitHub 票面为准；用户说「执行」后、且本 docs PR 合并后（草稿里的链接才有效），按「开父票与切片票 → 关票 → 改写票 → 留言」的顺序执行，免得关票评论与改写正文里留下未回填的票号——判据：9 张处置各带一句理由（#106 改为挂到 C5、C5 合并时关）、#67 / #148 / #54 / #20 改写、本章父票 + 7 张切片票开出、#153 挂到 C6。
3. **C1 开工**——依赖 1、2；判据：门禁全绿、删除的测试逐条列明、界面与配置里不再有知识库 / git 工具 / OpenAPI / 重新规划 / 辅助模型。

### 0.5 运行时部件对照（G-16 的依据，2026-10-19 读源码）

pi = `E:\code\pi` @ `4dd2b149d`；dsh = `E:\code\deepseek-harness` @ `687f282e86`（rc.2）。行号由子代理读出，引用前以当时 HEAD 为准。

| 部件 | 本仓现状 | pi | dsh |
|---|---|---|---|
| 存档 + 停下 / 续说 / 续跑 | LangGraph checkpoint + `tasks.json`；停下后续说、崩溃后续跑；入口处清扫悬空工具调用 | 宿主：会话 JSONL 树，`-c` / `-r` 续；不能续半截轮次；给模型转换消息时给没回音的工具调用补「No result provided」 | 会话持久化插件（最小组合也装）；续跑时给中断的轮次补收尾；核心里有悬空工具调用修复（`packages/core/session/src/repair.ts`） |
| 上下文注入 | AGENTS.md 两层 + 技能目录 + 环境信息 | 宿主：多层 AGENTS.md / CLAUDE.md；技能清单；环境只放 cwd（日期为提示缓存删了） | 插件：base / web 有，最小组合没有 |
| 大结果驱逐 | 单条 > 4000 字符换成首尾预览 + trace 指针 | 每个工具自己截：2000 行 / 50KB；bash 留尾部、全文存临时文件 | spill 插件（base / web）：超 12,500 token 首尾留在上下文、全文落盘 |
| 历史压缩 | 默认截断，可选摘要 | 接近窗口时用当前模型写摘要，溢出时压缩后重试一次 | 最小组合也有：先剪工具结果，再用当前模型写摘要 |
| 辅助模型（专写摘要） | 有，默认关 | 无，用当前模型 | 默认无，用当前模型，可另配 |
| 危险规则 | 规则表 + 一个钩子（ADR 0008 决策 6），默认空表 | 只有钩子，不带规则表 | 无规则表；靠沙箱档位 + 审批（base / web：workspace-write + ask；最小组合：danger-full-access） |
| 工具自动重试 + 熔断 | 有（`ToolExecutor`；ADR 0003） | **都没有**；只重试模型请求（3 次） | **都没有**；只重试模型请求（5 次）；工具只有超时与「重复调用提醒」 |
| 模型请求重试 | openai SDK 默认 2 次（`.venv311/.../openai/_constants.py:10`） | 有 | 有 |
| 每轮步数上限 | 15 | 无 | 无（文档明说） |
| trace 落盘 | 事件原样落盘；驱逐的占位指针指向它；界面有 Trace 标签页 | 无持续落盘（会话文件 + 崩溃记录 + 按需 debug 日志） | 无独立 trace；会话日志本身能还原每次请求 |
| 计划 / 执行两模式 | 结构性的（ADR 0008） | 只是示例 | 可选插件，只改提示词 |
| 登录 | 有，默认关；监听 `0.0.0.0`、跨域 `*` | 无网页 | 网页版强制开：只听本机，每次启动随机令牌，查 Host / Origin |
| 启动 | uvicorn + `npm run dev` 两个进程 | CLI | `npx @deepseek-ai/dsh web`：起在 127.0.0.1:3080，托管打包好的网页，自动开浏览器 |

### 0.6 别的 LangGraph 项目怎么打包（G-12 的依据，2026-10-19）

子代理用 `gh api` 按提交号读源码与 README；deepagents、langgraph 用的是本机克隆（`2069079e` / `f09cfe8f`，比远端旧，deepagents 的入口在远端 HEAD 未变）。

| 项目 @ 提交 | 怎么装、怎么起 | 网页在哪 | 默认监听 |
|---|---|---|---|
| deepagents CLI（`deepagents-code` @ `2069079`） | 安装脚本内部跑 `uv tool install`，命令 `dcode`；入口 `libs/code/pyproject.toml:154-156` | 只有终端界面；后台以子进程起 `langgraph dev` | `127.0.0.1`，随机端口 |
| langgraph-cli @ `f09cfe8` | `pip install "langgraph-cli[inmem]"` → `langgraph dev`；入口 `libs/cli/pyproject.toml:36-37` | 用官方托管的 Studio（smith.langchain.com），本地不带网页 | `127.0.0.1:2024` |
| agent-chat-ui @ `088908b` | `pnpm dev` | 单独的 Next.js 应用，连 LangGraph 服务 | `localhost:3000` |
| deer-flow @ `62130ca` | `make install` → `make dev`；一条命令起三个进程（uvicorn + Next.js + nginx） | 和后端分开跑，要仓库与 Node | `127.0.0.1`，有登录 |
| open-swe @ `aa00dc8` | `uv run langgraph dev`；`langgraph.json` 的 `http.app` 挂一个 FastAPI | FastAPI 托管打包好的前端（`StaticFiles` + 单页回退），同源 | `localhost:2024`，开发态无登录 |
| Open WebUI @ `91ea4e3`（FastAPI，非 LangGraph） | `pip install open-webui` → `open-webui serve`；前端用 `force-include` 打进安装包 | 和后端同一个进程 | `0.0.0.0:8080`（例外） |

共同做法：`[project.scripts]` 声明命令 + `uv tool install` 装成全局工具；同源托管打包好的前端（open-swe、Open WebUI）；默认只听本机。LangChain 自家项目都没把网页打进 Python 包。

### 0.6b 计划进入执行阶段后，压缩与改动怎么处理（G-29 缺口的依据，2026-10-19）

子代理读本机克隆（`E:\code\` 下）与 Claude Code 官方文档；行号属各自 HEAD。

| agent @ HEAD | 执行阶段计划放哪 | 压缩后还在吗 | 改了计划会重发吗 | 频率 |
|---|---|---|---|---|
| opencode @ `4e1c4963` | 批准时一条合成的用户消息 + 文件路径（`tool/plan.ts:53-69`） | 不在：摘要模板通用，计划与 todo 都不补（`core/src/session/compaction.ts:16-46`） | 不重发，靠模型自己再读文件 | 一次 |
| codex @ `fcbed044` | `<proposed_plan>` 回复 + 「Implement the plan.」 | 被摘要吸收，只保留用户消息与摘要（`core/src/compact.rs:655-743`） | 无计划文件 | 不重发 |
| pi @ `482c1381`（示例） | 一条「剩余步骤」消息 | 当普通消息被截 / 摘要；下一条用户消息时再补一份（`agent-session.ts:1893-1936`） | 不比对，**每条用户消息无条件重发**，旧份不清理、会叠加（`plan-mode/index.ts:177-249`） | 每轮 |
| dsh @ `687f282e` | `exit_plan_mode` 的参数与批准结果 | 被摘要吸收，不补（只有系统提示永不裁） | 无计划文件 | 不重发 |
| kimi-code @ `21406fb4` | 工具结果「## Approved Plan」+ 路径（`exitPlanModeReview.ts:72-78`） | **todo** 压缩后从实时数据补回（`fullCompactionService.ts:844-850`）；**计划不补** | 仅计划模式内重读文件 | 提醒每步跑一次 |
| hermes-agent @ `fcbd1076` | 无计划模式 | **todo** 每次压缩后补回，且**先删旧快照再加新的**（`todo_tool.py:136-141`、`conversation_compression.py:3265-3323`） | 不适用 | 压缩事件 |
| Claude Code（官方文档） | 未核实 | **计划「从磁盘重新注入」**，CLAUDE.md 同样（[context-window](https://code.claude.com/docs/en/context-window)） | 计划专用的改动检测未核实（文档只说「读过的文件在盘上改了会提示」） | 压缩事件 |

要点：① 六个本地仓库**都**把计划放进对话一次，没有一家每次贴进系统提示；② 压缩后从源头补回：Claude Code 补计划，kimi / hermes 补 todo，其余三家任其被摘要吸收；③ **没有一家在「计划文件改了」时自动重发**；④ 「每次调用前比对」无先例，「先删旧份再补新份」有先例（hermes）。

### 0.7 别家 agent 的计划是什么形态（G-24 的依据，2026-10-19）

子代理读本机克隆（`E:\code\` 下）与 Claude Code 官方文档；行号属各自 HEAD。

| agent @ HEAD | 计划是文件吗 | 谁写 | 用户怎么改 | 执行阶段怎么用 | 进度清单 |
|---|---|---|---|---|---|
| opencode @ `4e1c4963` | 是，但只在实验开关下：`.opencode/plans/<时间>-<slug>.md`（`packages/opencode/src/session/session.ts:331-336`） | 模型用普通 write / edit；计划代理只放行这一个路径（`agent/agent.ts:171-175`） | 是 / 否对话框；文件在盘上，无内置编辑器 | 只给路径指针「执行这份计划」（`tool/plan.ts:67`） | `todowrite`，侧栏显示 |
| opencode-v2 @ `46172108` | 否，在对话里讨论（`core/src/plugin/plan.ts:15`） | — | 聊天 | 对话历史 | 已删 |
| codex @ `fcbed044` | 否，回复里的 `<proposed_plan>` Markdown 块 | 模型（回复文本） | 聊天回复，每次整份重写；「实现这个计划？」 | 同一对话，或新线程贴入计划 | `update_plan` 勾选清单（计划模式里禁用） |
| pi @ `482c1381` | 否，从回复里的「Plan:」抽步骤（示例扩展） | 模型（回复文本） | 执行 / 留着 / 细化；「细化」打开的是空编辑器，写的是反馈 | 每轮注入剩余步骤，模型发 `[DONE:n]` | 另一个示例扩展 |
| dsh @ `687f282e` | 否，`exit_plan_mode(plan: string)` 带 Markdown | 模型（工具参数） | 批准 / 继续规划；网页是只读 Markdown 预览 | 留在对话里 | `todo_write` |
| kimi-code @ `21406fb4` | 是：`<会话目录>/agents/<id>/plans/<slug>.md`，每次修订另存版本（`agent-core-v2/src/features/plan/planService.ts:199-240`） | 模型用 Write / Edit；计划模式只许写这一个文件（`planService.ts:259-275`） | 批准 / 拒绝 / 带反馈修订；无内置编辑器（未核实） | 把文件内容注入工具结果「## Approved Plan」（`exitPlanModeReview.ts:72-78`） | `TodoList` |
| hermes-agent @ `fcbd1076` | 是（`/plan` 技能，不是模式）：`.hermes/plans/<时间>-<slug>.md` | 模型用 `write_file`，只靠提示约束 | 聊天或外部编辑器 | 未规定 | `todo` |
| Claude Code（官方文档） | 是：`~/.claude/plans`，可用 `plansDirectory` 改位置 | 模型用 Write（唯一放行的写）——出处为 opencode 收录的 Claude 提醒文本，非 Anthropic 源码 | **`Ctrl+G` 在你的默认编辑器里打开并直接改**；是 / 手动批准 / 继续规划 | 默认同一会话；是否重读文件未核实 | TodoWrite / Task* |

要点：① 做成文件的（opencode 实验版、kimi、Claude Code）都是「模型用普通写工具写一份 Markdown，计划模式只放行这一个路径」；② **只有 Claude Code 明文支持用户直接改计划原文**，其余都是「回一句反馈、模型重写」；没有一家像本仓这样逐行输入框；③ 计划和进度清单是两样东西，进度走单独的 todo 工具。

### 0.8 分发选项（G-22 的依据，2026-10-19）

子代理读一手文档与源码（uv 0.13.1、hatch v1.18.1、PyInstaller v6.22.3、platformdirs 4.13.4、pydantic-settings v2.16.0，npm 包用 `npm view` 实查）。

| 方式 | 对方电脑要装什么 | 一条命令 / 双击 | 主要代价 |
|---|---|---|---|
| PyPI + `uv tool install` / `uvx` | 只要 uv（uv 会自动下载 Python） | 一条命令 | 要先修数据与配置的位置；发布时把前端打进包（`frontend/dist` 被 `.gitignore:26` 忽略，hatch 要用 `artifacts` / `force-include`，同 Open WebUI） |
| npm 包转调 `uvx`（如 skilltotal） | Node + uv / Python | 一条命令 | 两套运行时，npm 包只是转发 |
| npm 按平台带 PyInstaller 二进制（如 sygal-cli，esbuild / biome 同款机制） | 只要 Node | 一条命令 | 每个系统各打一次；sygal 的 Windows 包解压约 208 MB；杀毒误报 |
| PyInstaller / Nuitka 打成 exe（如 Kiln：FastAPI + 网页） | 什么都不用 | 双击 | 每个系统各打一次；杀毒误报常见（PyInstaller 官方说「我们这边无能为力」）；单文件版启动慢 |

当前代码挡住分发的三处：① `backend/config.py:18,31` 把 `.env` 与 `data/` 挂在源码目录上，装进 site-packages 后找不到；打成单文件 exe 时会落进每次退出都删的临时目录（按文档推断，未实测）。约定位置是 platformdirs 的 `user_data_dir` / `user_config_dir`。② bash：别人的 Windows 上可能没有 Git Bash；裸 `bash` 还可能先撞到 WSL 的 `System32\bash.exe`。pi 按「用户配置 → Git 安装目录 → PATH」找 bash，找不到就报错并给安装提示；dsh 在 Windows 上默认改用 PowerShell。③ 没验证过：冻结后的 LangGraph + MCP + sqlite 能不能跑、包有多大。

## 1. 一页结论

1. **`docs/specs/capability-package-architecture.md` 已整体失真**：语义核仍是 `planner → executor → tool → reflect`，`mode="minimal"` 仍是 4 节点，五声明仍含「#58 manifest 件清单行」，验收仍要求「件清单恰好 ±1 行」与「冻结区零触碰」。`run_manifest.py` 已在 S1 删除，四个节点已在 S3/S4/S5 退役，冻结区已由 ADR 0008 解除。
2. **#67 系列六张票不能按现状开工**：不是措辞过时，而是**验收面依赖已删机制**（件清单、headless、live 双场景）。#67 的「排序在 #56 之后」尤其危险——#56 已关闭，容易被读成「现在可开工」。
3. **需要一次拍板，而不是六次**：决定「能力包注册表是否还是下一章」；若保留，再决定它在新架构下的最小形态。其余五张票的处置由这一个答案派生。

## 2. 票面矩阵（候选处置，非结论）

| 票 | 定位 | 仍有效的部分 | 已失真 / 断链 | 候选处置 |
|---|---|---|---|---|
| **#67** | 能力包线父票（规划） | 四要求（最小 agent / 可观测 / 可拆卸 / 加功能不改核心）；「能力包不碰图节点与语义」的硬边界 | 语义核四节点；`minimal` 4 节点；五声明含件清单；AC 依赖 #58 件清单、±1 行、冻结区；前置 #56 已关闭 | **重写**（若保留线）/ **关闭并另立**（若退役） |
| **#78** | Wayfinder 地图 | 决策边界方法；Phase 0 清账；#54/#20 的推迟判断 | destination 绑定「Batch 1 = trace/manifest 样板迁移」；件清单当裁判；固定边界仍引 `roadmap-pawbench §一` 与 ADR 0002「护城河纪律」（已部分退役）；frontier / 依赖边仍引用已关闭的 #74/#70 | **关闭**（旧架构的地图使命结束）；若保留线，另起新图 |
| **#79** | Batch 0 定稿（grilling） | 注册接口签名；settings 分组；`TaskManager.__init__` 不再长 if | 第三件拍板项「件清单行格式」随 #58 删除而失去对象 | **重写**：只保留接口 + 分组，断言面换成工具面快照；或随父票关闭 |
| **#81** | 四批迁移节奏（grilling） | 「一批一 PR / 断言挂哪」这类一般问题仍有参考价值 | 件清单 diff 断言、headless `--check`、live 双场景、「冻结区零碰」机械证明全部失效；前置 #74 已关闭 | **关闭**；若保留线，按新断言面另开一张小票 |
| **#86** | minimal profile 契约（grilling） | #80 的实证仍是一手材料：`_REGISTRY` 非权威面（G2）、`kb_enabled` 只掏数据不摘面（G3）、git/mcp/openapi「关了 vs 没配」不可区分（G4） | 以 4 节点 minimal + 件清单为断言面；前置 #74/#79 | **重写或并入 #67**；#80 事实降为附件；`minimal` 重新定义为「单 `agent` 节点 + 只读工具面」 |
| **#118** | 三轴参照材料 | exposure 五态 / namespace / deferred / `executeTool` 三轴仍有设计价值 | 与 manifest 行的绑定已断；作为 #79 输入，但 #79 本身要重写 | **保留为材料**，删 manifest 表述；不单独实施 |

### 2.1 同批被旧概念污染的票（不属能力包线，但要在 D6 一并处置）

| 票 | 失真点 | 候选处置 |
|---|---|---|
| **#117** | 「`state["plan"]` 唯一消费者是 `risk_scan` + `plan_update`，不进 executor」已由 #119 修复；A/B 对照的 A 形态（planner/executor 两节点）与 `live_e2e` 都已删除 | **关闭**；若仍关心 cache，另开「当前计划/执行切换的 cache 实测」小票 |
| **#113** | 阶段表仍是 `planner / risk_scan / 闸门 / reflect`；验收依赖 manifest、bare/minimal profile；绑定 #67 第一章 | **重写**阶段表为 `inject → agent(plan/build) → tool → policy → compress → finish`，从 #67 解绑后可独立成立 |
| **#54** | 仍含 `ConfirmDialog`、`human_confirm_required`、`COMPLETED / 五档终态`、「三栏问题」旧描述；Out of scope 仍写冻结区与 `_needs_confirm` | **重写票面**：保留四栏 IA / token / a11y / 文件端点，删确认弹窗期，补计划区、模式开关、停下/续说 |
| **#20** | 背景仍以 `backend/headless.py` 为对照；步骤流写 `planner → executor → tool → reflect`；事件含 `task_completed`；确认闸门 / `--auto-approve` 已无对应机制 | **重写**：单 loop + 两模式 + 停下/续说；不与评测台绑定 |
| **#106** | 三项 bug 分析仍有效；验收里的「冻结区零 diff」已失效，背景 #76 已关闭 | **更新票面后保留**，独立排期 |

### 2.2 可直接保留的票

| 票 | 判断 |
|---|---|
| **#153** | 有效 bug，且说明 S5 的网页验收没有真正完成；不受 D6 阻塞 |
| **#148** | 有效且 `ready-for-agent`；沙箱审计是 #147 的前置 |
| **#147** | 有效；「确认闸门」只是历史背景；等 #148 结论 |
| **#145** | 有效；检查点等 #147 的工具面定稿 |
| **#141** | 有效且 `ready-for-agent`；「冻结区解除」是历史理由，不是遗留依赖 |

## 3. spec 台账

`docs/agents/domain.md:36-39` 的规则本身是对的：**冻结只覆盖已交付增量；未开工增量是活设计**。问题出在上游归档口径（#135 AC2 把 `docs/specs/*` 整体列为「不改」），把一份活设计遮住了。

| spec | 类型 | 动作 |
|---|---|---|
| `capability-package-architecture.md` | **未开工活设计** | 重写为单 loop 形态，或加退役抬头 + 另起新 spec；二者必须选一 |
| `p0-b-completion-verification.md` | 已交付，但功能已在 S3 退役 | 归档不改正文；建议加一行退役抬头，指向 ADR 0008 |
| `second-tier-ci-guard-and-smoke.md` | 已部分退役 | 已有 2026-10-14 提示；可补 B（`--check` 冒烟）的半退役结论 |
| `langgraph-1x-migration.md` / `p1-a-*.md` / `p1-b-rollback.md` / `t1-4-tool-result-eviction.md` | 已交付 | 冻结，不动 |

**治理修正（与 D6 结论无关，可先落）**：在 `domain.md` 的 spec 段落补一句「归档豁免只覆盖已交付增量；未开工增量不因 `docs/specs/*` 的整目录豁免而冻结」。这样下一轮文档收尾不会再漏。

## 4. 引用链与小修清单

| 位置 | 现状 | 修法 |
|---|---|---|
| `docs/archive/pivot-plan-build.md:3` | 「S0–S5 已合入」 | 改为 S0–S6 |
| `README.md` | 非空行 52，PR #152 写「约 50 行」 | 不再精确引用 50 |
| `README.md` / `GLOSSARY.md` | `POST /tasks/{id}/messages` 省略 `/api` | 统一按实际前缀（`backend/main.py:63`） |
| #78 正文 | 把 `roadmap-pawbench §一` 与 ADR 0002「护城河纪律」当固定边界 | 随地图关闭或改引用 |
| #54 / #106 | 「冻结区」「`_needs_confirm`」当约束 | 重写票面时删除 |

## 5. D6 待拍板：三个选项

### 选项 A — 保留能力包线，按单 loop 重写

- 注册表继续做，但只贡献三样：工具、EventBus 订阅者、LLM 包装；不新增图节点。
- 删掉 4 节点 minimal、件清单、manifest 迁移批次。
- 断言面改为：**工具 schema 快照 + trace 事件快照**（可 diff、可测试）。
- 票面处置：重写 #67/#79/#86/#118，#78/#81 关闭；spec 重写。
- 代价最大，但完整保留「加功能不改核心」的原始目标。

### 选项 B — 退役能力包线，下一章改做「可观测 / 旅程」

- 关闭 #67/#78/#79/#81/#86；#118 留作归档材料。
- 把 #113 重写为独立下一章：trace 消费面 + 单次请求旅程页（CLI 优先）。
- 能力面遗留的 G2/G3/G4 事实单独开一张小加固票，不再用「注册表」包装。
- 代价最小，最贴合学习/作品定位；放弃「可拆装」这条架构叙事。

### 选项 C — 缩成一张「装配表」票（推荐）

- 只保留真正有收益的部分：把 `TaskManager` 的工具/订阅者装配收敛成一张表，消除 `__init__` 的 if 块与「关了 vs 没配」的不可区分。
- 不做 profile、不做 4 节点 minimal、不做 manifest、不做 settings 分组体系。
- 票面处置：#79 重写为设计票；#67 重写为实施票（或新开一张）；#78/#81/#86 关闭；#118 留作材料；spec 退役并另起一份短 spec。
- 相当于把六张票压成两张，保留目标、去掉已失去理由的机制。

**建议 C**，理由：它是唯一同时满足「保留加功能不改核心」与「不为已删机制重建舞台」的选项；A 的成本主要花在教学性 profile 与五声明上，B 则把已经投入的装配问题完全丢掉。**但这是产品方向判断，应由你拍板。**

### 拍板时可问自己的三个问题

1. 近期真的还有「加一个新能力包」的需求吗（记忆/RAG 升维是唯一已提名的第二个包）？
2. 愿意为一次 `TaskManager` 装配层重构付多少改动量？
3. 下一章更想要「架构可拆装」的叙事，还是「可观测 / 可学习」的叙事？

## 6. 拍板后的动作

无论选哪个：

- [ ] 在本文档顶部写结论与日期，状态改为「已拍板」。
- [ ] 重写/关闭对应票面，并在每张票留一条**处置评论**（不只改标题），说明「为什么不再是原形态」。
- [ ] 修正 `domain.md` 的归档豁免边界。
- [ ] 重写或退役 `capability-package-architecture.md`，确保 AC 不再依赖 manifest / 4 节点 / 冻结区。
- [ ] 更新 #54 / #20 / #113 / #106 的票面，删除已删概念。
- [ ] 落一次 docs 小修（pivot S0–S6、README 行数、`/api` 前缀）。
- [ ] 重跑 `rg 'planner|executor|reflect|risk_scan|human_confirm|COMPLETED|UNVERIFIED|run_manifest|headless|冻结区|件清单'`，开放票与活文档里不再有无主引用。

**完成判据**：一个新 agent 只读 #67 线任意一张票或活 spec，都不会被引向已删节点、已删 manifest、已解除的冻结区。

## 7. 不受 D6 阻塞的并行项

- **#153**：计划编辑未执行（bug）——修 prompt 优先级 + 补 next-round system 捕获测试 + 网页验收。
- **#148**：`code_exec` 沙箱审计——`ready-for-agent`，结论决定 #147 的默认档。
- **#141**：UP 规则族重估——独立 chore。
- **#106**：三项分析可先做，实施等票面更新。
- **#145**：等 #147 的工具面定稿，不抢跑。
