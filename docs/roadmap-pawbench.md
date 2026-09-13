# PawBench 驱动的能力补足路线图

> 生成于 2026-09-13。本文是本仓库后续能力补足的执行路线：
> 以第一性原理（[`capability-first-principles.md`](capability-first-principles.md)）定需求，
> 以 PawBench 评测定验收与优先级。配套背景见
> [`agent-comparison-report.md`](agent-comparison-report.md)。

---

## 〇、方法论修正（先于一切执行决策）

**不按评测集做 agent；按第一性原理做 agent，用评测集验收和排优先级。**

- 需求来源 = 第一性原理（通用 agent 本身需要什么，见推导文档）；
- 验收标准 = 外部可判定锚点（PawBench 分数 + 三里程碑）；
- 反馈回路 = 失败切片分析 → 定向修复 → 重切片验证。

反面教训（Goodhart 定律）：为 26 道多模态题补一套视觉管线是应试；
护城河能力（checkpoint/resume 语义、确认闸门、熔断）PawBench 一分不测，
它们的验收走 365 测试 + live 双场景，不走评测分。
瀑布式「先做好再评测」同样不成立——没有外部锚点的「好」会无限漂移。

---

## 一、决策记录（三轮 grilling 收敛，不再重议）

| 决策项 | 结论 |
|---|---|
| 动机 | 学习资产 + 简历项目，**能力补足为主**（评测背景为副产品） |
| 路线 | A 线：通用任务 agent + 运行时深度，不转编码 agent |
| 约束认知 | 时间无限（AI 代写），**注意力有限**——真约束 |
| 执行方式 | AI 写 + 深 review（逐 PR 逐行读，验收测试与失败复盘亲手做） |
| 首个基准 | **PawBench**（GAIA 留二期） |
| 评测形态 | live_e2e 模式：`scripts/` 评测脚本 + `--check` 离线冒烟进 CI + 真跑靠环境变量注入 Key；过 ruff/mypy 不进 pytest |
| 验收机制 | 三里程碑制（M1 链路 → M2 基线 → M3 目标分） |
| 明确不做 | MSAgent-Bench（是训练集不是评测集）、AgentBench（环境成本）、多模态 26 题（二期）、B 线转型 |

---

## 二、PawBench 关键事实（规划依据）

- 仓库：<https://github.com/agentscope-ai/PawBench>（开源，榜单公开提交）
- **150 任务 = Text 124 + Multimodal 26**，聚合自 6 个评测集；
  五维标签：应用场景 / 原子能力（tool use、Skill use、planning、logical
  reasoning、self-verification）/ 复杂度 L1–L3 / 输入模态 / 运行环境
  （离线沙箱 vs 需 web 检索的开放环境）
- **评分 = 规则断言（文件落盘 / diff / exit code）+ LLM-as-judge**，
  产物级硬校验防「虚假完工」
- 全部任务在 Docker 沙箱执行，轨迹 / grader 产物 / 环境快照完整保留可回放
- **参考锚点**（同技术栈模型）：qwen3.6-plus + QwenPaw = **76.5**，
  + Hermes = **72.6**；harness 分差最高 6.4 分，堪比一次模型大版本升级
- 官方三个 harness：Hermes / OpenClaw / QwenPaw——对手参照系就在榜上

---

## 三、能力补足清单（第一性原理定需求，PawBench 佐证优先级）

每项的完整推导见 [`capability-first-principles.md`](capability-first-principles.md) §六。

| 能力 | 第一性原理依据 | PawBench 佐证 | 优先级 |
|---|---|---|---|
| 文件精读编辑 read(分页/行号)/edit/glob/grep | P1+P3：read 全量塞爆上下文；整写贵且易错 | tool use 题型直接依赖产物落盘 | **P0-A** |
| 完成验证（reflect 落地自检） | P7：模型「做完了」的声明不可信 | self-verification 是五维原子能力之一；「虚假完工」是明示掉分点 | **P0-B** |
| headless 执行入口 | P12：用户不在场也要能用 | 跑 150 题必须无人值守批量执行（**评测前置依赖**） | **P0-C** |
| skills 运行时 | P4+收敛证据（Hermes/dsh 双收敛） | Skill use 是五维原子能力之一 | P1-A |
| 回滚（写前快照） | P8 补救半边（Hermes `/rollback` 收敛） | 间接（修复失败任务时缩短重跑成本） | P1-B |
| web fetch/extract | P5（snippets 不够回答事实题） | 开放环境题型（需 web 检索） | P1-C |
| 多模态 | 不通过第一性原理检验（A 线场景不需要） | 榜上 26 题 | **降级二期**（防应试） |

---

## 四、三里程碑展开

### M1 — 链路验证（第一个可判定验收）

- 任务：精读 PawBench 的 harness 接入接口（**M1 第一件事**，产出接口分析）；
  写接入适配层 + `scripts/pawbench_run.py`（live_e2e 模式：`--check` 离线冒烟，
  真跑环境变量注入 Key）
- 前置：P0-C headless 入口必须先行（无它跑不了批）
- **验收**：Text 切片 10 题跑完出分（**0 分也算过**——验收的是评分链路完整走通：
  规则断言 + LLM-as-judge + 轨迹落盘可回放），`--check` 冒烟进 CI

### M2 — 全量基线

- 前置：P0-A / P0-B 完成
- 任务：Text 124 题全量跑分 + 按五维切片的失败分析报告
- **验收**：基线分 + 失败归因报告（哪类原子能力 / 复杂度掉分，Top 3 缺陷维度）
- 参照：qwen3.6-plus 底座，Hermes 档 72.6 为心理锚点

### M3 — 目标分与提升

- 任务：由 M2 失败分析选定 2–3 个缺陷维度定向修复，重切片跑分验证；
  可选提交官方榜单
- **验收**：目标分 = 基线 + Δ（Δ 由 M2 报告决定，不拍脑袋）；
  每个修复有前后切片对比

---

## 五、纪律约束（长期生效）

1. **受保护文件零改动**：`resilience.py` / `registry.py` / `nodes.py` 的
   `_needs_confirm` 重算块——P0 三项均不需要动它们；真要动走 `[OVERRIDE]` 解冻窗口。
2. **每个能力先写测试再实现**（仓库 TDD 传统，AI 代写不豁免）。
3. **注意力保护**：验收测试与失败案例复盘必须亲手做——PawBench 全量轨迹回放
   是现成的复盘教材，也是「学习资产」落袋之处。警惕「读过了但没写进手感」。
4. 评测代码不进 pytest（天然 live），但 ruff/mypy 同闸。
5. **分支策略**：永远在 feature 分支做，master 只接 PR 合入（master 的
   「全绿」简历主张不可拆）。沙箱环境 git push 无凭据，落盘走 GitHub API
   通道；每段工作 = commit + 立即 push，本地 commit ≠ 持久化。

### 建议分支拓扑

```
docs/roadmap-pawbench     ← 本文档 + 对比报告 + 第一性原理推导
feat/file-tools           ← P0-A read/edit/grep/glob（最独立，先做）
feat/headless-runner      ← P0-C headless 入口（M1 前置依赖）
feat/pawbench-m1          ← 评测接入 + scripts/pawbench_run.py
feat/reflect-verification ← P0-B 动 nodes.py（review 最严，单独走）
```

每个 PR：分阶段 commit 不 squash（对齐 PR #8 风格）；当前计划零冻结文件改动，
标题不需要 `[OVERRIDE]`——这是刻意维持的设计约束。

---

## 六、风险与开放问题

- **PawBench harness 接口适配成本未知**——M1 存在的全部理由；
  摸清之前不排任何后续细节。
- Docker 沙箱语义待确认：整个 harness 进容器，还是仅 workspace 隔离
  （影响部署形态与 checkpoint 路径）。
- LLM-as-judge 需要的评分模型与 Key 额度（qwen3.6-plus 双用途可行性）。
- 「AI 写 + 深 review」执行力风险：对策见纪律 3。
