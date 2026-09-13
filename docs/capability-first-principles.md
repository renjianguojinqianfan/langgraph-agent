# 通用 Agent 必需能力：第一性原理推导

> 生成于 2026-09-13。本文回答一个问题：**一个通用 agent 到底必需哪些能力？**
> 结论不从竞品功能清单出发（那是「人有我也要有」的陷阱），而从问题的物理约束推导；
> Hermes 与 DeepSeek Harness 作为**存在性证明**参与验证——两个独立团队各自演化出
> 同一能力（收敛演化），是该能力「必需」的最强证据；二者分歧之处是定位选择而非必需项。
> 与 [`agent-comparison-report.md`](agent-comparison-report.md)（横向对比）和
> [`roadmap-pawbench.md`](roadmap-pawbench.md)（落地路线）互为配套。

---

## 一、方法论

模型是大脑，其余一切都是 harness。大脑的物理局限决定了 harness 必须补什么：

1. **物理约束 → 能力**：每条能力必须追溯到一条「没有它，agent 在物理上不成立」的约束；
2. **双收敛验证**：Hermes × dsh（两个开源、定位不同的标杆）都有的能力 → 必需；
   只有一方有的 → 定位选择，进 Tier 2/3 待定；
3. **本仓库代码实证**：缺口判定基于源码，不基于 README。

**检验一个能力该不该做的标准**：没有它，通用 agent 在真实任务里会不会残废——
而不是「评测集测不测它」（该原则见 roadmap 文档 §方法论修正）。

---

## 二、推导：Agent 问题的 12 条物理约束

| # | 物理约束 | 推导出的能力 |
|---|---|---|
| P1 | 上下文窗口有限 | 上下文管理（压缩 / 驱逐） |
| P2 | 模型只能输出 token，无副作用能力 | 工具层（token → 动作） |
| P3 | 持久状态活在文件系统里 | 文件操作是一等公民 |
| P4 | 任何固定工具集都覆盖不了「通用」 | 代码执行（通用逃逸舱）+ 扩展机制 |
| P5 | 知识有截止日期且会幻觉 | 外部信息获取 |
| P6 | 单轮推理不可靠，任务多步 | 循环 + 预算 + 错误处理 |
| P7 | **模型「我做完了」的声明本身不可信** | 完成验证（self-verification） |
| P8 | 副作用不可逆 | 预防（确认 / 沙箱）**+ 补救（回滚）** |
| P9 | 进程会崩、会话会断 | 持久化与恢复 |
| P10 | 黑盒不可调试则不可改进 | 可观测 / 审计 |
| P11 | 任务复杂度会超过单上下文 | 委派 / 子 agent（发生时必需） |
| P12 | 「通用」= 用户不在场也要能用 | headless 执行 + 调度 |

**分层**：

- **Tier 1**（P1–P10）：任何 agent 缺了不成立；
- **Tier 2**（P5 / P12）：「通用」定位才必需，任务执行器定位可选；
- **Tier 3**（P11 等）：规模触发，发生时才必需。

---

## 三、收敛验证：Hermes × DeepSeek Harness 双收敛检验

| 推导能力 | Hermes | DeepSeek Harness | 收敛判定 |
|---|---|---|---|
| 文件操作 | 文件编辑工具集 | ls / read / write / **edit** / glob / grep 六件套 | ✓ 双收敛 |
| 代码执行 | execute_code（沙箱 RPC） | Code mode（模型写 TS 编排工具）+ shell | ✓ 双收敛 |
| 沙箱 + 审批 | 容器加固 + 命令审批 | permission policy + Web UI 审批 | ✓ 双收敛 |
| 持久化恢复 | checkpoints + `/rollback` | append-only 日志 + resume / fork / replay | ✓ 双收敛 |
| 回滚 | 工作目录快照回滚 | fork（会话级） | ✓ 双收敛 |
| 扩展机制 | MCP + plugins | 一切皆插件（Cordis） | ✓ 双收敛 |
| skills | 自动创建技能（agentskills.io） | skills 插件 | ✓ 双收敛 |
| 子 agent | delegate_task（隔离上下文 / 终端） | task 工具 | ✓ 双收敛 |
| 调度 | cron + 多平台投递 | scheduling 插件 | ✓ 双收敛 |
| 上下文管理 | 记忆 + LLM 摘要 | tool-result eviction（大结果落盘换引用） | ✓ 双收敛 |
| web 获取 | 浏览器自动化 + 搜索 | web search + file | ✓ 双收敛 |
| 完成验证 | （隐式） | （隐式） | ◐ 外部佐证（PawBench 列为原子能力） |

**分歧项全部落在 Tier 2/3**：Hermes 押多平台消息网关 / 语音 / 训练数据导出
（个人助理 + 训练平台定位），dsh 押插件内核 / 四运行模式（harness 开发者定位）。
分歧恰好验证分层正确：**收敛的才是必需的，分歧的是定位。**

---

## 四、本仓库对照（代码实证）

| 能力 | 现状 | 判定 |
|---|---|---|
| T1.1 文件操作 | `file_io`：read 全量无分页 / write 整文件覆盖 / list 单层；无 edit / grep / glob | **硬缺口**——read 全量直接撞 P1（大文件塞爆上下文） |
| T1.2 代码执行 | `code_exec` subprocess + 超时 + 确认 | ✓ 有（隔离弱） |
| T1.3 循环控制 | max_steps 正确派生；熔断 + 退避**反超两个标杆**；但 `reflect` 是空壳（只做路由），finish 信任模型的 final_answer 声明 | **半缺口：完成验证缺失**（P7） |
| T1.4 上下文管理 | compress truncate / summarize | ✓ 基础有（无 eviction） |
| T1.5 安全 | EHRB + 确认闸门 + git 黑名单 + 路径白名单（**预防面反超**） | **半缺口：无回滚**（P8 的补救半边） |
| T1.6 持久化恢复 | checkpoint 格式守卫 + resume 拒绝语义 + 孤儿对账 | ✓✓ **三者中最深** |
| T1.7 可观测 | SSE 21 事件 + JSONL trace | ✓ 有 |
| T1.8 扩展 | plugins + MCP + OpenAPI，全配置化不碰内核 | ✓ 有 |
| T2.1 web | search 仅返回 snippets；fetch 靠 `http_request` 原始响应；无 extract / 浏览器 | 半缺口 |
| T2.2 skills | 仅 KB 检索，无运行时加载 | 缺口 |
| T2.3 记忆 | 关键词 KB，embeddings 关闭 | 半缺口 |
| T2.4 headless / 调度 | 仅 Web UI，无 cron、无 headless | **缺口——双重正当性**：第一性原理（P12）+ 评测前置依赖（PawBench 跑批必须无人值守） |
| T3.1 子 agent | 场景触发式 dispatch（非通用 delegate_task） | 有但弱 |
| T3.2 分级供给（模型分级） | 无 | 缺口（规模后修） |
| T3.3 多模态 | LLM 层无 image content | 缺口（维持二期） |

---

## 五、结论

1. **Tier 1 硬缺口只有 2.5 个**：文件精读编辑（T1.1）、完成验证（T1.3 后半）、
   回滚（T1.5 后半）。其余必需项本仓库都有，其中两项
   （持久化恢复、韧性）深度**超过**两个开源标杆。
2. **Tier 2 是定位题**：已选定 A 线（通用任务 agent）→ T2.1 fetch、T2.2 skills、
   T2.4 headless 进必修。
3. **交叉验证成立**：第一性原理推出的必修清单 ≈ PawBench 原子能力标签反推的
   P0 清单（文件精读编辑 / skills / self-verification）。两条独立路径指向同一处
   ——这是「这些能力真的必需」的证据，而非巧合。第一性原理额外推出两项此前
   漏掉的：**回滚**（Hermes `/rollback` 收敛证据）与 **headless 批量执行**。
4. **护城河纪律**：T1.6 / T1.5 预防面 / T1.3 韧性是反超标杆的部分，
   任何后续规划不得为凑功能挪用这里的注意力——它们的验收走
   365 测试 + live 双场景，**不走评测分**。

---

## 六、必修清单（第一性原理序，非优先级序）

```
P0-A 文件精读编辑：read(分页/行号) + edit(str_replace) + glob + grep + list(递归)
P0-B 完成验证：reflect 落地真实自检（产物存在性/断言），不达标继续循环
P0-C headless 执行入口：CLI/脚本方式批量提交任务（评测前置依赖）
P1-A skills 运行时：SKILL.md 按需加载进上下文
P1-B 回滚：写操作前工作区快照（复用 git 或文件副本，接确认闸口同一路径）
P1-C web fetch/extract：搜索之外补页面获取
```

每项的第一性原理依据与验收路径见上表；落地排期与里程碑见
[`roadmap-pawbench.md`](roadmap-pawbench.md)。
