# P3 增量架构：断点续跑（LangGraph Checkpointer）

> Spec：[Issue #4](https://github.com/renjianguojinqianfan/langgraph-agent/issues/4) · 实施日期：2026-08-27 · 测试基线：351 passed（331 既有 + 20 新增）

## 1. 目标与范围

任务执行内核从「内存态」升级为「可恢复态」：

| 之前 | 之后 |
|---|---|
| 停止 = 永久残废（INTERRUPTED 终态） | 停止后可 `POST /tasks/{id}/resume` 从断点续跑 |
| 进程崩溃 → RUNNING 僵尸卡死 | 启动对账自动转为 INTERRUPTED（可恢复） |
| 状态全靠 `_active_states` 内存字典 | SqliteSaver 落盘，重启后 checkpoint 链仍在 |

不做什么（Out of Scope）：运行中暂停按钮、前端改动、time-travel/rewind、子代理图 checkpointer。

## 2. 核心机制

### 2.1 挂载链路（三绑定原子改动）

```
config.checkpoint_enabled/dir ──► TaskManager.__init__
                                    │ sqlite3.connect(checkpoints.sqlite, timeout=10)
                                    ▼
                              SqliteSaver(conn)
                                    │ build_graph(runtime, mode, checkpointer=...)
                                    ▼
                        graph.invoke(state, _thread_config(task_id))
                              thread_id == task_id          (D3 约定)
```

`_thread_config` 同时注入 `recursion_limit = max_steps*8 + 20`。**为什么 ×8**：P1 主拓扑每 agent 循环消耗 7 个 superstep（planner→risk_scan→subagent_split→executor→[human_confirm]→tool→reflect），旧版 langgraph 默认 25 的上限意味着 `max_steps>6` 从未真正生效——这是挂接测试时由 GraphRecursionError 揭示的存量缺陷。子代理图独立注入 `×4+10`（subtask 拓扑 4 superstep/轮）。

### 2.2 resume 编排（与 spec D4 的实现级偏离）

spec 设想 `invoke(None, config)` 官方续跑语义。实测不可行：本项目 stop 走 finish 节点优雅退出，图已到达 END，None-resume 无事可做。实际实现：

```
resume(task_id):
  ① join 原 worker（≤5s）            # stop 发布 INTERRUPTED 后 worker 仍在收尾写最后帧
  ② _has_checkpoint 双探测           # 0.3s grace 重试一次，规避首帧未落盘窗口
  ③ 锁内 CAS claim                   # status==INTERRUPTED 才翻转 RUNNING（并发双跑拦截）
  ④ publish task_resumed + trace attach（append 同一 JSONL）
  ⑤ 后台 _resume_run:
       get_state() 取回快照
       仅重置控制位（status/stop_requested/pending_confirm/_needs_confirm/_current_tool_calls）
       plan / steps / messages / _confirmed_ids / step_index 全部存活
       invoke(restored, config) —— 同 thread_id 续写检查点历史
```

### 2.3 孤儿对账（D5）

TaskManager 构造器尾部单次执行：持久化里 status ∈ {RUNNING, PENDING} 的记录全部转 INTERRUPTED。构造时无 worker 线程，PENDING 覆盖「create_task 落盘 ↔ 线程置 RUNNING」之间的崩溃窗。逐条 try，单条失败不阻断清扫。P0 死循环修复代码零触碰。

## 3. FATAL 发现与修复：stop 在 checkpointer 下失效

实施中段的黑盒测试暴露了一个致命交互（若非本特性带出，生产一旦启用即全线故障）：

**根因**：langgraph 每个 superstep 向节点传递 state 的**副本**（serde 还原值）。旧 stop 机制通过 `stop()` 改 `_active_states[task_id]["stop_requested"]=True`——那是一个**不再被任何节点引用的孤儿字典**。结果：stop 后 human_confirm 的 wait 循环永远等不到停止信号（实测挂死 15s+ 直至脚本耗尽）；checkpoint 帧序列探针显示 13 帧全程 `stop_req=False`。

**修复**（nodes.py + task_manager.py）：

1. TaskManager 新增权威信号 `self._stop_flags[task_id]`（`stop()` 置位，`is_stop_flagged()` peek）。
2. AgentRuntime 新增 `_stopped(state)` helper：state 字段 OR manager flag；命中则把 True **写回本次 run 的副本**，下游条件边立即可见。
3. planner / risk_scan / subagent_split / executor / tool_node 五处守卫统一换用 helper。
4. 两处确认等待循环（human_confirm 节点、risk pause 计划级确认）改为轮询 flag。
5. run/_resume_run teardown pop flag；shutdown 前 join 全部 worker 再关 sqlite 连接（防 WAL 写中关闭引发 native crash——开发中真实发生过 access violation）。

红线遵守：`_needs_confirm` 重算块一字未动，仅添加警示注释。

## 4. 安全默认（确认闸门 × 续跑）

| 场景 | 行为 |
|---|---|
| 任务停在人工确认闸口被 stop | human_confirm_node 用 manager 权威 flag 判定 stop-forced，将 `pending_confirm` 标记写入 state 并随快照落盘 |
| resume 读取到该标记 / 未决 require_confirm 调用 | **同步拒绝**（409）："awaiting human confirmation"——闸门永不被静默绕过 |
| 已决确认历史（_confirmed_ids/_rejected_ids） | 随快照保留；续跑后的新高危调用因 id 新鲜必然重新触发闸门（story #13 契约验证） |
| COMPLETED / FAILED 终态 resume | 409 幂等拒绝 |

## 5. 持久化加固（review 产出）

resilience 层零改动的前提下，Persistence 补齐续跑承诺的地基：

- `_lock`：多线程 save/load/list 全部串行化（daemon 线程并发落盘原先无锁）
- `_dump` 原子写（tmp + `os.replace`）+ Windows PermissionError 短退避重试
- `_load` 失败不再 "starting fresh" 毁史：损坏文件归档为 `tasks.json.corrupt-<ts>`
- 新增 `list_all()`（对账需要全量遍历，替代魔法数字 limit）

## 6. 测试策略回顾

三层接缝按 spec 执行，双验证员交叉 review 各一轮：

1. **定点抽查**（test_checkpointer.py）：saver round-trip、thread 隔离、invoke(None) 语义钉档、空 state、AgentState serde 哨兵、**langgraph serde 对不可序列化对象静默丢弃的行为钉档**（未来字段演进哨兵）
2. **黑盒主缝**（test_resume.py + test_orphan_reconcile.py）：完整生命周期 create→stop→rebuild→resume→COMPLETED、trace append 连续性、拒绝分支、并发 resume CAS、confirm-gate 契约、孤儿对账、persistence 损坏防护
3. **live_e2e 场景 2**：真模型两步→杀停→重建→resume→完成（需 API Key 时执行）

工程边界备忘：
- conftest 顶部追加 `CHECKPOINT_ENABLED=false` setdefault（隔离段既有行未动）；checkpoint 用例经 `make_settings(checkpoint_enabled=True)` 显式 opt-in，且 make_manager 自动注册 shutdown finalizer
- 子代理（subtask 图）不接入 checkpointer——子图 invoke 不带 config 的 recursion_limit 缺口已单独修复

## 7. 配置面

```ini
checkpoint_enabled=true     # 关闭 = 回到无检查点旧行为，/resume 不可用
checkpoint_dir=             # 空 = <data_dir>/checkpoints（已被 .gitignore 覆盖）
```

## 8. 已知限制

- 子图（SubAgentExecutor）无断点能力：父任务 resume 不还原子任务进行中的执行（子任务摘要/产物仍安全折叠回父状态）
- event_bus 内存缓冲跨重启不保留（trace JSONL 已承担审计连续性职责）
- 多 manager 进程内共享同一 checkpoint 文件依赖 sqlite WAL 锁；跨进程部署建议后续切 PostgresSaver（接口已收窄，替换不动服务层）
