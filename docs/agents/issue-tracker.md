# Issue tracker: GitHub

Issues and specs for this repo live as GitHub issues in
[`renjianguojinqianfan/langgraph-agent`](https://github.com/renjianguojinqianfan/langgraph-agent).
Use the `gh` CLI (v2.96.0+ verified installed) for all operations.

> 本仓库既有约定：开 issue 时沿用现有的 `[Feature]` / `[后续候选#N]` 标题前缀风格与
> `enhancement` / `bug` / `spec` 标签。发布与合并惯例见下文「Release conventions」。

## Release conventions (repo-side, as practiced)

These describe how changes actually ship in this solo repo. They are **conventions describing
past practice — not a standing authorization to commit or push**; whether to commit/push is
still decided by the user in the moment.

- **分级**：功能 / 依赖 / 代码改动走 feature 分支 + PR + CI 全绿才合（护「master 全绿」主张）；
  docs/ 收尾小修因 `ci.yml` 的 `paths-ignore: docs/**, *.md` 不进 CI、不影响全绿，
  可直接 commit + push master、免 PR 仪式。
- **实证优先、不盲信状态标签**：合并前本地复现 CI 的**确切命令**（前端 `npm ci` 而非
  `npm install`）；`BLOCKED` / 无 checks / CI 红 ≠ 可绕过或强合——先深挖根因，必要时弃
  自动分支手动接管（先例：PR #6→#15，dependabot rebase 漏升配套 plugin-react 致
  `npm ci` ERESOLVE，手动配套 bump 才绿）。
- **依赖类 PR**：Dependabot 自动抬版勿直接合；跨 major 升级警惕配套 peer 缺失；依赖矩阵
  与抬版纪律见 `AGENTS.md`「跨任务安全边界」。
- **合并方式**：`[OVERRIDE]` PR 用 squash（免 guard 变红）；合并后删远程分支前先
  `git ls-remote --heads origin <branch>` 核对；docs-only PR 无 checks / admin bypass
  属已知现象，不等于强合。

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..."`. Use a heredoc for multi-line bodies.
- **Read an issue**: `gh issue view <number> --comments`, filtering comments by `jq` and also fetching labels.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with appropriate `--label` and `--state` filters.
- **Comment on an issue**: `gh issue comment <number> --body "..."`
- **Apply / remove labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **Close**: `gh issue close <number> --comment "..."`

Infer the repo from `git remote -v` — `gh` does this automatically when run inside a clone.

### Windows / PowerShell notes (this repo's actual dev environment)

- The dev shell is Windows PowerShell 5.1: `&&` is **not** a statement separator — use `;`.
- Multi-line `--body` via heredoc is a bash-ism. In PowerShell prefer
  `--body-file <path>` (write the body to a temp `.md` first) or a single-quoted
  here-string `@'...'@`. Never interpolate `$` inside double quotes when the body
  holds env-var-shaped text (e.g. `$DASHSCOPE_API_KEY`) — PowerShell will expand it.
- `gh` output piped through `Select-Object`/`ForEach-Object` is fine; piping through
  `jq` requires `jq` on PATH — fall back to `--json ... --jq '...'` (gh's built-in jq).

## Pull requests as a triage surface

**PRs as a request surface: no.** _(Set to `yes` if this repo treats external PRs as feature requests; `/triage` reads this flag.)_

When set to `yes`, PRs run through the same labels and states as issues, using the `gh pr` equivalents:

- **Read a PR**: `gh pr view <number> --comments` and `gh pr diff <number>` for the diff.
- **List external PRs for triage**: `gh pr list --state open --json number,title,body,labels,author,authorAssociation,comments` then keep only `authorAssociation` of `CONTRIBUTOR`, `FIRST_TIME_CONTRIBUTOR`, or `NONE` (drop `OWNER`/`MEMBER`/`COLLABORATOR`).
- **Comment / label / close**: `gh pr comment`, `gh pr edit --add-label`/`--remove-label`, `gh pr close`.

GitHub shares one number space across issues and PRs, so a bare `#42` may be either — resolve with `gh pr view 42` and fall back to `gh issue view 42`.

## When a skill says "publish to the issue tracker"

Create a GitHub issue.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --comments`.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a single issue with **child** issues as tickets.

- **Map**: a single issue labelled `wayfinder:map`, holding the Notes / Decisions-so-far / Fog body. `gh issue create --label wayfinder:map`.
- **Child ticket**: an issue linked to the map as a GitHub sub-issue (`gh api` on the sub-issues endpoint). Where sub-issues aren't enabled, add the child to a task list in the map body and put `Part of #<map>` at the top of the child body. Labels: `wayfinder:<type>` (`research`/`prototype`/`grilling`/`task`). Once claimed, the ticket is assigned to the driving dev.
- **Blocking**: GitHub's **native issue dependencies** — the canonical, UI-visible representation. Add an edge with `gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`, where `<blocker-db-id>` is the blocker's numeric **database id** (`gh api repos/<owner>/<repo>/issues/<n> --jq .id`, _not_ the `#number` or `node_id`). GitHub reports `issue_dependencies_summary.blocked_by` (open blockers only — the live gate). Where dependencies aren't available, fall back to a `Blocked by: #<n>, #<n>` line at the top of the child body. A ticket is unblocked when every blocker is closed.
- **Frontier query**: list the map's open children (`gh issue list --state open`, scoped to the map's sub-issues / task list), drop any with an open blocker (`issue_dependencies_summary.blocked_by > 0`, or an open issue in the `Blocked by` line) or an assignee; first in map order wins.
- **Claim**: `gh issue edit <n> --add-assignee @me` — the session's first write.
- **Resolve**: `gh issue comment <n> --body "<answer>"`, then `gh issue close <n>`, then append a context pointer (gist + link) to the map's Decisions-so-far.
