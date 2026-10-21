# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`GLOSSARY.md`** at the repo root, or
- **`GLOSSARY-MAP.md`** at the repo root if it exists — it points at one `GLOSSARY.md` per context. Read each one relevant to the topic.
- **`docs/adr/`** — read ADRs that touch the area you're about to work in. In multi-context repos, also check `src/<context>/docs/adr/` for context-scoped decisions.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The `/domain-modeling` skill (reached via `/grilling` and `/improve-codebase-architecture`) creates them lazily when terms or decisions actually get resolved.

> **Current state (2026-10-20):** root `GLOSSARY.md` exists as a pointer entry (vocabulary
> authority stays in `backend/core/agent/state.py`; follows upstream's `GLOSSARY.md` convention
> since #876); `docs/adr/` holds `0001`–`0010` — `0001`, `0002`, `0008`, `0009` and `0010` are live,
> `0005`–`0007` retired with `0008` (single loop + plan/build two modes), `0003` retired with
> slice C2, and `0004` retired with C5 (the workspace file list replaced the artifact ledger); `0010` supplements the
> daily form (launch dir = workspace owned by the task, one shared server, model profiles). This repo is **single-context** —
> one `GLOSSARY.md` + one `docs/adr/` at the root. No `GLOSSARY-MAP.md`, no per-package context dirs.
>
> Decision / plan / spec docs always live under `docs/` — never in vendor-specific agent
> dirs (`.qoder/`, `.trae/`, `.mimosa/`), which are untracked by design. Full rule table:
> 「文档归属」at the bottom of this file. Memory-type files (`.workbuddy/memory/`,
> `learning-records/`, `MISSION.md` / `NOTES.md`) stay put — they are not migrated.

## File structure

Single-context repo (this repo's layout):

```
/
├── GLOSSARY.md                      ← glossary / ubiquitous language (pointer entry; authority in state.py)
├── AGENTS.md                          ← agent operating manual (exists; short entry: positioning /
│                                        safety boundaries / task routing / verification / Agent skills)
├── docs/
│   ├── adr/                           ← decisions (0001 / 0002 live; 0003 retired with C2;
│   │                                     0004 retired with C5; 0005–0007 retired; 0008 single loop +
│   │                                     plan/build two modes; 0009 minimal-agent chapter; 0010 daily form)
│   ├── specs/                         ← frozen spec / implementation-plan archive
│   │                                     「冻结」只覆盖**已交付增量**的记录——不改写，现状写进权威段
│   │                                     （README 能力 / architecture）；**未开工增量**的 spec 是活的设计
│   │                                     权威，可以也应该改（如前置依赖清掉了就更新）
│   ├── agents/                        ← this skill's output (issue-tracker / triage-labels / domain)
│   ├── archive/                       ← 过时 / 点-时文档（git mv 保留历史；不作现役权威）
│   ├── assets/                        ← README 截图等文档资产
│   ├── architecture.md                ← v0.1 历史档；§3.3 REST / §3.4 SSE / §5 共享约定现役
│   ├── manual-verification.md         ← 9 个真模型网页验收场景（发布前跑）
│   ├── capability-registry-rescope.md ← 本章能力包线与切片决策日志（G-1…）
│   ├── migration-langgraph-1x.md      ← 0.2 → 1.2.x migration, measured
│   ├── incremental-arch-p3-resume.md  ← stop / resume / durability 的现行说明
│   └── security-audit-2026-10-19-bash.md ← bash 工具点-时审计与当前形态附记
├── backend/                           ← Python: api / core(agent,llm,tools,mcp) / services / utils
└── frontend/                          ← React + Vite + TS: components / pages / store / hooks
```

> 2026-10-20 D1（#174）：v0 / 迭代期文档、PawBench / benchmark、调研快照与旧图表已 `git mv` 到
> `docs/archive/`；根目录 `OVERVIEW.md` 也移入其中。ADR 不移动（退役的原地加标记），`docs/specs/` 保持冻结归档。

Multi-context repo (presence of `GLOSSARY-MAP.md` at the root) — **not this repo**, kept for reference:

```
/
├── GLOSSARY-MAP.md
├── docs/adr/                          ← system-wide decisions
└── src/
    ├── ordering/
    │   ├── GLOSSARY.md
    │   └── docs/adr/                  ← context-specific decisions
    └── billing/
        ├── GLOSSARY.md
        └── docs/adr/
```

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `GLOSSARY.md`. Don't drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal — either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

The vocabulary's authority lives in `backend/core/agent/state.py` / `graph.py` (node names) and
the hard rules in `AGENTS.md`「跨任务安全边界」; the root `GLOSSARY.md` is the pointer entry to both —
read it first, then follow its pointers rather than coining new terms.

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0007 (event-sourced orders) — but worth reopening because…_

`docs/adr/0001` (in-place migration over rewrite + resume-project positioning, decisions
D1–D9), `0002` (moat discipline — **部分退役**：「受保护文件零改动」随 `0008` 解除，
见 0008「三笔账」3),
`0008` (single loop + plan/build two modes — **0005–0007 已随之退役；
0003 随 C2 删除熔断后退役**),
`0009` (minimal-agent chapter: pi-style tool face, plan file, one-command start)
and `0010` (daily form: launch dir = workspace owned by the task, one shared server, model profiles)
are the live ADRs today. Beyond them, the equivalent
frozen decisions are the hard rules in `AGENTS.md`「跨任务安全边界」 (conftest isolation,
dependency matrix, quality-gate baseline). Treat a contradiction with those
the same way — surface it, don't silently override.

## 文档归属（hard rule, 2026-09-15 起）

决策 / 计划 / spec 类文档**一律落 `docs/`**；禁写各家 agent 专用目录（`.qoder/` / `.trae/` / `.mimosa/` 等，整体不入库）——换机器或换 agent 就丢，无法当交接的权威源。

| 类型 | 去处 | 命名 |
|---|---|---|
| 决策记录（grilling 定居、定位 / 选型结论）| `docs/adr/` | `NNNN-英文-kebab-case.md`（4 位递增编号）|
| spec / 实施方案 / 任务计划 | `docs/specs/` | `英文-kebab-case.md` |
| 调研 / 对标 / 路线图 / 迁移评估 | `docs/` 根（过时后移入 `docs/archive/`）| 同上（沿用既有惯例）|
| 过时 / 点-时文档 | `docs/archive/` | 原文件名（`git mv` 保历史；不作现役权威）|
| 技能组配置 | `docs/agents/` | 由 setup 技能生成 |
| 记忆类（不迁） | 留原地：`.workbuddy/memory/`、teach 的 `MISSION.md`/`NOTES.md`/`RESOURCES.md`/`learning-records/`/`lessons/` | 各家 agent 自己消费 |

文件名用英文 kebab-case，内容中文照旧；迁移用 `git mv` 保历史（`git log --follow` 可追），并顺手改掉旧路径引用。

（本表此前在 `AGENTS.md`；下沉到这里后，`AGENTS.md`「任务路由」的开 issue / 文档归属行指向本文。）
