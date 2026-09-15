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
