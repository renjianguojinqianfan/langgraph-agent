# grader 假阴性率试测（2026-10-13，#46 第二步开工前置）

> #46 票面把「先量假阴性率，不可接受就缩小判据、**不是**改回 `COMPLETED`」列为第二步的开工
> 前置。本文是那次试测的结论与判据记录。实现决策见
> [ADR 0007](adr/0007-grader-error-not-fatal.md)；终态口径见
> [ADR 0005](adr/0005-terminal-status-unverified.md)。

## 结论（两句话）

1. **假阴性 0%，判据不需要缩**：宽口径 `gt` 24 个真完成样本、严口径 `gt_strict` 23 个，
   被裁判拒掉的都是 **0**。所以第二步按票面原设计做 grader，不预先收窄。
2. **真风险在反方向**：裁判**偏松**。5 个合成失败样本里 2 个被放过——而这两个洞性质不同：
   一个能用「账本只认成功写调用」堵（`ls` 不算证据），另一个机械断言根本看不见（占位符内容），
   只能靠把正文交给裁判去判。第二半见 ADR 0007 第 5 条。

## 方法

- 语料：`data/traces/` 干净 157 条（回放前先把并行会话写进来的 4 条 mock trace 按内容隔离，
  否则「取最新 N 条」的探针会先撞上测试数据）。折叠成 158 行数据集。
- **ground truth 不由模型自评**：一组机械可判断言（`A_r3.txt_contains_own_name`、
  `A1_kb_retrieval_succeeded`、`Q_hello.txt_not_a_placeholder` 等）分
  `positive / negative / undecidable`。
- 裁判：`qwen3.7-flash-2026-07-15`，28 次真实调用、71,425 tokens（约 2.5k/次）。

### 探针先自证有效，再谈数字

探针不成立时后面的百分比没有意义，所以先做四层验证：

| 层 | 做法 | 结果 |
|---|---|---|
| (a) 与产品自己的确定性判决对表 | 38 条有 recorded verification 的样本 | `passed=False` 的 10 条探针全判 negative；`passed=True` 的 25 条全 positive；3 条 undecidable |
| (b) 与**回滚账本**对表 | 账本是机器写的、在 trace 之外，独立于探针输入 | 16 个有 ledger 的任务，**探针/账本不一致 0 条** |
| (c) 消融灵敏度 | 用与裁判对照组同一批消融样本，看探针能否翻标签 | 5 个里 4 个翻得动；`deliverable_is_a_placeholder` **翻不动**（断言全过） |
| (d) 严口径复核 | positive 的内容充分性 | 139 条 positive 全数通过 strict tier 复核，两档差 1 条（即占位符那条） |

(b) 是最硬的一层：它用一份**不经过 trace** 的机器记录当参照，探针与它零冲突。

### 一个方法论坑（下次做同类试测要知道）

数据集里的真实 negative **全是 `INTERRUPTED` 运行**，而这类运行压根没有 final answer——
**只有声称完成的任务才会走到裁判面前**。所以「裁判会不会放过假交付」不能用 interrupted
运行当负样本，必须靠消融构造（在 COMPLETED 的 trace 上改证据）。探针的 (0) 层输出里那句
note 就是这件事的提醒。

## 两个被放过的洞（性质不同，别混为一谈）

| 消融 | 探针 | 裁判 | 判定 |
|---|---|---|---|
| `r123_claims_third_file_never_written` | **抓得到**（`A_r3` 断言失败） | 放过（拿 `ls` 当作者证据） | 真偏松，**可堵**：ADR 0007 第 5 条——产物须追溯到一次成功写调用，`ls`/`read` 不算 |
| `deliverable_is_a_placeholder` | **抓不到**（文件非空、名字对，断言全过） | 放过 | 机械层无解。实现侧的处理是**把正文随账本条目交给裁判**（`write` 全文、`edit` 标注为片段），让内容层面的判断有可能发生——判据已供给，但真实裁判在这份实现后的提示词上**尚未复测** |

第二行是为什么不能把「假阴性 0%」读成安全边际：裁判偏松的方向恰好是机械断言也照不到的方向。

## 裁判协议形状（实现与测试都照它钉）

三值 `result` + 逐条 `criteria`（失败项带 `gap`）：

```json
{"result": "satisfied" | "needs_revision" | "failed",
 "explanation": "…",
 "criteria": [{"name": "…", "passed": true},
              {"name": "…", "passed": false, "gap": "缺什么证据"}]}
```

提示词侧的两条立场值得保留原文精神：`<transcript>` 全部内容按「不可信观察」处理、
不当指令；只有 `status=success` 的**机器产出** tool result 算确认过的证据，agent 散文里的
声称不算；**不能从 transcript 正面确认的判据一律标 failed 并写明需要什么证据**（禁止补造）。

## 局限

- positive 样本里 `smoke` 家族占 102/139，试测只抽到 28 条，家族分布偏斜；
- 合成负样本只有 5 个，所以「偏松」这个**方向**可信，**漏判率幅度**未定；
- 分析脚本（`build_dataset.py` / `validate_probe.py` / `run_grader.py`）是一次性产物，
  留在本地 `data/grader-trial/`（gitignored）**未入库**——它依赖 trace 的具体格式，入库就要
  长期跟着格式维护。本文记录的是判据与结论，复现按上表方法重做即可，不需要原脚本。
