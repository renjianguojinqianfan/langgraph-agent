# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

Edit the right-hand column to match whatever vocabulary you actually use.

## Repo state at setup time (2026-09-15)

Defaults kept as-is — no renames. Two of the five already existed on the tracker
with exactly the canonical strings, so nothing had to be reconciled:

| Label | Status before setup |
| --- | --- |
| `ready-for-agent` | **already existed** (`#0E8A16`, description「Spec 已拍板，可开始实施」) — already in active use |
| `wontfix` | **already existed** (GitHub default) |
| `needs-triage` | created during setup |
| `needs-info` | created during setup |
| `ready-for-human` | created during setup |

Pre-existing non-triage labels that stay in use alongside these: `bug`,
`enhancement`, `documentation`, `spec`（规格/设计文档）, `dependencies`,
`python`, `javascript`, `good first issue`, `help wanted`, `question`,
`duplicate`, `invalid`, `accessibility`.

`ready-for-agent` is the load-bearing one in this repo's workflow: an issue
carrying it means the spec is settled and an agent session may start
implementing without further clarification.

## 三类分诊（2026-09-24 起）

判据只有一条：**票面是否已完备到不需要再问人。**

| 类别 | 标签 | 判据 |
| --- | --- | --- |
| 执行类 | `ready-for-agent` | 修法唯一，不含设计选择 |
| 规划类 | 两个标签都不打 | 方向已定，但范围/清单/影响面未定 |
| 访谈类 | `needs-info` | 需人拍板，不问就不能动 |

- 规划类刻意用「无标签即信号」：既不能直接开工，也不是在等人补资料。
- 挂起票（如「只记录不实现」那类）保持原标签，**不要**误标 `needs-info`——它不缺信息，是不打算做。

### 摘标签必须留说明

从票上摘掉 `ready-for-agent` 时，必须同时留一条评论写明**补什么即可恢复**。否则后来者看到一张规格齐全却没有该标签的票，会当成漏打而直接开工。

### 快照会过期，规则不会

具体票号属于当天快照；本文的长期内容是上面那三条规则。开工前一律以
`gh issue list --state open --json number,labels` 的实时标签为准，别照抄本文提过的编号。

