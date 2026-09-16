# P1-B 回滚：写前文件级 before-image + 独立端点整任务回滚

> Issue [#33](https://github.com/renjianguojinqianfan/langgraph-agent/issues/33)（数据面）+ [#34](https://github.com/renjianguojinqianfan/langgraph-agent/issues/34)（行为面）· 2026-09-16 grilling 拍板 · 执行序：P1-A′ → T1.4 → **P1-B** → P1-A → 评测台

## 一、背景与目标

`docs/capability-first-principles.md §四` T1.5 是 Tier 1 唯一未闭合的**半缺口**：预防面（EHRB + 确认闸门 + git 黑名单 + 路径白名单）反超标杆，但 **P8 的补救半边（回滚）**缺失——任务跑砸后工作区无法回到任务开始前，只能人工清理。本 spec 落地「每写一次拍一张旧照片，搞砸了按相册倒着放回去」：

1. **文件级 before-image（undo log）**：每次沙箱内文件写**之前**把该文件的旧版本复制到沙箱外的 `data/snapshots/<task_id>/`，并记一行账本；回滚 = 账本逆序重放，回到任务起点。
2. **独立端点显式触发**：`POST /api/tasks/{task_id}/rollback` —— 零自动回滚（六家对标全为显式触发）。
3. **与 resume 完全解耦**：回滚只动沙箱文件，checkpoint（图状态）一字不碰；resume 的三条 409 一字不改。

术语固定（#33）：**快照 / before-image / 账本（undo log）= 本文件回滚**；**checkpoint = P3 图状态持久化**（`data/checkpoints/`）。两词不得混用；「回滚」单独出现指文件回滚，图级恢复一律叫 resume。

## 二、决策基线（#33 数据面六项 + #34 行为面八项 + 默认包六项）

| # | 决策点 | 结论 |
|---|---|---|
| 1 | 快照单位 | **文件级 before-image**——成本随被改文件数涨、不随沙箱体积涨；「回到起点」与「回到中间点」同一套逆序重放机制（Claude Code 同构）|
| 2 | 拍点 | **每写一次**（写类工具执行前捕获）——六家全部写前触发；闸口拍点被代码事实否决（沙箱文件写不在确认清单 + 评测态闸口整体旁路）|
| 3 | 存哪 | **沙箱外独立目录** `data/snapshots/<task_id>/`（Settings 新键可配）；headless 随既有 throw-away run root 走、**不进 workdir**（保 #35 契约）|
| 4·11 | 保留 | **30 天**（`snapshot_retention_days` 可配）+ **TaskManager 启动时顺带清扫**（`_reconcile_orphans` 同钩子位；无调度宿主，无新线程/定时器）|
| 5 | 账本边界 | **沙箱根为界**：沙箱内文件写全记账（子代理写归主任务账本）；沙箱外（code_exec 临时目录 / `git_*` 外部 repo / MCP·OpenAPI 远端）**声明不保** |
| 6 | 限额护栏 | v1 不设体积上限（N 天清理 + 按任务分目录已让体积有界）；未来加显式上限 + 告警，不做静默丢弃 |
| 7 | 捕获落位 | 新 `backend/services/snapshots.py`（capture / restore / cleanup）+ `file_io.py` 两个写点各一行调用；task_id 传递 = **contextvar**（`TaskManager.run()` / `_resume_run()` 工作线程入口设置）——账本逻辑单一权威文件，file_io 退役（Issue #17）时钩子随之消失，零碰保护文件 |
| 8 | 触发形态 | **仅独立端点** `POST /api/tasks/{task_id}/rollback`（用户显式触发）；「拒绝时自动回滚」被代码事实否决——闸口拒绝那一刻沙箱里没有该工具写的文件可撤，自动回滚会误撤之前已确认的成功写 |
| 9 | 与 resume 共存 | **完全解耦**（候选 C）：回滚只恢复文件、checkpoint 不动；resume 三条 409 一字不改。「回滚后 resume」= agent 看到回到旧版本的文件继续跑，是自然语义而非 bug |
| 10 | 回滚粒度 | v1 **整任务回滚**（账本逆序全重放，回到任务起点）；按点回滚（target 参数）、单步撤销留记录修正路径 |
| 12 | 护栏三条 | ① **fail-open**：快照失败不阻塞工具调用（WARNING，漏拍的写回滚时撤不掉，文档声明）② **可撤销撤销·数据面**：恢复前先把当前版本拍进留存账本（撤销入口 v2，纯增量）③ **活跃任务拒绝回滚 409**（RUNNING/PENDING 或仍有工作线程在收尾——防重放与活跃写交错，对齐 resume 的 409 风格）|
| 13 | 撤销回滚入口 | **v1 不开**：整任务粒度下逆序重放幂等于起点（重复调用 200「已是原样」）；数据面留存照拍（见 12②）|
| 14 | 前端 | **并入本 spec**：TaskHeader 回滚按钮 + 行内确认弹窗，不拆票 |
| 包 1 | 事件 | SSE 发布 `task_rollback`（含恢复文件列表）+ trace 落盘（TraceRecorder 已订阅事件总线，发布即留痕）|
| 包 2 | headless | **不暴露**回滚——评测态单发、进程即退，快照随 throw-away run root 删除，无物可滚 |
| 包 3 | 可回滚状态 | 非活跃（COMPLETED / FAILED / INTERRUPTED）皆可调；活跃（RUNNING / PENDING / 线程未收尾）→ 409 |
| 包 4 | 无账本 | 任务未写过文件 → 200 + `files=[]`（本就是起点，不算错误）|
| 包 5 | ADR | 不立——spec 级决策（端点可撤、账本格式可换，逆向成本中等），权威落本 spec + #33/#34 resolution |
| 包 6 | 文档 | capability §六 记录修正已执行（2026-09-17：「接确认闸口同一路径」被代码实证否决）；实现合并后 capability §五「Tier 1 硬缺口 0.5 个」→ **0**、roadmap §三 P1-B 行 → ✅ |

## 三、设计

### 3.1 账本模块（`backend/services/snapshots.py` 新增）

```python
LEDGER_NAME = "ledger.jsonl"

def set_current_task_id(task_id: str) -> None      # contextvar 写入（工作线程入口）
def get_current_task_id() -> Optional[str]

def capture_before_image(path: Path, *, settings: Settings | None = None) -> bool
def restore_task(task_id: str, *, settings: Settings) -> Dict[str, Any]
def cleanup_expired(*, settings: Settings) -> int  # 删除过期任务目录数（启动清扫）
```

**目录布局**（`snapshot_dir` 默认 `<data_dir>/snapshots`）：

```
data/snapshots/<task_id>/
  ledger.jsonl          # 一行一次捕获（时间序 = 写序）
  0001.bak, 0002.bak …  # before-image 字节副本（仅 existed=true 的条目有）
  retention/            # 恢复前留存（拍板 12②，撤销入口 v2 的原料）
    retention.jsonl
    <seq>.bak
```

**账本行契约**（`ledger.jsonl`，UTF-8，一行一 JSON）：

```json
{"seq": 1, "path": "sub/a.txt", "existed": true, "backup": "0001.bak", "chars": 1234, "ts": "2026-09-17T02:00:00+00:00"}
{"seq": 2, "path": "new.txt",   "existed": false, "backup": null,       "chars": 0,    "ts": "…"}
```

- `path` = **沙箱根相对路径**（POSIX 风格），跨平台可读；
- `existed=false`（本次写是新建文件）→ 回滚时删除该文件——「回到它还不存在的时候」。

**`capture_before_image` 规则**（fail-open，永不抛）：

1. `settings is None` / `snapshot_enabled=false` / contextvar 无 task_id → 静默跳过；
2. `path` 解析后必须落在沙箱根内（与工具同一套包含判定），否则跳过；
3. 模块级 `threading.Lock` 保护「读账本行数 → 定 seq → 写 .bak → 追加账本行」临界区（子代理线程池可并发写同一任务账本，seq 与文件名不得相撞）；
4. 文件存在 → `shutil.copyfile` 复制到 `<seq:04d>.bak`（字节级，不解析编码）；不存在 → `existed=false`；
5. 任何异常 → `logger.warning` + 返回 False（补救机制不得有杀死主路径的能力，Claude Code 先例）。

**`restore_task` 规则**（整任务回滚 = 逆序重放）：

1. 无账本目录 / 无 `ledger.jsonl` → `{"ok": True, "files": [], "already_original": True}`；总开关 false 同形（零回归）；
2. **路径闸门先行**：账本里出现过的每个 path（首次出现序去重）先过沙箱包含判定——逃逸项在此丢弃（不读、不写、不进摘要与留存）；
3. **留存拍摄**（12②）：对每个已确认的 path，当前存在者复制进 `retention/` + 追加 `retention.jsonl`（每次恢复追加一份，随调用次数增长——v1 不限额，拍板 6）；失败仅 WARNING，不阻断回滚；
4. **基线摘要**：记录每个 path 当前的 sha256（不存在 = None）；
5. **逆序重放**：账本行倒序，逐行 `existed=true` → `.bak` 复制回沙箱（建父目录）；`existed=false` → 删除该文件（不存在则跳过）。重放前后**逐路径判等**（sha256）得 `files`（**实际发生变化的**路径列表，POSIX 相对路径）；
6. 返回 `{"ok": True, "files": [...], "already_original": bool}`（`already_original = 无任何路径变化`——第二次调用天然命中，兑现拍板 13 的幂等语义）；
7. 备份名同样要过闸门（只接受裸文件名，防账本被篡改指向别处）；异常 → 抛出由调用方（TaskManager）转 500 / 事件不发布；单行损坏（JSON 解析失败）跳过该行不炸整次回滚。

### 3.2 捕获落位（`file_io.py` 两处一行钩子）

| 工具 | 位置 | 钩子 |
|---|---|---|
| `FileIOTool`（`action=write`）| `target.write_text` 之前 | `capture_before_image(target, settings=self.settings)` |
| `EditTool` | `target.write_text(new_text)` 之前 | 同上 |

- 沙箱文件写只有这两个入口（#34 代码交叉验证）；`code_exec` 写临时目录、`git_*` 在外部 repo、MCP/OpenAPI 在远端，均不在账本边界内（拍板 5）。
- **子代理**：`SubAgentExecutor._exec_one` 入口补一次 `set_current_task_id(spec.parent_task_id)`——线程池工作线程持有**全新 context**（contextvar 不跨线程传播），不补这一行则内置「调研+报告」场景的子任务写会漏账；`spawn_subagent` 路径与主图同线程，天然继承（**实现期事实修正**：#34 拍板 7 只写了 `run()` / `_resume_run()` 两个设置点，其「子代理同线程执行」的前提在并行场景不成立；修的意图不变——子代理写归主任务账本，兑现 #33 拍板 5）。
- file_io 之外**零改动**：`resilience.py` / `registry.py` / `nodes.py` 闸口块一律不碰。

### 3.3 TaskManager 编排

```python
def rollback(self, task_id: str) -> Dict[str, Any]:
    """Restore the sandbox to its pre-task state (P1-B)."""
    task = self.persistence.load_task(task_id)          # 不存在 → RuntimeError → 404/409
    live = self._threads.get(task_id)
    if task.status in (TaskStatus.RUNNING, TaskStatus.PENDING) or (live is not None and live.is_alive()):
        raise RuntimeError(...)                          # 路由映射 409
    result = restore_task(task_id, settings=self.settings)
    self.event_bus.publish(task_id, "task_rollback", {"task_id": task_id, **result})
    return result
```

- **启动清扫**：`__init__` 里 `_reconcile_orphans()` 之后调 `cleanup_expired(settings=self.settings)`——遍历 `snapshots/` 顶层任务目录，寿命取「目录与 `ledger.jsonl` 中较新的 mtime」（追加不推进目录 mtime，故以 ledger 为准），早于 `now - retention_days` 者 `shutil.rmtree`；**受总开关约束**（`snapshot_enabled=false` 时不清扫——停用的特性不得删除数据）；失败仅 WARNING（不得拖垮启动）。
- **contextvar 设置点**：`run()` 与 `_resume_run()` 各自在开头 `set_current_task_id(task_id)`（worker 线程自有 context，互不串扰）。
- **rollback 的 trace 归属**（实现期补齐）：终结任务的 TraceRecorder 已 close，故 `rollback()` 发布事件前 `attach`、发布后 `close`（与 `resume()` 的「重开审计段」同款）——否则事件只到 SSE、JSONL 里没有，兑现包 1「trace 记录」；代价是终结任务 JSONL 追加第二枚 `trace_end`（追加式审计的既有语义）。

### 3.4 REST 端点（`routes.py`）

| 方法 | 路径 | 语义 |
|---|---|---|
| POST | `/api/tasks/{task_id}/rollback` | 回滚整任务的文件改动 |

- 任务不存在 → 404；活跃（RUNNING/PENDING/线程未收尾）→ 409（`RuntimeError` → `HTTPException(409)`，与 resume 同款映射）；成功 → 统一信封 `{code:0, data:{ok, files, already_original}, message:"ok"}`。
- 零新请求体 schema（无参数）。15 → 16 REST。

### 3.5 配置（`config.py` 新 snapshot 族）

| 字段 | 默认 | 含义 |
|---|---|---|
| `snapshot_enabled` | `True` | 总开关；false = 零捕获零回滚（`restore` 落「无账本」分支，200 + `files=[]`）|
| `snapshot_dir` | `""` | 空 → `<data_dir>/snapshots`（同 `checkpoint_dir` 模式）|
| `snapshot_retention_days` | `30` | 启动清扫阈值（天）；`<=0` → 不清理 |

conftest 加 `SNAPSHOT_ENABLED=false` 隔离行（离线用例默认零快照；本特性测试显式开）。

### 3.6 前端（并入本 spec）

- `TaskHeader`：任务处于非活跃状态时显示「回滚」按钮 → 行内确认（「恢复到任务开始前？」+ 确认回滚/取消）→ 调端点；成功后行内提示「已回滚 N 个文件」/「已是原样（无可回滚的改动）」；被拒（FastAPI 错误体 `{detail}`）提示「回滚被拒绝（任务尚未收尾）」。任务记录本身不被回滚改动，故无需刷新详情。
- `api/client.ts` 加 `rollbackTask(id)`；`types/index.ts` 加 `RollbackResult`。SSE `task_rollback` 事件前端按未知类型自然忽略（与 T1.4 同策略）。

## 四、测试设计（`backend/tests/test_rollback.py`，含纯函数层 + 端到端层）

纯函数层（`snapshots` 直调，deterministic）：

1. 无 task_id（contextvar 空）→ 不捕获、不建目录
2. 总开关 false → 不捕获
3. 新建文件 → 账本 `existed=false`、无 .bak；回滚 → 文件被删除
4. 修改已存在文件 → `.bak` 内容 = 旧版本；回滚 → 内容回到旧版本
5. 同文件两次写 → 逆序重放回到**最老**版本（整任务语义）
6. 多文件（含子目录）→ 全部复原
7. 沙箱外路径传入 → 跳过（不建账本、不复制）
8. 捕获失败 fail-open：`.bak` 不可写（如账本目录被占用/只读）→ 返回 False 且**不抛**，工具写继续
9. 恢复前留存：`retention/` 有当前版本副本 + `retention.jsonl` 条目
10. 幂等：连续两次 restore → 第二次 `files=[] / already_original=true`
11. 无账本 restore → `files=[] / already_original=true`（不炸）
12. 账本行损坏（一行非 JSON）→ 跳过该行、其余正常重放
13. 篡改账本 `path` 为 `../../evil.txt` → 跳过该行，沙箱外无文件产生
14. `cleanup_expired`：过期目录被删、未过期目录保留（用文件 mtime 造旧）

端到端层（`TaskManager` + mock LLM，仿 `test_resume` 风格）：

15. 工具写入走完整 run → 产生账本；`tm.rollback(task_id)` → 文件复原 + `task_rollback` 事件发布（载荷含 `files`）
16. 活跃任务 → `rollback` 抛 RuntimeError（路由层 409 语义），文件未被动
17. 双判口径：RUNNING 状态（无存活线程）→ 仍 409（状态本身即闸门）；已终态但线程仍存活（stop 收尾窗口）→ 409
18. REST 层（TestClient，仿既有路由测试）：404 未知名 / 409 活跃 / 200 成功三态 + 信封形状
19. 子代理线程池场景（`run_plan_with_subtasks` 或直接 `_exec_one`）→ 子任务写归主任务账本（contextvar 修正生效）
20. `snapshot_enabled=false` → 改造前行为逐字节等价（写文件后无 `snapshots/` 目录）
21. resume 解耦：有账本的任务 resume 三条 409 语义不变（不引入新拒绝路径）——用既有 resume 用例口径抽查一条

## 五、验收标准

- `pytest backend/tests/ -q` 全绿（493 + 新增 32 = 525）；`ruff check backend scripts` 与 `mypy` 0 错（不许 ignore/override）。
- 保护文件零改动（`resilience.py` / `registry.py` / `_needs_confirm` 重算块）；`mypy` 现零 override 维持。
- `npx tsc --noEmit` 0 错误（前端改动）。
- `scripts/live_e2e.py`（`LLM_MODEL=qwen3.7-flash-2026-07-15`）双场景 PASS。
- 同步 `.env.example`（`snapshot_*` 段）/ README（能力条目 + 事件计数 22→23）/ `docs/architecture.md`（现役计数 + 事件行）/ AGENTS.md（快照计数、目录表、spec 档案、隔离清单）。
- 文档同步（本 PR 内执行，见 §五·补）：capability §四/§五/§六/§七、roadmap §三/§五；AGENTS.md 快照计数、目录表、spec 档案、隔离清单。
- 合并后：在地图 #27 留 resolution comment（destination 已达成 + P1-B 落地）并关图。

## 五·补 实施记录（2026-09-17）

- **本 PR 内**：capability §四（2026-09-17 复核）/§五 硬缺口 → 0 /§六 P1-A′·T1.4·P1-B 勾选 /§七 P1-B 行 → ✅；roadmap §三 P1-B 行 → ✅（PR #41）+ §五 分支拓扑。
- **live 实证**：`LLM_MODEL=qwen3.7-flash-2026-07-15` 双场景 PASS；真实任务在 `data/snapshots/<task_id>/` 留下真实账本（场景 1：`agent_summary.txt` 一份 before-image；场景 2 断点续跑：`r1/r2/r3.txt` 三份）——写入捕获在 run 与 resume 两条路径上都生效。
- **测试**：525 全绿（新增 32 例）；ruff/mypy 0 错；前端 `tsc --noEmit` 0 错。
- **review（双轴）后的实现修正**（如实记录）：① 逃逸闸门前移到「去重即丢弃」（原只在重放步判，摘要步仍会读逃逸路径）② 清扫受总开关约束（停用不得删数据）+ 寿命取目录/ledger 较新 mtime（追加不推进目录 mtime）③ 沙箱包含判定收敛为模块内单一谓词 `_under_root` ④ 留存 seq 改为先写后用、去掉回推 ⑤ 前端失败提示按 `{detail}` 分支。
- **口径**：`cleanup_expired` 仍按「目录 mtime」写进 #33 拍板 11 的 spec 文本，实现取更准的 max(dir, ledger)——不改变决策意图（N 天保留），只修正判据。

## 六、约束与红线

- **fail-open 三处**：捕获失败不阻断工具；留存失败不阻断回滚；清扫/启动检测失败不影响服务启动。
- 沙箱根为界：账本、留存、.bak 全部落在**沙箱外**（`data/snapshots/`），沙箱内不新增任何目录（否则 glob/grep/read 可见 + KB 自动索引成幽灵文档）。
- 零 LLM 调用、零新依赖、零破坏性外部状态；回滚只写沙箱文件与快照目录两处。
- `rollback` 不触碰 `_active_states` / `_stop_flags` / checkpoint——与 resume 正交。