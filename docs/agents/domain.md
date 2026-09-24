# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root, or
- **`CONTEXT-MAP.md`** at the repo root if it exists — it points at one `CONTEXT.md` per context. Read each one relevant to the topic.
- **`docs/adr/`** — read ADRs that touch the area you're about to work in. In multi-context repos, also check `src/<context>/docs/adr/` for context-scoped decisions.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The `/domain-modeling` skill (reached via `/grill-with-docs` and `/improve-codebase-architecture`) creates them lazily when terms or decisions actually get resolved.

> **Current state (2026-09-18):** `CONTEXT.md` does not exist yet; `docs/adr/` **does** —
> seeded with `0001-in-place-migration-and-resume-positioning.md` (migrated in from
> `.qoder/specs/`) and `0002-moat-discipline-no-attention-reallocation.md`
> (wayfinder #27/#30). This repo is **single-context** — one `CONTEXT.md` + one `docs/adr/`
> at the root. No `CONTEXT-MAP.md`, no per-package context dirs.
>
> Decision / plan / spec docs always live under `docs/` — never in vendor-specific agent
> dirs (`.qoder/`, `.trae/`, `.mimosa/`), which are untracked by design. Full rule table:
> 「文档归属」at the bottom of this file. Memory-type files (`.workbuddy/memory/`,
> `learning-records/`, `MISSION.md` / `NOTES.md`) stay put — they are not migrated.

## File structure

Single-context repo (this repo's layout):

```
/
├── CONTEXT.md                         ← glossary / ubiquitous language (not yet created)
├── AGENTS.md                          ← agent operating manual (exists; short entry: positioning /
│                                        safety boundaries / task routing / verification / Agent skills)
├── docs/
│   ├── adr/                           ← decisions (0001-in-place-migration-and-resume-positioning.md,
│   │                                     0002-moat-discipline-no-attention-reallocation.md,
│   │                                     0003-subagent-circuit-breaker-per-runtime.md)
│   ├── specs/                         ← frozen spec / implementation-plan archive
│   ├── agents/                        ← this skill's output (issue-tracker / triage-labels / domain)
│   ├── prd.md · architecture.md       ← original v0/v0.1 design docs
│   ├── incremental-prd-p{0,1,2}.md    ← per-iteration PRDs
│   ├── incremental-arch-p{0,1,2,3-resume}.md
│   ├── migration-langgraph-1x.md      ← 0.2 → 1.2.x migration, measured
│   ├── capability-first-principles.md ← first-principles capability derivation
│   ├── agent-comparison-report.md     ←横向对标（Codex/Qoder/Hermes/dsh）
│   ├── architecture-audit-2026-09-22.md ← 只读架构审计（发现 → issue 映射与状态）
│   └── roadmap-pawbench.md            ← execution roadmap (v2)
├── backend/                           ← Python: api / core(agent,llm,tools,kb,mcp) / services / utils
└── frontend/                          ← React + Vite + TS: components / pages / store / hooks
```

Multi-context repo (presence of `CONTEXT-MAP.md` at the root) — **not this repo**, kept for reference:

```
/
├── CONTEXT-MAP.md
├── docs/adr/                          ← system-wide decisions
└── src/
    ├── ordering/
    │   ├── CONTEXT.md
    │   └── docs/adr/                  ← context-specific decisions
    └── billing/
        ├── CONTEXT.md
        └── docs/adr/
```

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `CONTEXT.md`. Don't drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal — either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

Until `CONTEXT.md` exists, the de-facto vocabulary lives in `backend/core/agent/state.py` /
`graph.py` (node names) and the hard rules in `AGENTS.md`「跨任务安全边界」 — use those terms (`planner` / `executor` / `tool` /
`reflect` / `risk_scan` / `subagent_split` / `human_confirm` / `finish`,
`checkpoint` / `resume` / `confirm gate` / `熔断` / `沙箱根`) rather than coining new ones.

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0007 (event-sourced orders) — but worth reopening because…_

`docs/adr/0001` (in-place migration over rewrite + resume-project positioning, decisions
D1–D9), `0002` (moat discipline) and `0003` (circuit-breaker state stays per-runtime, never shared
across parent/subtasks — so **#60's "每个子任务各记一本账" is by design, not a bug to re-file**)
are the live ADRs today. Beyond them, the equivalent
frozen decisions are the hard rules in `AGENTS.md`「跨任务安全边界」 (protected files,
`_needs_confirm` recompute block, conftest isolation, dependency matrix, quality-gate
baseline). Treat a contradiction with those the same way — surface it, don't silently override.

## 文档归属（hard rule, 2026-09-15 起）

决策 / 计划 / spec 类文档**一律落 `docs/`**；禁写各家 agent 专用目录（`.qoder/` / `.trae/` / `.mimosa/` 等，整体不入库）——换机器或换 agent 就丢，无法当交接的权威源。

| 类型 | 去处 | 命名 |
|---|---|---|
| 决策记录（grilling 定居、定位 / 选型结论）| `docs/adr/` | `NNNN-英文-kebab-case.md`（4 位递增编号）|
| spec / 实施方案 / 任务计划 | `docs/specs/` | `英文-kebab-case.md` |
| 调研 / 对标 / 路线图 / 迁移评估 | `docs/` 根 | 同上（沿用既有惯例）|
| 技能组配置 | `docs/agents/` | 由 setup 技能生成 |
| 记忆类（不迁） | 留原地：`.workbuddy/memory/`、teach 的 `MISSION.md`/`NOTES.md`/`RESOURCES.md`/`learning-records/`/`lessons/` | 各家 agent 自己消费 |

文件名用英文 kebab-case，内容中文照旧；迁移用 `git mv` 保历史（`git log --follow` 可追），并顺手改掉旧路径引用。

（本表此前在 `AGENTS.md`；下沉到这里后，`AGENTS.md`「任务路由」的开 issue / 文档归属行指向本文。）
