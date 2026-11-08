---
status: accepted
---

# 插件 + 预设：能力包与全局组合（真关）

> 2026-11-08 由 spec #287 Phase 4 切片 P4-1（#67）落地；拍板点为 spec #287 的
> D-53 … D-57。本文只记决策与边界，不复制实施细节；现役装配事实以
> `docs/architecture.md` `4.5 / `4.6 与 `backend/core/capability_packages.py` 为准。
> 上一章最简 agent 的退役清单见 [ADR 0009](0009-minimal-agent-chapter.md)；本 ADR
> **不复活**其中退役的语义核四节点、件清单与冻结区约束，只引入「能力包 / 预设」两个词。

## 背景

#67 是「最简 agent 一章」之后的下一章占位票，材料指向
`docs/capability-registry-rescope.md` G-6 与 §0.2：参照 DSH 的插件可启停能力，
内置几套固定预设；「关」必须是真关——不留工具、不往上下文里加东西；只装本仓
自己的代码，插件不当沙箱。旧 spec `capability-package-architecture.md` 里的
件清单 / 语义核内容已随 ADR 0008、0009 退役，本片按新 spec 重定形态。

P4-1 只交付静态声明 + 装配 + 三个预设的真关；`data/plugins.json`、设置页、
`qianfan --preset`、API 属 P4-2，不在本片。

## 决策

1. **全局组合、服务级生效（D-53）**：一个后台进程只有一套能力包组合，启动时解析、
   改完重启生效（设置页在 P4-2 明示）。**不做每任务预设**：G-2 的学习场景是
   同一道题在不同全局组合下对比，任务级会把「任务 × 组合 × 持久化」的状态矩阵
   与续跑语义乘上去；重开条件见文末。
2. **承载面（D-54）**：`data/plugins.json` 形态 =
   `{preset: "minimal|research|full", overrides: {<package-id>: bool}}`；文件缺失 =
   默认 `full` + 无 overrides；`overrides` 叠在 preset 之上（true 强制开 / false
   强制关）。设置页「能力」区与 `GET/PUT /api/plugins` 读写该文件；改文件后
   **重启后台生效**（不做热重载）。`qianfan --preset <id>`（web）/
   `qianfan repl --preset <id>`（终端）一次性覆盖、不写文件。默认 `preset=full` =
   #67 之前的全部行为。
3. **能力包与三预设（D-55）**：能力包是本仓代码的**静态声明**（不是第三方插件），
   单项 = 工具名集合 + 注入片段 + 是否连 MCP + 是否影响子代理面：
   - `files` = read / write / edit / ls / glob / grep；
   - `bash` = bash；
   - `skills` = load_skill + skills 清单注入片段；
   - `web-search` = web_search；
   - `subagent` = spawn_subagent（子代理执行器随之启停）；
   - `mcp` = 动态 MCP 工具（连接由该包开启；工具名由服务器决定）。

   预设：`minimal` = files + bash；`research` = files + bash + skills +
   web-search + subagent + mcp；`full` = 全部（**默认，与 #67 前行为等价**）。
4. **真关的定义（D-55）**：包关闭后——不实例化对应工具、不进工具 schema、不进
   dispatch 面、不往上下文 / 注入里加片段、不连接 MCP。**不承诺「模块不被
   import」**：import 是 Python 的加载形态，不是能力面；真关约束的是实例化与接线。
   `explore` / `execute` 两个子代理 tier 常量**零改动**；子代理实际面 =
   固定档位 ∩ 已启用包（父任务已挂载工具再与 tier 求交）。未知 preset / 非法
   包 id 一律**启动即错**（可读错误指向 `.env.example` 与 P4-2 设置页）。
5. **装配面（D-56）**：`TaskManager` 从「启用包清单」装配工具面与注入片段；
   `nodes.py` 对 `spawn_subagent` 的特殊照顾改为由面派生（禁用 `subagent` 时工具
   自然缺席、执行器不构造）；MCP 只在 `mcp` 包启用且 `mcp_enabled` 时连接
   （沿用现有配置、懒加载与告警语义）；`skills` 注入片段按包归属追加，禁用即不加，
   AGENTS.md / 环境事实两个注入块不受影响。`subagent_enabled` 保留为附加门
   （既有配置兼容），不再是唯一开关。
6. **边界（#67 票面，写死）**：不做第三方插件、不做沙箱、不扩图节点、不做危险
   规则钩子插件、不做 EventBus 插件、不做插件市场。插件不是安全边界（同 README
   「这不是安全边界」：bash / 文件工具改真实文件，撤销靠 git）。
7. **文档与术语（D-57）**：README「能力」段、architecture `4.5/`4.6、GLOSSARY
   「能力包 / 预设」、AGENTS.md「工具挂载与失败回传」行同步。
8. **P4-2 优先级与生效语义（2026-11-08 已交付）**：解析优先级（高到低）=
   **CLI `--preset`（仅本次运行，经内部环境变量传给 web 子进程，不写文件）>
   `data/plugins.json`（文件存在即权威，env 不再生效）> `PLUGINS_PRESET` env
   （仅文件缺失时作为 P4-1 兼容回退）> 默认 `full`**。存储用 atomic 写
   （`*.tmp` + `os.replace`；与 tasks.json / models.json 同款），校验失败不落盘。
   §GET /api/plugins§ 返回目录 + saved（文件）+ active（本进程启动时装配的面）+
   `restart_required`（saved 的 preset/overrides 与 active 不同）；§PUT§ 校验后写
   文件并返回同形状，未知 preset / 包 id → 4xx 且不写。非法文件 / preset 在
   `create_app` / `build_runtime` / CLI / REPL / `start.py` 启动期报可读错误
   （含 `data/plugins.json` 路径与修法），不静默回退 `full`。**CLI `--preset` 不豁免坏
   文件**：文件非法时即使带一次性覆盖也启动即错（文件是权威配置，绕过后 API 的 saved /
   restart_required 会与真相不一致）。写文件失败（IO / 权限 / 磁盘）与校验失败分开：
   校验错误 4xx 不落盘；写入失败 500（旧文件保持、unique 临时文件清理），并发 PUT 不共用
   固定临时名。`GET /api/plugins` 的 `active.tools` 是实际装配面（含 legacy 开关与已连接
   的 MCP 动态工具），与 `TaskManager` 共用同一个 pure 派生 helper。

## 与既有 ADR 的关系

- **不复活 ADR 0009 的退役项**：语义核四节点、`mode="minimal"`、五声明件清单、
  「件清单 ±1 行」验收与冻结区约束均保持退役；本 ADR 只重新使用「包 / 预设」词。
- **继承 ADR 0008 的单 loop**：能力包只改「挂什么工具、注什么片段」，不改图拓扑、
  不改危险动作钩子、不改两模式语义。
- **继承 ADR 0010 / 0011 的配置面**：模型 / Provider 面不变；能力组合是另一条
  全局配置，不混进 `data/models.json`。

## 被否的选项

| 题 | 选定 | 被否 | 否掉的理由 |
|---|---|---|---|
| 组合粒度 | 全局、服务级、重启生效 | 每任务预设 | 状态矩阵 × 持久化 / 续跑复杂度；G-2 对比场景用「换全局组合 + 重启」足够（重开条件见下） |
| 生效方式 | 启动期解析 | 热重载 / 运行中切换 | 工具面与注入块是任务级快照；中途换会让同一任务的 schema / 上下文不一致 |
| 插件来源 | 只本仓静态代码 | 第三方插件包 / 市场 | 无信任 / 沙箱 / 签名故事；#67 明确插件不当沙箱 |
| 关闭语义 | 不实例化 / 不接线 / 不注入 | 只从 schema 隐藏、实例仍在 | 「隐藏」会让上下文、MCP、子代理面留后门；D-55 要真关 |
| 首片承载 | Settings 字段（P4-1）→ 文件 + UI（P4-2） | P4-1 直接上 `data/plugins.json` + API | 半套持久化会先冻结一个未经 UI 校验的 schema；两片拆开可独立回滚 |
| 覆盖方式 | 预设 ± overrides（P4-2） | 只支持命名预设 | 用户要微调（如 research 去掉 mcp）时不该逼出一个新预设 |
| MCP 关闭实现 | 装配期不连接、不懒加载 | 连接后从面移除 | 连接即 spawn 子进程 / 网络副作用，真关必须发生在连接前 |

## 三笔账（明说的代价）

1. **组合矩阵进测试面**：三预设 × 工具 / schema / 注入 / MCP / 子代理各一条机械
   断言；默认 `full` 还必须与旧行为逐名等价，防解析漏包。
2. **两个「开关」并存**：`subagent_enabled` / `mcp_enabled` 仍是附加门，
   `subagent` / `mcp` 包是能力面门；两者都开才挂载。文档写清「默认 full =
   现行为」，避免把旧开关当已退役。
3. **重启生效的 UX 债**：P4-1 只能改 `.env` / 环境变量并重启；P4-2 的设置页要
   明确「保存后需重启后台」并做文件校验与回退。

## 重开条件

- **每任务预设**：只有 G-2 学习场景升级为「同一任务内对比多组合」且状态矩阵 /
  持久化方案被单独设计（含 checkpoint 与续说语义）时才重开；否则保持全局。
- **第三方插件 / 市场**：需要信任模型、签名、沙箱或权限边界，并与「插件不是
  安全边界」的定位一起重估；不在本仓近期计划。
- **热重载能力组合**：需要工具面 / 注入块从任务级快照改成可热切换对象，且明确
  半途切换对 schema / 上下文的语义，另开票。
- **更多预设 / 自定义 preset**：P4-2 的承载面是**三预设 + `overrides`**（`custom`
  未实现，本 ADR 未将其定为长期方向）。要新增命名预设：扩展 `PRESETS` 并同步本 ADR、
  `capability_packages.py` 与文档。要支持任意 `custom` 组合：另开票先定 schema
  （`preset` 字段是否接受 `"custom"`、与 overrides 的关系）与设置页交互，再实施。
- **未知 preset 降级为警告**：不重开；启动即错是 D-55 的机械保障。
