# langgraph 0.2 → 1.2.x 原地迁移：评估与执行记录

> Issue #7 / spec `docs/specs/langgraph-1x-migration.md` 的 Phase 1 评估档。
> 本文只记**实测结果**与由它推出的决策；未实测的猜测一律标注为推测。
> 复现环境：Python 3.11.15（`.venv311`），Windows / Ubuntu(CI) 双跑。

## 1. 起点

| 组件 | 迁移前 | 迁移后 |
|---|---|---|
| `langgraph` | 0.2.76 | **1.2.11**（1.2.x LTS 线，钉 `>=1.2,<1.3`） |
| `langgraph-checkpoint` | 2.1.2（传递） | **4.2.0**（显式钉死：serde 住在这个包里） |
| `langgraph-checkpoint-sqlite` | 2.0.11（钉死） | **3.1.1** |
| `langchain-core` | 0.2.43（显式钉死） | 1.6.2（**传递**，langgraph 1.x 要求 ≥1.0） |
| `langchain` / `langchain-openai` | 0.2.17 / 0.1.25（显式钉死） | **删除** |
| `uvicorn` / `starlette` | 0.30.6 / 0.38.6 | 不动（AGENTS.md 硬约束） |

依赖面变化（`pip freeze` 逐行对比，全部由上游声明，本项目不直接 import）：

- **新增传递**：`langgraph-prebuilt 1.1.0`、`sqlite-vec 0.1.9`（checkpoint-sqlite 3.x
  的 delta channel 存储）、`langchain-protocol 0.0.19`、`xxhash 4.0.1`、`zstandard 0.25.0`、
  `truststore 0.10.4`、`uuid-utils 0.17.1`、`httpx2/httpcore2 2.12.0`（langsmith 0.12.x 自带）。
- **被抬版本**：`langchain-core 0.2.43→1.6.2`（改为传递引入）、`langgraph-sdk 0.1.74→0.4.4`、
  `langsmith 0.1.147→0.12.4`。
- **被降版本**：`websockets 17.0.1→16.1.1`（新依赖链的上限所致）。仍在
  `uvicorn[standard] 0.30.6` 声明的 `websockets>=10.4` 区间内，且本项目只跑 HTTP + SSE、
  不用 WebSocket，无功能影响。
- **删除**：`langchain 0.2.17`、`langchain-openai 0.1.25`、`langchain-text-splitters 0.2.4`。
- **不变**：`aiosqlite 0.22.1`、`ormsgpack 1.12.2`、`httpx 0.28.1`、`fastapi/uvicorn/starlette`。
  `aiosqlite` 与 `sqlite-vec` 现在是 checkpoint-sqlite 3.1.1 自己声明的硬依赖
  （`Requires-Dist: aiosqlite>=0.20, sqlite-vec>=0.1.6`），会随正常安装到位，
  因此 requirements.txt 里那行手写的 `aiosqlite`（注释还写着 "--no-deps install"）
  已过期，一并删除——只有 mcp 走 `--no-deps`。

`pip check` 唯一告警是 `mcp 1.29.0 要求 uvicorn>=0.31.1，实装 0.30.6` —— **迁移前就存在**，
是 AGENTS.md「mcp 用 `--no-deps` 装、不升级 uvicorn/starlette」的既定代价，不是本次引入。

### 1.1 三行死钉版必须删（不是洁癖）

`langchain` / `langchain-openai` / `langchain-core` 在全仓库 **0 处 import**（`langchain`
字样只出现在注释与 requirements 自身）。但 `langchain-core<0.3`
与 langgraph 1.2.11 的 `langchain-core>=1.0` **直接冲突**——留着它们不是"无用"，而是
让升级在解析阶段就失败。删除是升级的前置条件。

## 2. 旧 checkpoint 快照兼容性：A/B 实测（本次迁移最关键的假设验证）

**迁移前的书面理由**（旧 requirements.txt 注释 / 迁移前的 AGENTS.md P3 约束段）是：
「3.x 需要 langgraph-checkpoint≥4，serde 兼容对 resume 有影响，因此钉死 2.0.11」。
这条理由在迁移前**从未被实测过**，是继承来的假设。

### 2.1 方法

对 `data/checkpoints/checkpoints.sqlite`（由 2.0.11 真实写入：104 行、7 个 thread）
分别在旧栈与新栈下做**只读**探测（`mode=ro` 打开、绝不调用 `setup()`/`put()`，因此不可能
改写被测文件），dump 成 JSON 后做结构化 diff：

```python
conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
# 存储层事实：表名集合、PRAGMA user_version、行数、DISTINCT type
saver = SqliteSaver(sqlite3.connect(f"file:{db}?mode=ro", uri=True, check_same_thread=False))
for tid in thread_ids:                      # 每个 thread 的最新快照
    tup = saver.get_tuple({"configurable": {"thread_id": tid}})
    cp = tup.checkpoint
    record = {
        "checkpoint_id": ..., "channel_value_keys": sorted(cp["channel_values"]),
        "cp_keys": sorted(cp), "versions_seen_n": ..., "channel_versions_n": ...,
        "task_id": ..., "status": ..., "steps_n": ..., "messages_n": ...,
        "metadata": tup.metadata,
    }
```

### 2.2 结果

结构化 diff 的**全部**差异只有三行版本号：

```
.versions.langgraph:                     "0.2.76" -> "1.2.11"
.versions.langgraph-checkpoint:          "2.1.2"  -> "4.2.0"
.versions.langgraph-checkpoint-sqlite:   "2.0.11" -> "3.1.1"
```

存储层与读回层**逐项相同**：表集合 `[checkpoints, writes]`、`PRAGMA user_version = 0`、
104 行、`DISTINCT type = ['msgpack']`；7 个 thread 各自的 `checkpoint_id`、
`channel_values` 键集合（含 0.2 时代的 `branch:reflect:_after_reflect:planner` 分支通道）、
`checkpoint` 顶层键、`versions_seen`/`channel_versions` 条目数、`task_id`/`status`/
`steps`/`messages` 内容、`metadata` 全等。

**结论：3.1.1 + checkpoint 4.2.0 能无损读回 2.0.11 写的快照；"serde 不兼容"这条钉版理由
在读回层不成立。** 两个版本的 `setup()` 建表 SQL 逐字符相同（都没有 schema 版本表），
序列化器同为 `JsonPlusSerializer`（msgpack），所以这个结果并不意外——但它是被测出来的，
不是被假设的。

### 2.3 那为什么仍然作废旧快照？

**读得回 ≠ 跑得续。** resume 要恢复的不是一坨数据，而是 pregel 循环的**执行位置**：
`channel_versions` / `versions_seen` 决定下一步哪些节点被触发，`writes` 表决定未完成任务，
`branch:*` 通道是 0.2 路由留下的痕迹。1.x 重写了 pregel 的调度与分支通道语义（新增
delta channel、`durability` 分级、`Send`/`Command` 路径），"用 1.x 的调度器接着 0.2 的
调度痕迹往下跑"这件事，**上面这个 A/B 没有证明，也不打算去证明**：

1. 证明它需要构造跨版本的真实中断-续跑场景，成本高于收益；
2. `data/` 是纯运行时数据（不入库、可随时重建），作废的代价接近零；
3. 一旦赌错，失败模式是**静默腐蚀 resume 状态**——最难查的一类。

所以按 spec 决策：**声明作废 + 启动检测 + 拒绝挂载 + 不做自动迁移**，理由从"serde 坏了"
（已被实测推翻）改成"跨大版本的执行位置等价性未证明，而数据可弃"（诚实且可辩护）。

### 2.4 检测标记为什么是 `PRAGMA user_version`

上游没有留任何版本痕迹（无 migrations 表、schema 相同、`type` 列同为 `msgpack`），
所以"旧格式"无法从上游数据结构里识别——**标记必须由我们自己写**。候选里
`PRAGMA user_version` 最合适：sqlite 头里的一个整数，`SqliteSaver` 从不读写它，
`executescript` 建表不会重置它，且不需要我们新增表（新增表会与上游 `CREATE TABLE
IF NOT EXISTS` 的假设共存，多一处耦合面）。

判定规则（`CHECKPOINT_FORMAT_VERSION = 1`）：

| 文件状态 | 判定 | kind |
|---|---|---|
| 不存在 | 可用（新建并打标） | `new` |
| 存在、`checkpoints` 与 `writes` 均 0 行、未打标 | 可用（无可腐蚀数据，打标） | `empty` |
| 存在、`user_version == 1` | 可用（本代码库写的） | `stamped` |
| 存在、有行、`user_version == 0` | **拒绝挂载**（升级前遗留） | `legacy` |
| 存在、`user_version > 1` | **拒绝挂载**（更新的 build 写的，本 build 证明不了） | `newer` |
| 读不出来（损坏 / 被锁 / 权限 / 不是 sqlite） | **拒绝挂载**（不带崩启动） | `unreadable` |

两个细节是评审后加的：

- 行数同时数 `checkpoints` 和 `writes`。§2.3 自己论证过 `writes` 决定未完成任务，
  只数 `checkpoints` 会让判据比论证窄。
- 版本判据是 `==` 而不是 `>=`。整个设计的立足点是「未证明即拒绝」，`>=` 会让回滚
  之后的旧代码无条件信任未来版本的库，方向与其他三行相反。

检测连接用 `mode=ro` 打开，这是硬要求而不是洁癖：对 WAL 库而言，**最后一个关闭的
读写连接**会触发 sqlite 把 `-wal` 折回主库并删除 `-wal`/`-shm`——也就是说「决定拒绝它」
这个动作本身会改写被拒文件，把运维事后拿旧栈取证的现场毁掉（而崩溃遗留 `-wal`
恰好就是 P3 关心的场景）。§2.1 的 A/B 探测用的就是 `mode=ro`，生产代码必须同一标准。

拒绝挂载 = `self._checkpointer = None` + ERROR 级日志，**不是**拒绝启动：服务照常起，
新任务照跑，只是 resume 走既有的「no checkpoint → 409」路径（`_has_checkpoint()`
返回 False）。日志文案按 kind 分支，因为两类原因需要**相反**的补救动作：

- `legacy` / `newer`：格式不被本 build 背书 → 把文件移走就恢复 resume（指向本文档）；
- `unreadable` / 打标写入失败：权限 / 完整性 / 锁 / 磁盘满 → 去修根因，**不要**删快照
  （删了既修不好下次新建的库，又白白丢掉历史数据）。

打标写入本身也包在 try/except 里：迁移前的构造函数不写盘，坏库只会让单个任务在
`run()` 里失败；打标一旦被只读目录 / 属主不一致的容器挂载 / 磁盘满挡下又不接住，
就会变成整个 FastAPI 起不来——那是比迁移前更差的降级。

## 3. API 面盘点与破坏面实测

生产代码的耦合面只有 **3 个文件**：`graph.py` 与 `task_manager.py` 直接 import langgraph，
`subagent.py` 经 `build_graph` 间接耦合（`nodes.py` / `config.py` 里的 "langgraph" 只出现
在注释中，不是耦合面）：

| 文件 | 用到的 API |
|---|---|
| `backend/core/agent/graph.py` | `StateGraph` / `add_node` / `add_edge` / `add_conditional_edges` / `set_entry_point` / `END` / `compile(checkpointer=)` |
| `backend/core/agent/subagent.py` | `build_graph(...)` → `graph.invoke(state, {"recursion_limit": N})` |
| `backend/services/task_manager.py` | `SqliteSaver(conn)` / `compile(checkpointer=)` / `invoke(state, config)` / `get_state(config)` / `get_tuple(config)` |

测试侧另有 `test_checkpointer.py` 直接用 `SqliteSaver` + 最小 `StateGraph`。

**实测破坏面 = 0。** 装上新栈、**一行生产代码未改**，跑全量离线套件：

```
351 passed, 1 warning in 54.40s     # 迁移前基线：351 passed in 57.69s
```

1.x 对上述 API 全部保持兼容：`langgraph.graph` 仍导出 `END/START/StateGraph`，
`langgraph.checkpoint.sqlite.SqliteSaver` 构造签名不变（`conn` + 关键字 `serde`），
`invoke/get_state/get_tuple` 语义不变，`recursion_limit` 仍走 config。

**这决定了"重写"的性质**：不是修兼容性（没有东西坏），而是把 2024 年的写法换成 1.x
的惯用写法——否则代码库教的是过期范式，而"跑在新版上"只是数字变化。

## 4. 编排层重写清单（1.x idioms）

| 位置 | 0.2 写法 | 1.x 写法 | 动机 |
|---|---|---|---|
| `graph.py` | `set_entry_point("planner")` | `add_edge(START, "planner")` | `set_entry_point` 在 1.x 里就是 `add_edge(START, key)` 的薄包装；直接写 START 与测试/官方文档一致 |
| `graph.py` | 匿名 `lambda s: ...` 路由 | 具名路由函数 + `-> Literal[...]` 返回注解 | 1.x 从 `Literal` 注解**自动推导 path_map**，拓扑变成静态可声明的（图可视化/校验都受益），且路由有名字可读 |
| `graph.py` | `add_conditional_edges("human_confirm", lambda s: "tool")` | `add_edge("human_confirm", "tool")` | 无条件路由不该占用条件边；静态边表达真实意图 |
| `graph.py` | main/subtask 共用一个路由函数 | 每模式独立路由 + 各自 `Literal` | subtask 拓扑没有 `human_confirm` 节点，共用函数会让推导出的 path_map 指向不存在的节点 |
| `graph.py` | `compile(checkpointer=x) if x else compile()` | `compile(checkpointer=x)` | `checkpointer=None` 本就是默认值，三元是噪音 |
| `task_manager.py` | 直接 `SqliteSaver(conn)` | 挂载前做格式检测 + 打标 | 见 §2.4 |
| `task_manager.py` | `invoke(state, config)` | `invoke(state, config, durability="sync")` | 1.x 新特性，见 §5 |

**明确不动**：`nodes.py`（含 P0 `_needs_confirm` 重算死循环修复）、`resilience.py`、
`registry.py`、`conftest.py` 隔离块、API 层、前端。工具层与熔断层不碰 langgraph。

两个命名决定：

- main 模式的路由保留 0.2 时代的名字（`_after_planner` / `_after_split` / `_after_executor`
  / `_after_tool` / `_after_reflect`），subtask 变体加 `_subtask` 后缀。理由：`nodes.py` 里
  的 P0 红线注释写着 `graph.py's _after_tool ...`，而 `nodes.py` 属于零改动保护范围，
  改名会让那条交叉引用静默失效。
- `_reflect_router(runtime)` 捕获 `runtime` 而不是 `runtime.max_steps`，保持 0.2 闭包的
  晚绑定语义（路由时才读预算）。

### 4.1 测试侧的刻意偏离（记在这里免得被当成先例）

spec Testing Decisions 要求「只断言外部行为，不断言 langgraph 内部 API 调用形态」。
本次有两类断言故意越了这条线：

1. `test_graph.py` 断言 `graph.get_graph().edges`（拓扑声明）。因为**拓扑静态声明本身就是
   这次重写的交付物**：`Literal` 注解丢了不会让任何运行测试变红（langgraph 只是退化成
   “这条边可能去任何节点”），只能从声明面上看。行为等价性另由既有跑图用例保证。
2. `test_checkpointer.py` 少量断言 `tm._checkpointer is None`。“拒绝挂载”的定义就是它，
   且每一处都有行为侧兄弟用例兜底（resume 报 no checkpoint、新任务仍 COMPLETED）。

两类都是例外，不是新模式；新增测试默认仍应只断言外部行为。

## 5. 1.x 新特性选择：durability mode（不选 typed streaming v2）

spec 的选择规则：优先 durability；仅当 spike 证明 typed streaming v2 能被现有 SSE 事件
总线消费且**零前端契约变更**时才改选。

**spike 结论：选 durability。** 依据：

- 本项目的执行形态是「后台线程 + `graph.invoke()` + 自建 `EventBus` → SSE」。事件由
  **节点内部**主动 `publish()`，与 langgraph 的流式输出完全解耦。要用 typed streaming v2
  （`stream(..., version="v2")`）就得把 invoke 换成 stream 循环、把节点内 publish 改成
  流事件映射，SSE 事件时序与类型都会变——**违反"零前端契约变更"**，直接出局。
- durability 只影响 checkpoint 写入时机（`Literal["sync","async","exit"]`，默认 `"async"`），
  不改任何对外契约，且与 P3 断点续跑叙事天然咬合。

**为什么选 `"sync"`**：`resume()` 的契约是「`stop()` 之后快照必须已经落盘」。默认
`"async"` 下 checkpoint 写入在后台进行，`_has_checkpoint()` 里那段"双探 + 0.3s 宽限"
注释写的正是这个竞态（「第一个 superstep 的 put 可能在 stop() 翻转状态之后几百毫秒才
落地」）。`durability="sync"` 让写入在下一个 superstep 开始前完成，从机制上关掉这个窗口。

宽限双探**保留**不删：resume 拒绝语义不可放松（`AGENTS.md`「任务路由」；三类 409 见
`docs/incremental-arch-p3-resume.md` §4），`sync` 只是让它从
"必需的补丁"降级为"纵深防御"。这是有意的冗余，不是忘删。

`subagent.py` 的子任务图**不传** durability：子任务图不挂 checkpointer。

### 5.1 踩到的上游边界（1.2.11）

不挂 checkpointer 时传 `durability="sync"` 不是「无效」而是「崩」：
`pregel/main.py` 先 `warnings.warn("durability has no effect when no checkpointer is
present")`，接着 `if durability_ == "sync": loop._put_checkpoint_fut.result()`——而
`SyncPregelLoop` 在没有 checkpointer 时根本没有 `_put_checkpoint_fut` 属性，
AttributeError 在第一个 superstep 就炸。

这个状态恰好就是「旧库被拒绝挂载」之后的状态（见 §2.4），所以两个决策会相互撞上。
修法：durability 只在 `self._checkpointer is not None` 时才传（`TaskManager._invoke_kwargs()`）。
拒绝挂载必须降级成「resume 不可用」，绝不能降级成「任务全崩」。

这不是推理出来的，是被测试抓住的：`test_new_tasks_still_run_with_legacy_store_present`
（旧库在场时新任务仍须跑完）在加上 durability 后立刻变红。先写行为测试、后接新特性，
刚好把这个交互逼出来。

## 6. 闸门与回滚

闸门（`AGENTS.md`「验证入口与完成证据」+ spec Testing Decisions）：

1. `.venv311 python -m pytest backend/tests/ -q` 全绿（用例数变化须在 commit 说明）；
2. `scripts/live_e2e.py --check` 离线冒烟；
3. `scripts/live_e2e.py` 真实模型双场景（冒烟 + 断点续跑）；
4. CI 全绿（含 `guard-protected-files` 守卫）。

实际结果：

| 闸门 | 结果 |
|---|---|
| 离线套件 | 351 → **365 passed**（+6 旧库守卫、+3 拓扑声明、+2 durability、+3 评审后补的守卫：更高版本标记 / 损坏库 / 不可写库）|
| `--check` 冒烟 | 5/5 PASS |
| 真实模型（qwen3.6-plus） | 场景 1 冒烟 PASS、场景 2 stop→重建→resume→COMPLETED PASS |
| 受保护文件 | `git diff master...HEAD -- conftest.py nodes.py resilience.py registry.py` 为空 |

真实模型跑之前需先做一次运维动作：本地 `data/checkpoints/checkpoints.sqlite`（2.0.11 写的
104 行）会被新守卫正当拒绝，按日志提示把它移走（重命名为 `*.pre-1x-legacy`）即可；
新建的库会打上 `format v1` 并被正常挂载。

一个观察（不属本次范围，不改）：`scripts/live_e2e.py` 场景 1 的终态预算是硬编码的
~60s，而场景 2 用 180s。真实模型一次 planner 调用就花了 ~10s，两轮循环很容易超过 60s：
首跑因此 FAIL（trace 显示无事件、循环正常推进，纯粹是模型延迟），重跑即 PASS。
另外 `OpenAICompatibleClient` 未设请求 timeout（SDK 默认 600s），单次卡顿会吃掉整个预算。

回滚：`requirements.txt` 恢复旧矩阵 + `pip install -r requirements.txt`。数据侧无需回滚
（`data/` 不入库；打标只写 `PRAGMA user_version`，旧栈忽略该值，因此打标后的文件在
回滚后仍可被 2.0.11 正常使用）。

## 7. Phase 2 待办（本次交付刻意不含）

> **2026-09-12 补记：下列四项已全部交付**（Issue #7 Phase 2）。交付摘要见 `OVERVIEW.md` 的
> Issue #7 章节，门禁配置与每条取舍理由见仓库根 `pyproject.toml`。保留下文原样，
> 因为「当时为什么不在迁移分支里做」本身就是阶段划分的一部分。

按 grilling 总结的阶段划分，以下属于 Phase 2，不在迁移分支里做，记在这里免得被当成已交付：

- **ruff + mypy 进 CI**（spec US11、Testing Decisions 的「强制缝」、US22 的「质量门禁配置」）：
  `backend-test` job 内新增两步 + 入库最小配置，合入时基线必须全净。越晚接入要清的债越多，
  而本次重写新增了大量 `Literal` / `tuple[bool, str]` 类型面，正是 mypy 能立即锁住的收益。
  → **已交付**：`pyproject.toml`（ruff select=`E4,E7,E9,F,I`、mypy `files=["backend"]`）+
  `requirements-dev.txt`（钉死）+ CI 三步。实测基线：ruff 55 处全修（零 `per-file-ignores`），
  mypy 59 处修完 + `registry.py` 2 处走唯一一条 override（冻结文件）。mypy 额外抓出五处真缺陷：
  `trace.py` 把文件句柄抹平成 `object`、`task_trace` 的返回注解与真实返回不符、
  `_submit` 对 `self.loop` 的 TOCTOU 双读、planner 异常分支的 `plan` 形态不一致（只加注解未改行为）、
  `signal.SIGKILL` 在 Windows 上不存在（mypy 按当前平台解析，会让本地红而 CI 绿）。
- **README 重写为叙事型**（US3：迁移章节的动机/矩阵/代价/闸门/安全闭环）：当前只加了
  顶部事实段 + 指向本文档的链接。
  → **已交付**：定位与亮点 → 架构图（mermaid 拓扑 + 分层）→ 迁移章节 → 组合分工互链 → 运行与门禁。
- **与 `interview-agent-py` 互链并显式写分工**（US15）：本仓库单侧先写。
  → **已交付**：README §4（分工对照表 + 一句话分工）。
- **OVERVIEW.md 补 Issue #7 章节**：既有章节是历史交付日志（里面的 351/2.0.11 是当时事实），
  不改旧章节，只追加新一章。
  → **已交付**：旧章节逐字未动，末尾追加 Issue #7 一章。

> **2026-09-12 后续注记**：上文「`registry.py` 2 处走唯一一条 override」已在解冻窗口修正——
> `get_tool` / `make_openapi_tool` 两处签名改正、`pyproject.toml` 那条唯一 mypy override 已撤除，
> mypy 现零 override。详见 `OVERVIEW.md` Issue #7 后续候选 #1。
>
> **2026-09-22 后续注记**：上文「planner 异常分支的 `plan` 形态不一致（只加注解未改行为）」已修——
> planner 异常改为写 `state["error"]` + 空 plan，`_after_planner` / `_after_planner_subtask` 在 `error`
> 时短路到 `finish`（判定 `FAILED` 并保留真实原因）。详见 `OVERVIEW.md` Issue #7 后续候选 #2 /
> [Issue #12](https://github.com/renjianguojinqianfan/langgraph-agent/issues/12)。
