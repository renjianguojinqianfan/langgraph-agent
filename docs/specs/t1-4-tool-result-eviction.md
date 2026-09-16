# T1.4 Tool-Result Eviction：先搬大件再摘要

> Issue [#38](https://github.com/renjianguojinqianfan/langgraph-agent/issues/38) · 2026-09-16 grilling 拍板 · 执行序：P1-A′ → **T1.4** → P1-B → P1-A → 评测台

## 一、背景与目标

仓库已有 compress（对话历史压缩，默认 8000 tokens 预算），但**工具返回的大段原文**压不掉、一直占据上下文——长任务（L3 占 PawBench 106/150）后期上下文持续变脏。本 spec 落地「先搬大件，再写日记」：

1. **eviction 在 compress 之前**：先把又旧又大的 tool result 从 `messages` 里换成占位（纯机械、零 LLM 调用）；腾出空间不够再走既有 compress。
2. **可回读**：占位保留「全文在哪」的留痕指针——trace JSONL 本来就有底（`tool_result` 事件携带全文 `output`），成本近零。**否决 Codex 式不可逆中间截断**。

## 二、决策基线（7 项拍板 + 1 项预算调整，grilling round 1 定案）

**预算调整（2026-09-16 追加拍板）**：`context_token_budget` 默认 `8000 → 32000`。对标 dsh——但 dsh 是**比例制**（`thresholdRatio 0.8` × 路由模型窗口，deepseek-flash 窗口 131072 → 触发线 ≈105K tokens，保留最近 16%），不是绝对值；直接抄 105K 不安全：本仓估算器是 `chars/4`，对中文内容**低估**真实 token 2~3 倍（1 中文字 ≈ 0.6~1 token）。32000 估算值：英文/代码真实 ≈32K（128K 窗口的 25%，宽裕）；纯中文极端 ≈64~96K（50~73%，仍安全，且 LLM 溢出报错有 executor 降级兜底）。eviction 阈值 4000 字符维持不变——它是绝对「大件」语义，不随预算浮动（dsh pruner 8192 字符为参照，我们预览更小故阈值同比更小）。已同步：`config.py` / `.env.example` / README；`test_context_injection.py` 三个压缩共存测试改为显式传 `context_token_budget=8000` 与默认值解耦。

| # | 决策点 | 结论 |
|---|---|---|
| 1 | 占位符形态 | 标记行（工具名 + 原文字符数 + 留痕指针）+ **头 800 字符 + 尾 400 字符**预览；`role`/`tool_call_id` 原样保留（OpenAI 配对不断） |
| 2 | 触发阈值与保护带 | 单条 `content > 4000 字符`（≈1000 est. tokens）才逐——**绝对「大件」语义**（参照 dsh pruner 8192 字符，我们预览更小故阈值同比更小），不随 `context_token_budget` 浮动；**保护带 = 最近 10 条消息**（与 `context_keep_recent=10` 对齐）；全用字符口径（与 `estimate_tokens` 的 chars/4 一致） |
| 3 | 回读指针形态 | `<trace 文件绝对路径>#<tool_call_id>`（`tool_call_id` 任务内唯一，grep 即定位）；`trace_enabled=false` 时文案降级为「无（trace 未开启）」。**指针面向人/评测台做审计留痕**——trace 在沙箱外，agent 经 `read` 工具读不到；agent 的「回读」= 对幂等工具（read/grep）重跑一次 |
| 4 | 与 compress 的挂接 | `_build_messages` 内 `compress_messages()` **之前**常驻运行，顺序固定 evict → compress；搬完大件若已低于预算，compress（尤其 summarize 的 LLM 调用）不触发 |
| 5 | `read` 工具结果 | **不跳过**，全工具平等。防循环三重兜底：保护带（刚读的不动）+ 占位文案明示「已移除」+ `max_steps` 有界；PawBench 文件精读恰是大户，白名单 = 自废武功 |
| 6 | 逐出选择策略 | 保护带外超阈值者**一轮全逐**，无「最旧/最大优先」排序决策；占位可回读 = 逐多了零信息损失 |
| 7 | 操作面 | 5 个 `context_evict_*` 配置（默认开启）；conftest 加 `CONTEXT_EVICT_ENABLED=false` 隔离；每次逐出发 `tool_result_evicted` 事件（SSE + trace 留痕） |

## 三、设计

### 3.1 纯函数（`backend/core/agent/context.py` 新增）

```python
EVICT_PLACEHOLDER_PREFIX = "[tool result 已移除"

def evict_tool_results(
    messages: List[Dict[str, Any]],
    *,
    threshold_chars: int = 4000,
    protect_recent: int = 10,
    head_chars: int = 800,
    tail_chars: int = 400,
    trace_ref: str = "",
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """保护带外超阈值 tool 消息的 content 原地换占位（消息本身不删，配对不断）。

    返回 (messages, evictions)：无逐出时原列表对象原样返回（identity，同
    compress_messages 约定）；evictions 为 [{tool_call_id, tool_name,
    original_chars}]，供调用方发事件。
    """
```

规则：

- 只动 `role == "tool"` 且 `content` 为 `str` 且 `len(content) > threshold_chars` 的消息；其余角色、多模态 list content 一律不碰。
- 保护带：列表末尾 `protect_recent` 条消息不逐（按位置，不问角色）。
- 幂等：content 以 `EVICT_PLACEHOLDER_PREFIX` 开头的跳过（每轮 LLM 调用都会跑，占位不会被套占位）。
- 退化防护：`head_chars + tail_chars >= len(content)` 时跳过（无可省空间）。
- 工具名从同列表 assistant 消息的 `tool_calls` 反查（id → `function.name`），查不到显示 `?`。
- 逐出消息整体换新 dict（不原地改旧 dict），未逐出的消息引用不变。

### 3.2 占位符契约（文本格式）

```
[tool result 已移除 | 工具: read | 原文 12345 字符 | 留痕: <trace_ref>#call_3_0]
--- 头部预览（前 800 字符）---
<head>
--- 尾部预览（后 400 字符）---
<tail>
```

`trace_enabled=false` 时留痕段为 `留痕: 无（trace 未开启）`。

### 3.3 挂接点（`nodes.py::_build_messages`，compress 之前）

```python
if settings.context_evict_enabled:
    trace_ref = str(settings.trace_path / f"{self.task_id}.jsonl") if settings.trace_enabled else ""
    messages, evictions = evict_tool_results(messages, threshold_chars=..., ...)
    if evictions:
        state["messages"] = messages
        for ev in evictions:
            self._publish("tool_result_evicted", {"step_index": ..., **ev})
```

- 默认开启 → 既有 480 测试由 conftest 的 `CONTEXT_EVICT_ENABLED=false` 隔离，零回归锚：`context_evict_enabled=False` 时 `_build_messages` 与改造前逐字节相同。
- 子图复用同一 `_build_messages`，自动获得 eviction（同 P1-A′ 注入）。
- `state["context_tokens"]` 由 compress 的 meta 汇报，eviction 在前 → 汇报值自然反映逐出后的体量。

### 3.4 配置（`config.py` context 族）

| 字段 | 默认 | 含义 |
|---|---|---|
| `context_evict_enabled` | `True` | 总开关；false = 改造前行为 |
| `context_evict_threshold_chars` | `4000` | 单条 tool content 超此字符数才逐 |
| `context_evict_protect_recent` | `10` | 末尾 N 条消息保护带 |
| `context_evict_head_chars` | `800` | 头部预览字符数 |
| `context_evict_tail_chars` | `400` | 尾部预览字符数 |

### 3.5 断点续跑兼容

`state["messages"]` 本就进 checkpoint：换占位后快照内容即占位，resume 天然继承逐出后的历史，无新增工作。trace 文件按 task_id 锚定，指针跨 resume 有效。

## 四、测试设计（`backend/tests/test_eviction.py`）

纯函数层（deterministic）：

1. 低于阈值 → identity（同一对象）+ 无 evictions
2. 超阈值旧 tool 消息 → 占位含标记/工具名/原文字符数/`trace_ref#id` 指针；头 800 + 尾 400 在；`role`/`tool_call_id` 保留
3. 保护带内超阈值 → 不动；带外才逐
4. 非 tool 角色（user/assistant/system）超大也不逐
5. 幂等：已占位的 content 不再逐、不计 evictions
6. 多条超阈值 → 一轮全逐
7. `trace_ref=""` → 降级文案「无（trace 未开启）」
8. 工具名反查：assistant `tool_calls` 配对命中；查不到 → `?`
9. 退化配置：head+tail ≥ 原长 → 跳过
10. 多模态 list content 的 tool 消息不逐

runtime 层（仿 `test_context_injection.py` 的 fake tm）：

11. `_build_messages` 内 evict→compress 顺序：三条 12000 字符旧 tool 消息（显式传 `context_token_budget=8000` 与默认值解耦，总量超预算），逐出后低于预算 → `compressed=False`（**先搬大件再摘要**的资金测试）
12. `context_evict_enabled=False` → 输出与改造前逐字节相同（零回归锚）
13. 逐出时 `tool_result_evicted` 事件发布，载荷含 tool_call_id / tool_name / original_chars / step_index

## 五、验收标准

- `pytest backend/tests/ -q` 全绿（480 + 新增）；`ruff check backend scripts` 与 `mypy` 0 错。
- `scripts/live_e2e.py`（qwen3.7-flash-2026-07-15）双场景 PASS。
- 同步 `.env.example`（`context_evict_*` 段）与 README（能力条目 + 事件计数若变）。
- 不碰保护文件（`resilience.py` / `registry.py` / `_needs_confirm` 重算块）；改动集中 `context.py` / `nodes.py` / `config.py` / conftest / 新测试文件。

## 五·补 实施进度快照（2026-09-16，接力会话收口）

分支 `feat/t1-4-tool-result-eviction`。spec 主体全部交付：

- ✅ 预算调整：`config.py` 默认 8000→32000、`.env.example` / `README.md` 同步、`test_context_injection.py` 三个压缩共存测试显式传 `context_token_budget=8000` 与默认值解耦
- ✅ `context_evict_*` 五个配置字段（默认开启）+ conftest `CONTEXT_EVICT_ENABLED=false` 隔离行
- ✅ `context.py::evict_tool_results`（identity / 幂等 / 退化防护 / 多模态不碰，全按 §3.1）+ `nodes.py::_build_messages` 挂接（evict → compress 固定序、`tool_result_evicted` 事件、失败降级不阻断）
- ✅ `backend/tests/test_eviction.py` 13 用例（§四 逐条对应）
- ✅ 文档同步：README 能力条目 + 事件计数 21→22、`.env.example` 的 `context_evict_*` 段、`docs/architecture.md` §3.4 事件表与「已核」计数、AGENTS.md 快照（493 计数 / 隔离清单 / 目录表 / spec 档案）
- ✅ 门禁全量：`pytest backend/tests/ -q` = **493 passed**；`ruff check backend scripts` 与 `mypy` 0 错
- ✅ live：`LLM_MODEL=qwen3.7-flash-2026-07-15` 连通性探测 OK + `scripts/live_e2e.py` 场景 1（冒烟）/ 场景 2（stop→resume）双 PASS；期间该模型一次调用挂起（>180s 轮询窗口）致场景 2 单次失败，A/B 复跑通过（该场景消息数 < 保护带，eviction 实为 no-op），判定模型侧偶发、非回归。模型口径与免费额度核实（qwen3.6-plus 已 expire）见 AGENTS.md §2「真实 LLM 验证」
-  提交 / PR / CI 全绿合并 / 关闭 issue #38（resolution comment 引本 spec）

实现期一处对测试设计的偏离（如实记录）：§四 #11 对照组与 #12 显式传 `context_token_budget`——本地 `.env` 的 `context_token_budget=8000` 会经 pydantic-settings 泄漏进 `make_settings`（conftest 只中和 provider 键），属 §二「与默认值解耦」同一问题，未被本 spec 修复（存量行为，另行评估）。

## 六、约束与红线

- 纯机械、零新增 LLM 调用；eviction 失败不阻断主流程（防御性 try/except 降级为不逐 + warning，同既有「失败只降级不中断」语义）。
- 占位文案用中文（与既有 `[上下文已截断：…]` 风格一致）。
- SSE 事件 `tool_result_evicted` 为新增类型，前端按未知事件自然忽略（事件总线 fan-out 无强类型约束），README 事件计数随之 +1。
