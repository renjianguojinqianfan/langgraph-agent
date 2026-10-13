---
status: accepted
---

# 裁判故障记 `grader_error`：不回环、不烧预算、不判死

> 2026-10-13 #46 第二步（grader）落地。承接 [0005](0005-terminal-status-unverified.md)（`UNVERIFIED`
> 终态）与 [0006](0006-expectations-declared-by-caller.md)（expectation 由调用方声明）；
> 判据顺序在 0006「后果」里已预告，本文记的是第二步真正新增的那一条。

## 决策

1. `rubric`（自由文本合格线）由**调用方**声明（同 0006 的两个输入面：REST 与 headless），
   由**独立裁判** `aux_llm` 判定，三值 `satisfied | needs_revision | failed`。
2. **确定性层先行**：`expectation` 有缺口时不调裁判——机械可判的失败不需要第二意见，
   也不该花那份 token。
3. `needs_revision` 注回对话续循环，烧**自己那本**预算（`verify_rubric_max_retries`；切片 A
   曾与第一步的 `verify_max_retries` 共用一本账，切片 B 分账——共用时一次机械缺口会把语义
   复核的回合提前烧光，「先确定性、后裁判」的顺序反而惩罚了 rubric）。
4. **裁判自身故障**——aux 客户端缺席 / 调用抛异常 / 回复不可解析 / rubric 本身无从评——
   记 `UNVERIFIED(reason=grader_error)`：**不回环、不消耗重试预算、不判 `FAILED`**。
5. 裁判的证据面按 [0004](0004-artifact-is-what-the-task-wrote.md) 同一条线收口：只有声明
   `registers_artifact=True` 的工具**成功**调用才算「本任务写出了它」，`ls` / `read` 的结果
   **不得**作为作者证据；且账本**带着写进去的正文**（`write` 给全文、`edit` 标注为片段），
   让内容层面的判断有可能发生。
6. 默认 `aux_llm_enabled=false` 时声明 rubric 必然落 `UNVERIFIED`——这是 fail-closed
   （0006 第 4 条），不是配置错误，所以也不额外抛 4xx。

## 为什么值得 ADR 级记录

第 4 条乍看是个可以随手改的实现细节：裁判挂了，重试一下不就完了？三个被拒方案各有理由：

- **让主模型自评 rubric**：违反 P0-B §一 的前提「验证者必须独立于声明者」。声明者自己
  判自己合格，就是 0006 当初推翻「动作类步骤」时请走的那条幻觉通道，换个名字又回来。
- **`grader_error` 判 `FAILED`**：违反 P0-B Q4「绝不因验证误判判 `FAILED`」，而且归因错了——
  裁判不可用不是这份工作的错，把它记成崩溃会让调用方去查一个不存在的产品 bug。
- **`grader_error` 回环重试**：让模型「再试一次」修不好调用方写的一份文档。回环的前提是
  「被judge 的一方还能改变证据」，裁判故障时这个前提不成立；更糟的是它会白烧掉
  `verify_max_retries` 预算，把一次配置问题放大成一次假性的重试耗尽。

三条判据都满足：**难反转**（改回自评或改回 FAILED 都会重新引入刚被请走的通道）；
**无上下文会疑惑**（为什么 `needs_revision` 回环而 `grader_error` 不回环？为什么关掉 aux 时
声明 rubric 不报错、而是安静地落 `UNVERIFIED`？）；**有真实取舍**（放弃「裁判挂了也尽量给个
结论」的可用性，换「不拿一个不可用的判定冒充结论」）。

第 5 条同样值得记：它不是理论洁癖。2026-10-13 的开工前置试测（157 条 trace 回放、28 次真实
`aux_llm` 调用）量出假阴性 **0/24 与 0/23**——判据不需要缩；但对照组 5 个合成失败样本里有
2 个被放过，而**这两个洞的性质不同**（详见 `docs/grader-fn-trial-2026-10-13.md`）：

- `r123_claims_third_file_never_written`——模型声称写了 r3 其实没写，裁判拿一次 `ls` 的间接证据
  就确认「三个文件都在」。机械断言**能**抓到这个（`A_r3.txt_contains_own_name` 失败），是裁判
  放过的。第 5 条前半堵它：账本只收 `registers_artifact` 工具的**成功**调用，失败的写、
  `read` 同路径、`ls` 列表一律不进账本。
- `deliverable_is_a_placeholder`——文件确实由一次成功写调用创建、名字也对，只是内容是占位符。
  **机械断言看不见它**（非空、名字对，断言全过）。第 5 条后半是给它的：正文随账本条目一起交给
  裁判（`write` 全文、`edit` 标注为片段），否则「路径 + 字节数」这个形状永远判不出占位符。

要说清后半的边界：它只是**把判据供给裁判**，不等于裁判一定判得动——真实裁判在实现后提示词
（带新增的账本块）上的判决质量**尚未复测**，0/24 那组数字量的是试测那份 prompt。所以这条不是
「洞已封死」，而是「证据形状从『存在』改成『写了什么』」；残留风险由人工复核出口（0005 的重开
条件）持续采样，而不是把 0% 读成安全边际。

## 后果

- `verification.reason` 的 `grader_error` 一档有了生产者（第一步只是留位、没人发它）。
- 裁判故障与验收未过在**终态上同类**（都 `UNVERIFIED`）、在**原因上可分**（`reason` 不同），
  所以下游要按两件事处理：`retry_exhausted` / `max_steps` 是「工作没达标」，
  `grader_error` 是「我们没能判」。
- 声明 rubric 却不配 aux 的调用方会看到 `UNVERIFIED`，需要 `.env.example` / README 明确
  这条耦合（`aux_llm_enabled` + `aux_llm_model` 是 grader 的前置）。
- 离线测试面复用既有的 `aux_llm_use_mock`，不新造 mock 客户端。

## 重开条件

- 人工复核出口落地时（把 `UNVERIFIED` 接受为 `COMPLETED`，0005 的重开条件）：
  `grader_error` 是否应当比 `retry_exhausted` 更容易被人工放行，要一起拍。
  → **2026-10-13 切片 B 已拍：不设按档门槛**——任何一档都能被人工接受，但接受的是哪一档记在
  `Task.review.overruled_reason` 上（拦档只会让黄灯永远亮着；可数才是试测要的持续采样）。裁定正文在
  [0005](0005-terminal-status-unverified.md) 的重开条件段，本条只留指针。
- 若将来给裁判配独立重试预算，第 4 条「不烧预算」的边界要重述——那时要区分
  「被judge 的一方重试」与「judge 自己重试」。
  → **2026-10-13 切片 B 已配**（`verify_rubric_max_retries` / `_rubric_attempts`，与第一步分账）：
  第 4 条的「不烧预算」从此指**两本账都不烧**——`grader_error` 既不推进 `_verify_attempts`
  也不推进 `_rubric_attempts`。而「被 judge 的一方重试」走的是 `_rubric_attempts` 那本，
  与 judge 自己无关：裁判调用本身仍不重试（`grader.grade_rubric` 一次调用定判决）。
- 出现「rubric 部分可评」的需求（多条合格线里只有一部分评不动）时，二值
  `grader_error / not` 不够用，要换成逐条状态。
