# PawBench 驱动的能力补足路线图

> 生成于 2026-09-13。本文是本仓库后续能力补足的执行路线：
> 以第一性原理（[`capability-first-principles.md`](capability-first-principles.md)）定需求，
> 以 PawBench 评测定验收与优先级。配套背景见
> [`agent-comparison-report.md`](agent-comparison-report.md)。

> 🔄 **v2 方向修订（2026-09-14）**：经一轮 grilling + 对 PawBench 仓库的一手接口调研
> （[`pawbench-harness-interface.md`](pawbench-harness-interface.md)），**放弃「接入 PawBench 评测机器」**
> （不写 `ContainerAgent` 适配类、不 fork/维护 patch、不建评测镜像、不搞 OpenClaw transcript），
> 改为**只借 PawBench 的题目 + 每题自带的 `grade()` 自动检查，自建一个轻量的跨-agent 对比评测**。
> 下面 §一 决策表、§四 里程碑、§五 分支拓扑、§六 风险均已按 v2 更新；§二/§三 仍作参考。

---

## 〇、方法论修正（先于一切执行决策）

**不按评测集做 agent；按第一性原理做 agent，用评测集验收和排优先级。**

- 需求来源 = 第一性原理（通用 agent 本身需要什么，见推导文档）；
- 验收标准 = 外部可判定锚点（PawBench 分数 + 三里程碑）；
- 反馈回路 = 失败切片分析 → 定向修复 → 重切片验证。

反面教训（Goodhart 定律）：为 26 道多模态题补一套视觉管线是应试；
护城河能力（checkpoint/resume 语义、确认闸门、熔断）PawBench 一分不测，
它们的验收走 444 测试 + live 双场景，不走评测分。
（护城河纪律的决策级冻结见 [ADR-0002](adr/0002-moat-discipline-no-attention-reallocation.md)）
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
| **v2 接入形态** | **只借题目 + `grade()` 自动检查，不接 PawBench harness**；自建轻量跨-agent 对比评测（详见 §四） |
| **v2 选手与对照** | 本 agent（大脑换 **deepseek-v4.1-flash**）+ pi + opencode + qoder；**尽量统一到 deepseek-v4.1-flash**（同一大脑、不同壳，比纯 harness 差距）；换不了模型的选手单列并标注「不可比」 |
| **v2 评分** | 硬分 = 借来的 `grade()`（宿主机 exec 看产物）；软分 = 脚本直调**阿里云 `qwen3.8-max-0902`**（温度0、固定 prompt）；trae 桌面版仅作人工复核。沿用 hybrid 0.75 闸门（硬分<0.75 则软分贡献置 0——[#36](https://github.com/renjianguojinqianfan/langgraph-agent/issues/36) 已最终确认，双记 soft_raw/soft_gated） |
| **v2 代码归属** | P0-C headless 入口留 **本仓库**；评测台（驱动选手+打分+报告）**单开新仓库** |
| 评测形态 | live_e2e 模式：`scripts/` 评测脚本 + `--check` 离线冒烟进 CI + 真跑靠环境变量注入 Key；过 ruff/mypy 不进 pytest |
| 验收机制 | 三里程碑制（见 §四 v2 重写） |
| 明确不做 | **接入 PawBench harness/ContainerAgent/fork/评测镜像（v2 砍）**、追官方榜单分数（v2 砍）、MSAgent-Bench（是训练集不是评测集）、AgentBench（环境成本）、多模态 26 题（二期）、B 线转型 |

---

## 二、PawBench 关键事实（规划依据）

> ⚠️ **v2 校正**：本节若干数字经一手核对**有误**——QwenPaw 实为 **75.0**（非 76.5）、Hermes 实为
> **70.4**（非 72.6）；「harness 分差 6.4」不可复现（同模型跨 harness 最大极差实为 **11.5**）；原子
> 能力实为 **7 个**（漏 `Math_Computation`/`Code_Manipulation`）。全部出入见
> [`pawbench-harness-interface.md`](pawbench-harness-interface.md) §五。v2 既不追官方榜单，这些分数
> 仅作参考、不再作验收锚点。

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
| 文件精读编辑 read(分页/行号)/edit/glob/grep | P1+P3：read 全量塞爆上下文；整写贵且易错 | tool use 题型直接依赖产物落盘 | ✅ **P0-A 已交付**（PR #18）|
| 完成验证（reflect 落地自检）| P7：模型「做完了」的声明不可信 | self-verification 是五维原子能力之一；「虚假完工」是明示掉分点 | ✅ **P0-B 已交付**（PR #26）|
| headless 执行入口 | P12：用户不在场也要能用 | 跑 150 题必须无人值守批量执行（**评测前置依赖**）| ✅ **P0-C 已交付**（PR #19）|
| **上下文注入层**（AGENTS.md 分层加载 + skills 清单渐进披露 + 环境事实）| **P13**：agent 冷启动失忆，约定只存在于文件系统里——不注入就等于不存在（**四重收敛**：Codex/Claude Code/Qoder/Hermes+dsh）| 间接但是 Skill_Use 的前置（没有「清单注入」就没有按需加载）| **P1-A′**（新增，建议先于 P1-A）|
| skills 运行时 | P4+收敛证据（Hermes/dsh 双收敛）| Skill use 是五维原子能力之一（qwen3.6-plus 实测 **0.3562**，全表最低切片之一）| P1-A（依赖 P1-A′）|
| 回滚（写前快照）| P8 补救半边（Hermes `/rollback` 收敛）| 间接（修复失败任务时缩短重跑成本）| **P1-B**（Tier 1 唯一未闭合硬缺口）|
| web fetch/extract | P5（snippets 不够回答事实题） | 开放环境题型（需 web 检索） | P1-C |
| 多模态 | 不通过第一性原理检验（A 线场景不需要） | 榜上 26 题 | **降级二期**（防应试） |

---

## 四、三里程碑展开（v2 重写：自建轻量对比评测）

> v1 的 M1「接入 PawBench harness」整段作废。v2 验收锚点从「PawBench 官方分」改为
> 「自建评测台跑通 + 可复现的跨-agent 对比 + 失败归因」——仍是外部可判定锚点，只是尺子自己造。

### M1 — 链路验证（第一个可判定验收）

- **前置**：P0-C headless 入口必须先行（本仓库 `feat/headless-runner`）——本 agent 得先能
  「无人值守跑一道题、产物落盘、退出码有意义」。P0-C 因不接 harness 而**大幅瘦身**：不需
  OpenClaw transcript / 胖镜像 / ContainerAgent，只需 CLI 单发 + 闸门旁路开关 + 模型可配 +
  自己的 trace JSONL 落盘。
- **执行序**（[#37](https://github.com/renjianguojinqianfan/langgraph-agent/issues/37)，2026-09-16
  agent-first；同日记录修正：T1.4 提前到第二位）：评测台搭建（含 M1）在**必修四项**
  （P1-A′ / T1.4 / P1-B / P1-A）全闭合后启动，
  替代「M1 基线先行」旧序；基线改由 checkout 回 #37 定案 commit `44e74df` 复跑同切片获取
  （只看聚合 Δ，逐能力拆分放弃、归因靠 M2 失败切片定性补）。
- **评测台**（新仓库）：借 PawBench 的**题目 + `grade()`**，写一个轻量 runner：解析题目 md →
  给各选手喂 prompt（统一 `-p "<prompt>" --dir <workdir> --yolo --output json` 形态）→ 收产物 →
  跑 `grade()` 硬分 → 调 qwen3.8-max-0902 软分 → 合成报告。
- **切片**：先挑 **10–20 道「纯文本+封闭+无素材+无外部依赖」的零依赖题**（只需 `pyyaml`；
  宿主要 `bash` → Windows 走 WSL/Git Bash）。
- **验收**：这批题在 **≥2 个选手**上跑通出分（**0 分也算过**——验的是链路完整：`grade()` 硬分 +
  qwen3.8-max-0902 软分 + 产物/trace 落盘可回放），评测台的 `--check` 离线冒烟进它自己的 CI。

### M2 — 扩规模 + 失败归因

- 前置：P0-C（已合入，PR #19）/ P0-A（已合入，PR #18）/ P0-B（已合入，PR #26）——三项均已到位；
  **＋ 必修四项 P1-A′ / T1.4 / P1-B / P1-A**（[#37](https://github.com/renjianguojinqianfan/langgraph-agent/issues/37)
  agent-first 序 + 同日记录修正，评测台殿后）。
- 任务：切片扩到全部「可单借」的题（~100+ 道，排除 skillsbench 重依赖题与 open/external-dep 题）；
  按 PawBench 五维/七能力标签做**失败切片分析**。
- **切片口径**（[#36](https://github.com/renjianguojinqianfan/langgraph-agent/issues/36)，2026-09-16）：
  单维切片，Top-3 候选池 = 7 能力切片（复杂度只作副视图）；Top-3 = 覆盖题数 ×（逐切片最强可比赛手 −
  本 agent）降序取前三 + 人工定性确认，n<10 切片只进附录；每题总分沿用题带 grading_weights
  （缺省 0.5/0.5），软分归一 0-1 + 一句话理由（模板 = 题面+产物+trace 摘要）；degraded 独立三态打标
  （分数照算 + 每切片 degraded 率列）；M1 即用同一切片管道出报告（标「链路验证样本、不作归因解读」）。
- **验收**：一张跨-agent 对比表（同模型 deepseek-v4.1-flash 口径）+ 失败归因报告（本 agent 掉分在哪类
  原子能力/复杂度，Top 3 缺陷维度）。

### M3 — 定向修复与复验

- 任务：由 M2 失败分析选 2–3 个缺陷维度定向修复，重跑同切片验证。
- **验收**：目标 = 基线 + Δ（Δ 由 M2 报告决定）；每个修复有前后切片对比。**不追官方榜单、不对外
  声称可比分数**（裁判是 qwen3.8-max-0902 非 opus，尺子自造，只做内部相对对比）。

---

## 五、纪律约束（长期生效）

1. **受保护文件零改动**：`resilience.py` / `registry.py` / `nodes.py` 的
   `_needs_confirm` 重算块——P0 三项均不需要动它们；真要动走 `[OVERRIDE]` 解冻窗口。
2. **每个能力先写测试再实现**（仓库 TDD 传统，AI 代写不豁免）。
3. **注意力保护**：验收测试与失败案例复盘必须亲手做——PawBench 全量轨迹回放
   是现成的复盘教材，也是「学习资产」落袋之处。警惕「读过了但没写进手感」。
4. 评测代码不进 pytest（天然 live），但 ruff/mypy 同闸。
5. **分支策略（分级）**：功能/依赖/代码改动永远在 feature 分支做、master 只接
   PR 合入（护「全绿」简历主张）；docs/收尾小修因 `ci.yml` 的 `paths-ignore:
   docs/**、*.md` 不进 CI、不影响全绿，单人自用可直接 commit+push master、免 PR
   仪式（2026-09-14 architecture.md 依赖同步实例：push 时 admin bypass 了 guard
   required check）。沙箱 git push 走 OAuth 回落（清空 `GITHUB_TOKEN` +
   per-command `credential.helper='!gh auth git-credential'`）；每段工作 =
   commit + 立即 push，本地 commit ≠ 持久化。

### 建议分支拓扑

```
docs/roadmap-pawbench     ← 本文档 + 对比报告 + 第一性原理推导 + harness 接口分析
feat/file-tools           ← P0-A read/edit/grep/glob（已合入，PR #18）
feat/headless-runner      ← P0-C headless 入口（已合入，PR #19；含 UTF-8 stdio 修复 #22）
feat/reflect-verification ← P0-B 动 nodes.py（已合入，PR #26；review 最严，单独走）
feat/context-injection    ← P1-A′ 上下文注入层（待开；单一注入点 _build_messages，零碰保护文件）
<新仓库> eval-harness      ← v2 评测台：借题目+grade、驱动选手、qwen3.8-max 打分（不在本仓库）
```

每个 PR：分阶段 commit 不 squash（对齐 PR #8 风格）；当前计划零冻结文件改动，
标题不需要 `[OVERRIDE]`——这是刻意维持的设计约束。

---

## 六、风险与开放问题（v2 更新）

- ~~PawBench harness 接口适配成本未知~~ **已关闭**：一手调研摸清后，v2 直接**不接 harness**、改只借
  题目+`grade()`，成本从「中低但有架构代价」降为「写个轻量 runner」。见
  [`pawbench-harness-interface.md`](pawbench-harness-interface.md)。
- ~~Docker 沙箱语义~~ **v2 无关**：不进 PawBench 容器；评测台在自己机器/WSL 跑，选手各自 headless。
- **model id 已核实定死**（2026-09-16，DashScope `GET /compatible-mode/v1/models` 实查，
  [#35](https://github.com/renjianguojinqianfan/langgraph-agent/issues/35) resolution）：
  选手大脑 = **`deepseek-v4.1-flash`**（原文口语「deepseek-flash」非 API 字符串）；
  裁判 = **`qwen3.8-max-0902`**（连字符，非括号写法）。
- **qoder headless 能否换成 deepseek 自带 key 待核实**：换不了则踢出「同模型对比」、单列标注不可比。
- **Windows 跑 `grade()` 需 `bash`**：走 WSL/Git Bash。
- **公开发布题目的上游许可**：PawBench NOTICE 声明 129/150 题各留原许可；内部自跑无妨，公开需逐一核实。
- **注意力分配**（纪律 3 + v2）：管道活（驱动脚本/镜像/依赖）交 AI 写、轻 review；亲手注意力押失败
  切片归因 + trace 映射。
- 「AI 写 + 深 review」执行力风险：对策见纪律 3。
