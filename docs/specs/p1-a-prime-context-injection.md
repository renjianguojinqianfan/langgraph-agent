# P1-A′ 上下文注入层实现规范

> 生成于 2026-09-16。决策基线全部拍板完毕，本文件只引用、不重议：
> - 数据面七项：issue #31 resolution comment
> - 行为面五项：issue #32 resolution comment
> - 发现链修正两项：#32「记录修正」comment（追加 home 全局层）+ **2026-09-16 spec 编写会话再修正（发现链收窄为两层、子文件夹懒加载暂缓，见 §二 表 #13–#15）**
> - 权威依据：`docs/capability-first-principles.md` §七（P13）、`AGENTS.md` §2 硬规则、#29 拍板 2/3（TDD 占比 45/35/20，本 spec 风险中心 = 注入语义，测试设计是灵魂）
>
> 关联 issue：#31（closed）/ #32（closed）/ 地图 #27。下游分支：`feat/context-injection`（roadmap §五 分支拓扑已定）。
> 本文件是 P1-A′ 的权威设计源。

## 一、动机与目标

- **第一性原理 P13**（capability §七）：agent 每次冷启动都是失忆的。项目约定、可用能力目录、环境事实只存在于文件系统里——**不注入就等于不存在**。Codex（AGENTS.md 分层加载）/ Claude Code（CLAUDE.md）/ Qoder（规则+记忆+knowledge tree）/ Hermes·dsh（MEMORY.md / 会话预设）四重收敛，证据强度全表最高。
- **现状病灶**（逐文件实证）：
  1. `backend/core/agent/prompts.py` 是两段硬编码常量，无插值、不读外部文件；
  2. `AGENTS.md` 运行时从不被读（全仓引用只有注释文字）；
  3. `.agents/skills/*/SKILL.md` 从未被 KB 索引，能力目录对模型不可见；
  4. 连沙箱根绝对路径都不告诉模型（工具描述只说 "relative to the sandbox root"）。
- **目标**：planner/executor 每次 LLM 调用前，在 system 侧注入「两层 AGENTS.md + skills 清单 + 环境事实」三块内容；任务级缓存、默认开启、零碰保护文件。
- **P1-A 前置依赖**：skills 的「清单先注入 + 正文按需加载」是渐进披露的两半，本 spec 交付「清单注入」这一半；`load_skill` 工具归 P1-A spec（§六 非目标）。

## 二、决策基线（15 项，逐条标来源；终版以此表为准）

| # | 分支 | 拍板 | 来源 |
|---|---|---|---|
| 1 | AGENTS.md 合并语义 | **全部追加·根→子**：近者靠后；无覆盖语义；重复段保留两份 | #31-1 |
| 2 | AGENTS.md 裁剪 | **永不裁**（作者自律；若未来出现病态大手册，走记录修正加显式上限+告警，不做静默截断） | #31-2，#32 审查加护栏 |
| 3 | 环境事实 | **3 项：沙箱根绝对路径 + OS + 日期**；永不裁；模型名/用户画像不入 | #31-3 |
| 4 | skills 卡片形态 | 注入 **name + description**（frontmatter 白名单两键，其余键忽略） | #31-4 |
| 5 | skills 清单预算 | **卡片粒度，默认 4000 字符**（`context_inject_skills_budget` 可调）；超限从清单末尾丢整张卡 | #31-5 |
| 6 | SKILL.md 正文获取 | 新 `load_skill` 工具——**归 P1-A spec，本 spec 非目标** | #31-6 |
| 7 | 改动面 | **钉死 `_build_messages` 单一注入点**，零碰保护文件（resilience.py / registry.py / `_needs_confirm` 重算块） | #31-7 |
| 8 | 注入节点 | planner + executor（唯二 LLM 节点）经 `_build_messages` 自动同享；**subtask 复用同一 `_build_messages` 零代码获得同一份注入**；aux_llm（summarize/risk 语义）不注入 | #32-2 |
| 9 | 注入时机 | **任务级缓存**：runtime 构造时发现+合并一次，任务内每轮复用；**中途修改不生效、下任务生效**（明确语义，非缺陷） | #32-3 |
| 10 | 开关 | **`context_inject_enabled=true` 默认开**（归 `context_inject_*` 子家族，不新开配置组）；conftest 加一行 `CONTEXT_INJECT_ENABLED=false` 隔离；444 基线重跑验证 | #32-4 |
| 11 | compress 共存 | **注入块不计入 8K token 压缩预算**；总上下文 = 注入块 + 压缩后历史；`context.py` 零改动 | #32-5 |
| 12 | 交互态/评测态 | **同一注入语义不分叉**：FastAPI 交互态与 headless 评测态走同一条链；`--no-global-inject` 不预建 | #32 记录修正 |
| 13 | **发现链（终版）** | **仅两层**：`~/.agents/AGENTS.md`（home 全局层，链最前）→ 工作区根 AGENTS.md（= 沙箱根 `settings.artifacts_path`，链末尾）。**盘根与一切中间层一律不读**；缺失层静默跳过零告警；只认 `AGENTS.md` 文件名（不兼容 CLAUDE.md 等别名）。**本行覆盖 #32 拍板 1「沙箱根向上到文件系统根」与其记录修正的「盘根链」表述** | 本会话拍板（2026-09-16） |
| 14 | 子文件夹懒加载 | **本 spec 不实现**：「某文件夹有 AGENTS.md 就在该文件夹内工作时读取」机制整体暂缓（含触发时机两候选「任何路径触碰 / 仅写操作」一并暂缓），未来需要时走记录修正重开 | 本会话拍板（2026-09-16） |
| 15 | skills 发现范围 | 与发现链同构的两层静态发现：`~/.agents/skills/` + 工作区根 `.agents/skills/`；**不做 per-folder skills**；同名 skill 跨层**不去重**（与 #1「全部追加」同语义） | 本会话拍板（2026-09-16） |

**#13 的直接后果**（实现者与评测台须知）：FastAPI 演示态**不再**吸进仓库根 `AGENTS.md`（仓库根既非 home 亦非沙箱根），#32 旧拍板中「吸进仓库手册属可容忍项」一句随之作废；同理仓库根的 `.agents/skills/`（3 个千问 skill）在默认配置下不会被注入——演示/评测需要 skills 时，由任务准备方把 `.agents/skills/` 放进工作区根。

## 三、注入块组装规范（精确定版，§四 全部断言的基准）

### 3.1 组装顺序（pi 分层：稳定前缀在前、可变尾靠后）

```
{PLANNER_SYSTEM 或 EXECUTOR_SYSTEM 原文}     ← 稳定前缀，逐字不动

# Project Instructions (AGENTS.md)             ← 注入块段一：AGENTS.md 合并块
## {home 层 AGENTS.md 的绝对路径}
{home 层文件全文}
## {工作区根 AGENTS.md 的绝对路径}
{工作区根文件全文}

# Available Skills                             ← 注入块段二：skills 清单
- {name}: {description}
- ...

# Environment                                  ← 注入块段三：环境事实（最易变，放最后保前缀缓存）
- Sandbox root: {settings.artifacts_path resolve 后的绝对路径}
- OS: {platform.system()}
- Date: {YYYY-MM-DD}
```

- 段间以单个空行分隔；三个段标题文案为实现常量，测试中作为锚点字符串断言。
- 某一层 AGENTS.md 缺失 → 该层小标题与内容整体不出现（不是空标题）；某段整体无内容 → 段标题不出现。
- **三段全空时注入块为空串，system 与改造前逐字节相同**——这是零回归锚（§四-5.1 断言）。

### 3.2 AGENTS.md 合并块

- 发现范围恰为两处（#13）：`{home_dir}/.agents/AGENTS.md`、`{sandbox_root}/AGENTS.md`。其中 `sandbox_root = settings.artifacts_path`（已 resolve 的绝对路径）；`home_dir` 默认 `Path.home()`，发现函数必须参数化以便测试注入临时目录。
- 顺序：home 层在前、工作区根层在后（根→子、近者靠后，#1）。
- 内容原样拼接，不去重、不裁剪、不改写（#1/#2）。

### 3.3 skills 清单段

- 发现范围（#15）：`{home_dir}/.agents/skills/*/SKILL.md`、`{sandbox_root}/.agents/skills/*/SKILL.md`，按目录名排序后拼接（home 层在前）。
- 卡片渲染：`- {name}: {description}`，仅取 frontmatter 的 `name`/`description` 两键；其余键（如 compatibility）忽略。**缺 name 或缺 description 的卡整张丢弃**（确定性容错，不猜默认值）。
- 预算（#5）：逐卡累计**字符数**（渲染后卡片文本长度），超过 `settings.context_inject_skills_budget`（默认 4000）时，从清单**末尾**逐张丢整卡，直到累计 ≤ 预算；单卡即超预算时该卡也丢（清单可为空）。
- 两处 skills 目录都不存在 → 空清单，零告警。

### 3.4 环境事实段

- 三行，顺序固定：Sandbox root / OS / Date；永不裁（#3）、不受 skills 预算影响。
- `Sandbox root` 与工具描述中的 "sandbox root" 指同一目录（`settings.artifacts_path` resolve 后）。

## 四、测试设计（TDD 灵魂；新文件 `backend/tests/test_context_injection.py`）

> 占比纪律（#29 拍板 3）：本 spec 测试设计 45% / review 实现 35% / 真实模型验收 20%。注入语义的正确性主要由本节的确定性测试承载。
> 公共构造：发现函数为纯函数（`sandbox_root` / `home_dir` 参数化）；测试用 `tmp_path` 造两层目录，**不读真实 `~/.agents`**（monkeypatch home 到 tmp，遵守 #32 记录修正的测试隔离要求）；runtime 侧测试复用 conftest 的 `make_settings`/`make_manager` 与 `MockLLMClient`。

### 4.1 发现链（两层语义 + 反向断言）

| 用例 | 构造 | 断言 |
|---|---|---|
| test_discover_both_missing | 两层都不放文件 | 注入块无 AGENTS.md 段标题；无异常无告警 |
| test_discover_home_only | 仅 home 层放 AGENTS.md | 合并块含 home 内容及其路径小标题，不含工作区根小标题 |
| test_discover_sandbox_only | 仅工作区根放 | 合并块含工作区根内容 |
| test_discover_both_order | 两层各放标记内容 | home 内容出现在工作区根内容**之前**（根→子） |
| test_discover_ignores_alias | 两层放 CLAUDE.md | 不注入（只认 AGENTS.md） |
| test_discover_ignores_drive_root | 在 `tmp_path.anchor` 盘根放 AGENTS.md，两层缺失 | 不注入（锁死 #13） |
| test_discover_ignores_parent_dir | 在 sandbox_root 的父目录放 AGENTS.md | 不注入（锁死 #13） |
| test_discover_ignores_child_dir | 在 sandbox_root 的子文件夹放 AGENTS.md | 不注入（锁死 #14，懒加载暂缓） |

### 4.2 合并语义

| 用例 | 构造 | 断言 |
|---|---|---|
| test_merge_duplicates_kept | 两层文件内容相同 | 两份都出现在合并块（无去重） |
| test_merge_never_truncates | 工作区根放 100KB AGENTS.md | 注入块原样包含全文（逐字相等） |

### 4.3 环境事实

| 用例 | 构造 | 断言 |
|---|---|---|
| test_env_facts_sandbox_root | 常规 | 段内含 `settings.artifacts_path` resolve 后的绝对路径字符串 |
| test_env_facts_os | 常规 | 段内含 `platform.system()` 返回值 |
| test_env_facts_date | 常规 | 段内含当天 ISO 日期（`date.today().isoformat()`） |
| test_env_facts_immune_to_budget | skills 预算设为极小值 | 环境事实段完整出现（不受 skills 预算影响） |

### 4.4 skills 清单与预算边界

| 用例 | 构造 | 断言 |
|---|---|---|
| test_skills_both_layers_collected | home 与工作区根各放 1 skill | 两卡都出现，home 层在前 |
| test_skills_card_whitelist | frontmatter 含 name/description/compatibility 等额外键 | 卡片只有 `- name: description`，其余键不出现在注入块 |
| test_skills_missing_name_dropped | 一卡缺 name | 该卡被丢，其余卡保留 |
| test_skills_missing_desc_dropped | 一卡缺 description | 该卡被丢 |
| test_skills_no_frontmatter_dropped | SKILL.md 无 frontmatter | 该卡被丢 |
| test_skills_budget_exact_fit | 卡片合计恰好 4000 字符 | 全保留 |
| test_skills_budget_overflow_drops_tail | 合计 4001 字符 | 末尾整卡被丢，剩余 ≤ 4000 |
| test_skills_budget_multi_drop | 5 卡、预算只容前 2 卡 | 从末尾逐张丢，恰好剩前 2 卡 |
| test_skills_single_card_over_budget | 唯一一卡即超预算 | 清单为空（段标题不出现） |
| test_skills_dir_missing | 两层都无 skills 目录 | 空清单，零告警 |
| test_skills_duplicate_names_kept | 两层各放同名 skill | 两卡都在（不去重） |

### 4.5 开关与隔离

| 用例 | 构造 | 断言 |
|---|---|---|
| test_disabled_byte_identical | `context_inject_enabled=False` + 两层全放内容 | `_build_messages` 返回的 system 与改造前逐字节相同（零回归锚） |
| test_settings_defaults | `monkeypatch.delenv("CONTEXT_INJECT_ENABLED")` + `delenv("CONTEXT_INJECT_SKILLS_BUDGET")` 后构造 `Settings()` | `context_inject_enabled is True`、`context_inject_skills_budget == 4000`（conftest 隔离行已设进程级环境变量，默认值断言必须先清掉再构造） |
| 基线验证（非单测） | conftest 隔离行就位后全量重跑 | 444 存量用例全绿（验收锚点 ①） |

### 4.6 任务级缓存

| 用例 | 构造 | 断言 |
|---|---|---|
| test_cache_frozen_within_task | 构造 runtime 后改写两层 AGENTS.md，再调 `_build_messages` | 注入块内容不变（发现+合并只发生一次） |
| test_cache_new_runtime_rereads | 新建 runtime | 读到修改后的内容（下任务生效） |
| test_cache_planner_executor_share | 同一 runtime 连续两次 `_build_messages`（planner/executor 各一次） | system 注入块完全一致 |

### 4.7 compress 共存

| 用例 | 构造 | 断言 |
|---|---|---|
| test_compress_keeps_inject_block | 注入开启 + 构造超 8K 预算的 messages 触发压缩 | system 注入块逐字保留；messages 被压缩；`state["compressed"] is True` |
| test_compress_budget_excludes_inject | 同一段 messages，分别在有/无注入下构建 | 写回的 `state["context_tokens"]` 两场景相等（注入块不计入压缩预算口径） |

### 4.8 注入节点

| 用例 | 构造 | 断言 |
|---|---|---|
| test_subtask_runtime_shares | 模拟 subagent.py 构造路径建 subtask runtime（`confirm_enabled=False`） | 其 `_build_messages` 的 system 含同一注入块 |
| test_aux_llm_not_injected | summarize 策略 + aux mock，触发压缩 | aux 收到的载荷中不含注入段标题（aux 只碰 messages，天然不注入） |

### 4.9 配置解析

| 用例 | 构造 | 断言 |
|---|---|---|
| test_env_override_enabled | 环境变量 `CONTEXT_INJECT_ENABLED=false` | Settings 解析为 False |
| test_env_override_budget | 环境变量 `CONTEXT_INJECT_SKILLS_BUDGET=1000` | Settings 解析为 1000 |

## 五、实现方案

### 5.1 新增 `backend/core/agent/inject.py`（纯函数组，全部确定性与 I/O 封装在此）

```python
def build_inject_block(settings, *, home_dir: Path | None = None) -> str:
    """任务级注入块。home_dir 默认 Path.home()；参数化供测试注入临时目录。"""

# 内部 helper（私有）：
#   _render_agents_md(home_dir, sandbox_root) -> str   # 两层发现 + 根→子拼接
#   _render_skills(home_dir, sandbox_root, budget) -> str  # 两层收集 + 白名单 + 卡片预算
#   _render_env_facts(sandbox_root) -> str             # 3 项环境事实
```

- frontmatter 解析用 `yaml.safe_load`（PyYAML 已在 `requirements.txt` 钉版，仓库既有先例 `backend/core/tools/openapi_tool.py`；不引入新依赖、不新写手写解析器）。只取 `name`/`description` 两键；frontmatter 缺失或 YAML 解析失败按「缺键 → 整卡丢弃」处理（与 §3.3 确定性容错同语义，SKILL.md 是外部输入、属校验边界）。
- 所有文件读取 `try/except OSError` 静默跳过（缺失层零告警，#13）。

### 5.2 改 `backend/core/agent/nodes.py`（唯一注入点，两处小改）

1. `AgentRuntime.__init__` 末尾追加：

```python
settings = getattr(getattr(self, "tm", None), "settings", None) or get_settings()
self._inject_block = (
    inject.build_inject_block(settings)
    if getattr(settings, "context_inject_enabled", True)
    else ""
)
```

2. `_build_messages` 返回处把 `system` 拼上注入块：`system + self._inject_block`（`self._inject_block` 非空时其自身以 `\n\n` 开头，由 `build_inject_block` 保证）。

- **不碰** `_needs_confirm` 重算块、`_stopped` helper、`tool_node` 等一切既有逻辑；`getattr(..., True)` 兜底 SimpleNamespace 假 settings（与 `verify_enabled` 同款先例）。

### 5.3 改 `backend/config.py`（2 个新字段，归 `context_inject_*` 子家族，紧跟 Context compression 组之后）

```python
# ── Context injection (P1-A′) ──
context_inject_enabled: bool = True  # master switch: AGENTS.md + skills + env facts into system
context_inject_skills_budget: int = 4000  # chars; drop whole cards from the tail when over
```

> 组头注释仅为可读性分段。#32 拍板 4「不新开配置组」的语义 = 不另立独立配置家族（如 `injection_*`），`context_inject_` 是 context 家族的**子前缀**；README 的前缀计数按子前缀计，故 16 → 17。

### 5.4 改 `backend/tests/conftest.py`（隔离块追加一行，其余一字不动）

```python
os.environ.setdefault("CONTEXT_INJECT_ENABLED", "false")  # P1-A′: isolate the injection chain
```

### 5.5 零碰清单（显式声明，CI guard 已机械化拦截前两个）

`backend/core/tools/resilience.py`、`backend/core/tools/registry.py`、`backend/core/agent/context.py`、`backend/core/agent/graph.py`、`backend/core/agent/subagent.py`、`backend/services/task_manager.py`、`backend/core/agent/prompts.py` —— 一律零改动（subtask/resume 经共享 `AgentRuntime.__init__` 自动获得注入，#8）。

### 5.6 文档同步（与代码同 PR）

- `.env.example`：新增 `context_inject_*` 段（两键带注释，风格对齐现有各组）。
- `README.md`：能力清单加一条「上下文注入（P1-A′）」；配置计数「16 组前缀」改为「17 组前缀」（`context_inject` 为 context 家族子前缀，见 §5.3 注）；链接指回 `.env.example` 新段。
- `AGENTS.md`：§1 快照中「40 个 test_*.py / 444 用例」与「16 组配置前缀」两处计数随实现同步（反熵：文档与事实同步；p0-b spec 有同款「顺手更新测试数快照」先例）。

## 六、非目标（显式排除）

1. **`load_skill` 工具**（SKILL.md 正文按需获取）——归 P1-A spec，数据契约见 #31 表 #4/#6。
2. **「宿主自动注入」备选升级方向**（规则正文自动进 system）——归 P1-A。
3. **AGENTS.md 任何形式的裁剪/截断**——永不裁（#2）；病态大手册未来走记录修正加显式上限+告警。
4. **热加载 / 任务中途重读**——任务级缓存（#9）。
5. **`--no-global-inject` 开关**——不预建；评测若需纯净环境走记录修正。
6. **CLAUDE.md 等别名兼容**——只认 AGENTS.md（#13）。
7. **子文件夹 AGENTS.md 懒加载**——本会话拍板暂缓（#14），含「任何路径触碰 / 仅写操作」触发形态一并暂缓。
8. **盘根 / 中间层发现**——#13 终版已排除。

## 七、实现路径与验收锚点

- **分支**：`feat/context-injection` + PR + CI 全绿；零碰保护文件，无需 `[OVERRIDE]`。
- **验收锚点**：
  1. `.venv311\Scripts\python.exe -m pytest backend/tests/ -q` —— 444 存量 + 本节新增全绿（重跑基线证零回归）；
  2. `.venv311\Scripts\python.exe -m ruff check backend scripts` 与 `.venv311\Scripts\python.exe -m mypy` —— 均 0 错（基线全净，不许 ignore/override）；
  3. `LLM_API_KEY="$DASHSCOPE_API_KEY" .\.venv311\Scripts\python.exe scripts/live_e2e.py` —— 真实模型双场景（冒烟 + 断点续跑）PASS，Key 只走环境变量；
  4. `.\.venv311\Scripts\python.exe scripts/live_e2e.py --check` 与 `.\.venv311\Scripts\python.exe -m backend.headless --check` 离线冒烟不受影响（解释器一律 `.venv311`，见 AGENTS.md §2）。
