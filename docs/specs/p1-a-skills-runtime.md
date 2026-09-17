# P1-A skills 运行时实现规范

> 生成于 2026-09-17。决策基线全部拍板完毕，本文件只引用、不重议：
> - 数据契约三项：issue #31 resolution 表 #4（卡片形态）/ #6（正文获取 = 新 `load_skill` 工具）/ #7（注册路径）
> - 执行序与验收纪律：issue #37（P1-A 排在 P1-B 之后、评测台之前；同日记录修正后的序）、ADR-0002（验收走离线全量 + live 双场景，不走评测分）
> - 上游 spec：`docs/specs/p1-a-prime-context-injection.md`（渐进披露的「清单注入」半边；其 §六 非目标 1 明示 `load_skill` 归本 spec）
> - 本会话补充拍板两项（2026-09-17）：① 同名跨层**两份都返回**；② live 验收**扩展 `scripts/live_skill_test.py`**
>
> 关联 issue：#31（closed）/ #37（closed），无需新开——实现 PR 直接引用本文件。
> 本文件是 P1-A 的权威设计源。

## 一、动机与目标

- **第一性原理**：capability §四 T2.2「skills：仅 KB 检索，无运行时加载 = 缺口」。P1-A′ 已交付渐进披露的**前半**（清单常驻：只含 `name` + `description` 两键的卡片进 system）；本 spec 交付**后半**——模型按需取回 SKILL.md **正文**进对话，用多少取多少。
- **收敛证据**：pi 源码级同构（skills 清单注入 + 模型主动读 SKILL.md 正文、正文作为 tool result 进对话而非 system——正是「不破坏 prompt cache」的实现）；#31-6 已据此拍板工具形态。
- **为何不是 KB 检索**：`kb_query` 是碎片检索（chunk 命中），与「整档精读」语义错位；且现状 SKILL.md 从未被索引（capability §七 实证）。两者边界不交叉（#31-6）。
- **为何不是「宿主自动注入」**：#31-6 将「规则正文自动进 system」记为**备选升级方向**，不在本 spec 范围；本 spec 只做模型主动加载。
- **目标**：新增 `load_skill(name)` 工具——按名取回两层 skills 目录中的 SKILL.md 全文，返回结果经既有工具管线进对话；零碰保护文件、零新增配置、注入块形态冻结。

## 二、决策基线（16 项，终版以此表为准）

| # | 分支 | 拍板 | 来源 |
|---|---|---|---|
| 1 | 工具形态 | 新 `load_skill` 单工具，参数仅 `name`（必填 string） | #31-6 |
| 2 | 正文形态 | 「按名取整档」= SKILL.md **全文原样**（含 frontmatter）作为 tool result 进对话，**不进 system prompt** | #31-6 |
| 3 | 名字来源 | 以 frontmatter `name` 为准（= 清单卡片显示名，strip 后精确匹配、大小写敏感）；**不认目录名**、不做别名/模糊匹配 | 本 spec 定版 |
| 4 | 可加载名单 | = 两层全部「frontmatter 可解析且 `name` 为非空字符串」的 SKILL.md；**不受清单 4000 字符预算影响**（预算是展示预算，不是可加载性边界）；`description` 缺失者不进清单但**仍可按名加载** | 本 spec 定版 |
| 5 | 同名仲裁 | **两份都返回**：按发现序（home → workspace）逐份返回并带 `layer` 标注；不引入任何「覆盖 / 先者胜」仲裁（与 #31-1「全部追加、不去重」同语义） | 本会话拍板（用户确认，2026-09-17） |
| 6 | 未命中行为 | `success=False` 且**诊断写入 `data`**（`nodes.py` 只把 `data` 序列化进对话，`error` 字段模型看不到——见 §三 3.4）；可用名单列前 20 个，超出加 `(+N more)`；两层都无 skills 时给「无可用」文案 | 本 spec 定版（约束来自 `nodes.py:527-529` 实证） |
| 7 | 注册路径 | 新文件 `backend/core/tools/skill_tool.py` + `@register` + `backend/core/tools/__init__.py` import/`__all__` 各一行；**零碰** `registry.py` / `resilience.py` | #31-7（已核实） |
| 8 | 发现复用 | P1-A′ 的两层 skills 发现抽为 `inject.discover_skills()` 共享（单一权威，禁止第二份发现逻辑）；**注入块渲染输出逐字节不变**（既有 `test_context_injection.py` 全量即回归锚） | 本 spec 定版 |
| 9 | 韧性/确认策略 | `requires_confirm=False`、`retryable=False`、`max_retries=0`、`circuit_breaker=False`（本地确定性只读，与 `read`/`glob`/`grep` 同款先例） | 本 spec 定版（对齐 P0-A） |
| 10 | 配置 | **零新增 Settings**；工具常驻注册；与 `context_inject_enabled` 正交（注入关 → 清单不出现，但显式点名仍可加载，因为工具读盘不读注入块） | 本 spec 定版 |
| 11 | 沙箱边界 | **名字不参与任何路径拼接**：文件从两层 skills 目录**枚举**得到，名字只做等值比较 → 无路径注入面；`../`、绝对路径、含分隔符的名字天然不命中（有测试锁死） | 本 spec 定版 |
| 12 | artifact 登记 | 结果 `data` **不含顶层 `path` 键** → 不触发 `tool_node` 的 artifact 登记（`nodes.py:531-535`），skill 文件不落产物列表、不进 KB | 本 spec 定版（实证同上） |
| 13 | 子任务可用性 | 子图工具表 = 父表 − `spawn_subagent`（既有 `subagent.py:_subtask_tools`）→ `load_skill` 自动可用，**零代码** | 既有实证 |
| 14 | T1.4 交互 | 整档如实进对话；保护带外超 4000 字符的 skill 正文会被逐出为占位（含 trace 回读指针）——既有语义，不需任何新处理 | 既有 T1.4 |
| 15 | 提示词 | `prompts.py` **零改动**；使用引导只写在工具 description（「任务匹配 Available Skills 清单中的技能时调用」） | 本 spec 定版 |
| 16 | live 验收 | 扩展 `scripts/live_skill_test.py` 新增 skills-runtime 腿：真实模型须实际调用 `load_skill`、tool_result 的 `documents[0].content` 与磁盘全文逐字符相等、final answer 含正文原句引用（断言细节见 §5.5） | 本会话拍板（用户确认，2026-09-17） |

## 三、接口规范（精确定版，§四 全部断言的基准）

### 3.1 工具契约

```python
name = "load_skill"
description = (
    "Load the full text of a skill's SKILL.md by name. Use it when the task "
    "matches a skill listed in the Available Skills section, before following "
    "that skill's instructions."
)
args_schema = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Skill name exactly as listed in the Available Skills section.",
        }
    },
    "required": ["name"],
}
```

- 名字归一：`str(kwargs.get("name") or "").strip()`；为空 → 参数错误分支（§三 3.4）。
- `__init__(self, settings=None, home_dir: Path | None = None)`：`home_dir` 默认 `None` → `Path.home()`，仅测试注入临时目录用（与 `inject.build_inject_block` 同款先例）；`build_tools(settings)` 的 `cls(settings)` 调用不受影响。

### 3.2 共享发现（改 `backend/core/agent/inject.py`，渲染输出逐字节不变）

```python
@dataclass(frozen=True)
class SkillEntry:
    layer: str        # "home" | "workspace"
    name: str         # frontmatter name，strip 后
    description: str  # frontmatter description；缺失/非字符串 -> ""
    path: Path        # SKILL.md 绝对路径

def discover_skills(home_dir: Path, sandbox_root: Path) -> List[SkillEntry]:
    """两层枚举（home -> workspace），层内按目录名排序；
    仅收 frontmatter 可解析且 name 为非空字符串的 SKILL.md。"""
```

- 层与顺序与 P1-A′ 完全一致：`{home}/.agents/skills/*/SKILL.md` → `{sandbox_root}/.agents/skills/*/SKILL.md`。
- 读取容错沿用 `_read_text`（`OSError` / `UnicodeDecodeError` 视为不存在）；坏 YAML / 缺 name 整项丢弃。
- `_render_skills` 改为消费 `discover_skills`（过滤 `description` 非空 + 既有 `_fit_budget` 预算），**section 标题、卡片文本、顺序、预算行为全部不变**。
- 工具与清单共用同一份名单：**清单中的每个名字必然可加载**（有测试锁死），反之不成立（缺 description / 被预算丢尾的仍可加载，#4）。

### 3.3 命中输出形态

```python
ToolResult(success=True, data={
    "name": <请求名>,
    "documents": [
        {"layer": "home" | "workspace", "content": "<SKILL.md 全文>"},
        ...  # 同名多份按发现序全部返回；不含 path 键（#12）
    ],
})
```

- 命中判据：`[e for e in discover_skills(...) if e.name == 请求名]` 非空**且至少一份可读取**；读取失败（OSError / UnicodeDecodeError）的单份跳过——全部失败视为未命中。
- `content` 为文件逐字节解码后的全文（含 frontmatter 块，原样），不做任何裁剪/改写（#2）。

### 3.4 未命中与参数错误（诊断进 `data`，#6）

参数错误（`name` 缺失或 strip 后为空）：

```python
ToolResult(success=False, data={"error": "`name` is required."}, error="`name` is required.")
```

- 未命中（名单非空）：`success=False`，`data["error"]` 与 `error` 同为 `Unknown skill: '<name>'. Available skills: a, b, c (+N more)` 形态（单引号包裹请求名）；可用名 = 全部可加载名，按发现序，前 20 个，超出追加 ` (+N more)`。
- 名单为空：`success=False`，`data["error"]` 与 `error` 同为 `Unknown skill: '<name>'. No skills found under <home>/skills or <workspace>/skills.` 形态。
- 依据：`tool_node` 把 `ToolResult.data` 用 `json.dumps(ensure_ascii=False, default=str)` 写进 `role="tool"` 消息（`nodes.py:527-529`），`error` 字段只进事件与步骤记录、**不进对话**——所以诊断必须放 `data`，模型才能自我纠偏。不改 `nodes.py`。

### 3.5 安全边界

- 名字**永不参与路径构造**（文件来自目录枚举，名字只做等值比较）→ `../x`、`/etc/passwd`、`C:\...`、`a/b` 一律「未命中」，不会读到 skills 根以外的任何文件（§四 B 组测试锁死）。
- 只读 `{layer}/.agents/skills/<dir>/SKILL.md` 这一个文件名，不做 glob、不列目录给模型。
- 已知暴露面 = 与 P1-A′ 注入层**同源同权**（同样是枚举两层 skills 目录、读取 SKILL.md；不额外做 symlink 逃逸加固，也不收窄）——不扩大攻击面，本 spec 不引入新的加固主张。

### 3.6 与相邻机制的交互（零代码，仅确认）

- 子任务：`subagent.py:_subtask_tools` 只剔除 `spawn_subagent` → 子图自动获得 `load_skill`（#13）。
- T1.4：大段正文的逐出与回读指针由既有 eviction 承担（#14）。
- 注入开关：`context_inject_enabled=false` 时清单不注入、工具仍注册可用（#10），与 P1-A′ 的「零回归锚」不冲突。

## 四、测试设计（新文件 `backend/tests/test_skill_tool.py`）

> 仓库 TDD 传统：先写用例（红）→ 实现（绿）。构造沿用 `test_context_injection.py` 的既有模式：`make_settings(tmp_path)` + `tmp_home` 两层目录 + `_skill()` 写 frontmatter；工具直接实例化（`LoadSkillTool(settings, home_dir=tmp_home)`）。

### A 发现与命中

| 用例 | 构造 | 断言 |
|---|---|---|
| test_load_from_home_layer | 仅 home 层放 skill | 命中，`documents[0].layer == "home"` |
| test_load_from_workspace_layer | 仅工作区根放 | 命中，`layer == "workspace"` |
| test_load_duplicate_returns_both_home_first | 两层同名各一份 | `documents` 恰两份，顺序 home → workspace，内容各自对应（#5） |
| test_load_name_from_frontmatter_not_dirname | 目录名 ≠ frontmatter name | 按 frontmatter 名命中；按目录名调用「未命中」（#3） |
| test_load_ignores_manifest_budget | 预算调至只容第一张卡 | 被丢尾卡的 skill 仍可加载（#4） |
| test_load_without_description_loadable_not_listed | frontmatter 缺 description | 清单无此卡、`load_skill` 可命中（#4） |
| test_load_invalid_frontmatter_not_loadable | 坏 YAML / 无 frontmatter / name 为空 | 均不命中 |
| test_load_content_verbatim | 常规 | `content` 逐字符等于文件全文（含 frontmatter 块） |

### B 输入与安全

| 用例 | 构造 | 断言 |
|---|---|---|
| test_load_empty_name_param_error | `name=""` / 缺失 | `success is False`、`data["error"]` 含 "required" |
| test_load_numeric_name_not_found | `name=3` | 不抛异常，未命中分支 |
| test_load_rejects_traversal | `name="../secret"`、`"..\\secret"` | 未命中；目标文件（两层 skills 根之外）从未被读取 |
| test_load_rejects_absolute_path | `"/etc/passwd"`、`"C:\\Windows\\win.ini"` | 未命中 |
| test_load_rejects_separator_name | `"a/b"`、`"a\\b"` | 未命中 |
| test_load_never_scans_outside_roots | 沙箱根内 `.agents` 之外放同名 SKILL.md | 未命中（只认两层 skills 根，#11） |

### C 未命中形态

| 用例 | 构造 | 断言 |
|---|---|---|
| test_missing_lists_available_names | 两个 skill、请求不存在的名字 | `data["error"]` 含两个可用名与请求名 |
| test_available_capped_at_20 | 造 25 个 skill | 名单恰 20 个 + `(+5 more)` |
| test_empty_catalogue_message | 两层都无 skills 目录 | `data["error"]` 含 "No skills found" |
| test_failure_contract_data_carries_error | 未命中 | `success is False` 且 `data` 为 dict 含 `error`（#6 契约） |

### D 契约与注册

| 用例 | 构造 | 断言 |
|---|---|---|
| test_registered_in_build_tools | `build_tools(settings)` | `"load_skill" in names`（既有 `⊆` 风格，不做全集相等断言） |
| test_policy_flags | 实例 | `requires_confirm/retryable/circuit_breaker is False`、`max_retries == 0`（#9） |
| test_openai_schema_required_name | `to_openai_schema()` | `function.name == "load_skill"`、`required == ["name"]` |
| test_result_has_no_top_level_path | 命中 | `"path" not in data`（#12，不触发 artifact 登记） |

### E runtime 集成（两例 + 基线回归，走既有管线）

| 用例 | 构造 | 断言 |
|---|---|---|
| test_tool_node_feeds_skill_content_to_conversation | `make_manager` + scripted `MockLLMClient(tool_calls=[load_skill])` | 对话出现 `role="tool"` 消息且内容含 SKILL.md 特征串；无 artifact 登记；任务 COMPLETED |
| test_catalogue_cards_all_loadable | 两层混合构造（含缺 description 卡） | 注入块里出现的每个卡片名都能 `load_skill` 成功（清单 ⊆ 可加载，锁死 #8 的共用名单） |
| 基线（非新增用例） | `test_context_injection.py` 全套零修改运行 | 注入块逐字节不变（#8 的回归锚） |

## 五、实现方案

### 5.1 新增 `backend/core/tools/skill_tool.py`（唯一新生产文件）

`@register class LoadSkillTool(BaseTool)`：§三 契约 + §3.2 共享发现 + §3.3/3.4 输出形态；读取用严格 UTF-8 + 与发现层同款容错（`OSError` / `UnicodeDecodeError` → 该份视为不存在）。约 90 行，零外部依赖。

### 5.2 改 `backend/core/agent/inject.py`（行为保持的抽取，非新功能）

新增 `SkillEntry` + `discover_skills()`（§3.2）；`_render_skills` 改为消费它；卡片渲染等价改写（过滤 description + 预算不变）。**渲染输出逐字节不变**，`test_context_injection.py` 零修改即回归锚。

### 5.3 改 `backend/core/tools/__init__.py`（2 行）

`from .skill_tool import LoadSkillTool` + `__all__` 加一项（import 序由 ruff isort 门禁裁定）。

### 5.4 新增 `backend/tests/test_skill_tool.py`（§四 全部用例）

### 5.5 改 `scripts/live_skill_test.py`（新增第二腿 skills-runtime，#16）

1. 建临时工作区 `tmp_workspace`，把仓库 `.agents/skills/qianwen-model-selector/SKILL.md` 拷到 `tmp_workspace/.agents/skills/qianwen-model-selector/SKILL.md`；
2. 隔离 Settings（`artifacts_dir` 指向该工作区、data/trace/kb 落临时目录）建独立 `TaskManager`，跑第二题：prompt 要求「先查看可用 skills，再用 `load_skill` 读取 `qianwen-model-selector` 正文，按其指示推荐一个适合日常文本对话的千问模型，并把正文里的一句话原样引用进回答（以 `<quote>...</quote>` 包裹）」；
3. 断言：① 事件流含 `tool_call` 且 `tool_name == "load_skill"`；② 该调用的 `tool_result` 载荷中 `documents[0]["content"]` 与磁盘上的 SKILL.md 文件内容**逐字符相等**（「整档」语义的真实模型验证）；③ `final_answer` 含 `<quote>` 引文且引文内容出现在 SKILL.md 正文中；④ 任务 COMPLETED；⑤ 同一工作区离线调 `build_inject_block(settings, home_dir=<空目录>)` 断言清单卡出现（证明「清单可发现」的前置成立，确定性检查）；
4. 首腿（KB）保持原样；两腿都跑、任一脚 FAIL → 退出码 1；脚本仍要求真实 Key（无 Key 返回 2，口径不变）。开发者本机 `~/.agents/skills` 若也有同名 skill，按 #5 会返回两份，不影响断言。

### 5.6 文档同步（与代码同 PR，反熵）

- `README.md`：能力清单加「skills 运行时（P1-A）」一条；顶部与门禁段测试计数同步为合入时的实测值（当前基线 525，§四 设计新增 24 例）；
- `AGENTS.md`：§1 快照追加 P1-A 交付；§3 测试行计数（test 文件数 / 用例数）随实测同步；
- `docs/capability-first-principles.md`：§四 T2.2 行改「✅ 已交付（P1-A）」；§六 清单 `⬜ P1-A` 改 ✅；顺手把 §五 结论 4 的「444 测试」改为现役计数（既有过期数字，反熵修正）；
- `docs/roadmap-pawbench.md`：§三 skills 运行时行与 §五 分支拓扑标注交付（以合入后的实际 PR 编号写入，格式对齐邻居行）；
- `.env.example` 与 README 配置计数**零改动**（无新配置，#10）。

## 六、非目标（显式排除）

1. **「宿主自动注入」备选升级方向**（规则正文自动进 system）——#31-6 记录为备选，本 spec 不实现；
2. **references 素材获取**——继续走 `kb_query` / `memory_search`（#31-6 语义边界不交叉）；
3. **skill 自动触发 / 语义匹配**——渐进披露两半已齐（清单常驻 + 按名加载），触发由 description 与模型判断承担；
4. **注入块形态任何改动**——P1-A′ 契约冻结（section 标题、卡片文本、预算语义均不动）；
5. **skills 热加载 / per-folder skills**——沿用 P1-A′ #9（任务级缓存）与 #14（子文件夹暂缓）；
6. **任何新配置项**（#10）；
7. **symlink 逃逸加固或收窄**——维持与注入层同源同权（§3.5）；
8. **失败路径重试 / 熔断**——本地只读，策略钉死为非重试非熔断（#9）。

## 七、实现路径与验收锚点

- **分支**：`feat/skills-runtime` + PR + CI 全绿；零保护文件改动 → 标题不需要 `[OVERRIDE]`。
- **TDD 序**：先落 `test_skill_tool.py`（红）→ 实现（绿）→ 全量回归。
- **验收锚点**：
  1. `.venv311\Scripts\python.exe -m pytest backend/tests/ -q` —— 525 存量 + §四 新增全绿（`test_context_injection.py` 零修改在列）；
  2. `.venv311\Scripts\python.exe -m ruff check backend scripts` 与 `.venv311\Scripts\python.exe -m mypy` —— 均 0 错（零 ignore / 零 override）；
  3. `LLM_API_KEY="$DASHSCOPE_API_KEY" .\.venv311\Scripts\python.exe scripts/live_e2e.py` —— 真实模型双场景（冒烟 + 断点续跑）PASS（回归）；
  4. `LLM_API_KEY="$DASHSCOPE_API_KEY" .\.venv311\Scripts\python.exe scripts/live_skill_test.py` —— 两腿 PASS（KB 腿 + skills-runtime 腿：真实模型实际调用 `load_skill`、正文逐字符相等、答案引用正文原句，断言见 §5.5）；
  5. `.\.venv311\Scripts\python.exe scripts/live_e2e.py --check` 与 `.\.venv311\Scripts\python.exe -m backend.headless --check` 离线冒烟不受影响。
- **零碰清单**（diff 面之外一律不动）：`backend/core/tools/resilience.py`、`backend/core/tools/registry.py`、`backend/core/agent/nodes.py`、`graph.py`、`subagent.py`、`prompts.py`、`context.py`、`backend/services/task_manager.py`、`backend/tests/conftest.py`、`.env.example`。

## 八、实施记录（2026-09-18，PR #42）

- **TDD 序**：tracer 先红（ImportError）→ 最小实现绿；随后 A–E 全 24 例落盘（真红仅初始一次——工具从零新建，其余用例是对既有实现的锁定），全量 525 → **549 passed**；`test_context_injection.py` 零修改全绿（抽取零回归锚）。
- **code-review 双轴修正 3 条**（规范轴/需求轴并行子代理）：① 移除 `run()` 的宽泛兜底 `except` —— 其分支只给 `error` 不进 `data`，与 §3.4 诊断契约相悖（预期失败面已由发现层容错覆盖，意外异常留给内核安全网）；② 层布局收敛为 `inject.SKILLS_LAYER_SUBPATH` 单常量（原 `_miss_message` 重复拼布局，Shotgun Surgery 轻微）；③ 遍历用例升级为「`Path.read_text` 记录器证明目标文件从未被打开」的强断言（原断言只证明结果未泄漏）。
- **live 实证与两处记录修正**（真实模型 `qwen3.7-flash-2026-07-15`，两腿全 PASS）：
  1. §5.5 ③ 的「`documents[0]` 逐字符相等」放宽为「返回的 `documents` 中存在一份逐字符相等」——home 层若也有同名 skill，按 §二 #5 会返回两份且 home 在前，原断言会误判；语义不变、断言更贴 #5。
  2. §3.4 空名单文案实际形态为 `Unknown skill: '<name>'. No skills found under {home}/.agents/skills or {sandbox}/.agents/skills.`（spec 原文 `<home>/skills` 是简写）；测试锁定实际形态（含 `.agents` 与两个根）。
- **live 首跑的两个现场发现**（非缺陷，供后续参考）：① 真实模型可能中途调确认闸门工具（`code_exec`），无人代答会把**同批**的 `load_skill` 一并挂起 —— `live_skill_test.py` 现以操作员身份显式代答确认（生产语义不变，闸门仍被咨询）；② home 层 30+ 张卡会吃满 4000 字符清单预算、把工作区层的卡从尾部挤出（P1-A′ #5 既有语义）——live 第二腿的 prompt 直接点名技能，离线清单断言用空 home 目录。
- **验收**：549 passed / ruff+mypy 0 错 / `live_skill_test.py` 两腿 PASS / `live_e2e.py` 双场景 PASS / 零碰清单未动。
