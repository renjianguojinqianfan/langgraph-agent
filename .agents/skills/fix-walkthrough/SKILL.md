---
name: fix-walkthrough
description: 把 issue 修复 / PR 合并前后的代码行为变化做成两份可直接打开的自包含 HTML（before / after + 并排对比），默认不要求用户启动服务。优先用真实函数、FastAPI TestClient 或截图采集状态，再重绘成可点击的步骤页；当我说「讲讲这个修复」「前后行为有什么变化」「做个 before/after 对比」「体验一下这个 PR」「这个 issue 修了什么」时使用。
argument-hint: "<issue/PR 编号或 commit 范围>"
---

# Fix Walkthrough

## 这个 skill 产出什么

一份离线的「行为对比卡」，不是截图堆，也不是手写模拟：

- `out/before.html`、`out/after.html`：两份自包含 HTML，双击即开，默认停在第一个有差异的步骤。
- `out/compare.html`：同一份数据，默认并排显示前后。
- `out/demo.json`：合并后的原始数据，便于复查或二次渲染。
- 每个场景有分步播放、状态面板、判据表、代码 diff 和原始证据。

**看的时候不需要启动任何服务。** 生成时也优先不启服务：纯逻辑/节点行为直接调用真实函数，端到端流程用 FastAPI `TestClient` + `MockLLMClient` 在进程内跑；只有 UI 视觉/交互面才需要后台跑一次浏览器截图，而且由 agent 跑，用户只看 HTML。

## 不可协商的事

1. **事实来源优先真实执行**。`real-function` > `testclient` > `screenshot` > `simulated`；`simulated` 必须在 HTML 顶部明确标注「手写模拟，不是真实代码执行」。
2. **先自己跑一遍再交付**。before 侧必须真的复现 bug，after 侧必须真的消失；两边看起来一样就是场景设计错了。
3. **不要为了对比去改生产代码**。用 `.qoder/tmp/fix-walkthrough/<slug>/` 下的临时文件和 git worktree；不要提交演示代码。
4. **不要在 HTML、命令或 meta 里放 Key、完整对话或本机敏感路径**。
5. **不要用 show-me 式手写状态冒充真实行为**。show-me 是渲染风格；这里的值来自真实执行。做不到真实执行时，降级并标注，不要假装。

## 工作流

### 1. 解析目标

```bash
gh issue view <N> --json number,title,body,labels,state
gh pr view <N> --json number,title,body,state,baseRefName,headRefName,headRefOid,mergeCommit,files
```

定出两个点：

- **已合并 PR**：`before = <merge_commit>^1`，`after = <merge_commit>`。
- **未合并 PR**：`before = git merge-base origin/master <head>`，`after = <head>`。
- **只有 issue**：先找关联 PR；没有就只演示当前 master 上的 bug，并明确写「尚未修复，没有 after」。

建两个 worktree，放在 `.qoder/tmp/fix-walkthrough/<slug>/`：

```bash
git worktree add --detach .qoder/tmp/fix-walkthrough/<slug>/before <before-ref>
git worktree add --detach .qoder/tmp/fix-walkthrough/<slug>/after  <after-ref>
```

解释器用仓库的 `.venv311`；不要用根目录 `.venv`。worktree 里没有 `.env`，`collect.py` 会设置临时数据目录和离线开关。

### 2. 选最小可观测状态

只选能证明这次变化的那一个面，不要试图复刻整个应用：

| 改动面 | `state.kind` | 看什么 |
|---|---|---|
| 消息/上下文/配对 | `messages` | role 顺序、assistant `tool_calls` 与 `tool` 回复是否相邻 |
| 节点/轮次/步骤状态 | `steps` | `round`、`status`（running/done/failed/skipped） |
| 工具调用记录/旧数据 | `records` | `input` 形态、`status`、`error` |
| 事件/配置/其它 | `json` | 原始结构；必要时在模板里加专用渲染 |
| UI 视觉/交互 | 截图 | 真实浏览器渲染；`source: screenshot` |

场景设计规则：

- 一个 AC 或一个独立失败模式一个场景；场景 1–4 个，每个场景 2–4 步。
- 每一步只展示一个状态快照；输入步可以两边相同，但**至少要有一个步骤产生 false→true 的判据**。
- 判据要机械可算（协议是否合法、status 是否保持、`dropped` 是否回退），不要写「看起来更合理」。
- 后端不可见面（例如 #255 的消息配对）就展示内部状态，不要硬造一个假的 UI 差异。

### 3. 写场景模块

场景模块是一个普通的 Python 文件，暴露 `build()`，返回：

```python
{
  "scenarios": [
    {
      "id": "compress-pair",
      "title": "压缩切点拆散配对",
      "steps": [
        {
          "id": "compressed",
          "label": "压缩之后",
          "action": "compress_messages(msgs, budget=1, keep_recent=3)",
          "state": {"kind": "messages", "messages": out},
          "metrics": {"dropped": meta["dropped"]},
          "checks": [
            {"label": "压缩后配对合法", "ok": not issues, "detail": "..."}
          ],
          "note": "切点回退到 assistant 之前。"
        }
      ]
    }
  ]
}
```

硬性要求：

- **同一个模块必须在 before / after 两个 worktree 都能 import 并跑通**；只用两个 ref 都存在的 API。
- `collect.py` 会把 worktree 放到 `sys.path[0]`，并设置临时 `DATA_DIR` / `TRACE_DIR` / `CHECKPOINT_DIR`、`USE_MOCK_LLM=true`、`AUTH_ENABLED=false`。场景模块可以直接 `from backend... import ...`。
- 调用会原地修改消息/状态的函数前，先 `copy.deepcopy` 一份作为输入快照。
- 纯函数直接调用；图/节点行为用 `AgentRuntime` + 最小 `SimpleNamespace` task manager（参考 `backend/tests/test_dangling_tool_calls.py` 的 `_rt`）；端到端 API/任务流程用 `TestClient` + `MockLLMClient`。
- mock LLM 的提示词只是剧本标签，不驱动行为；需要真实推理时才换真模型，并接受输出随机性。
- 不写 pytest 断言；`checks` 是给 HTML 的判据数据。

更细的配方见 [`references/observation-playbook.md`](references/observation-playbook.md)。

### 4. 写 meta.json

meta 负责「讲清楚」，场景模块负责「拿数据」：

```json
{
  "title": "#255 上下文与工具消息配对",
  "summary": "一句话说清改动后的统一不变量。",
  "issue": {"number": 255, "url": "..."},
  "pr": {"number": 284, "url": "..."},
  "before": {"ref": "71a46af", "commit": "71a46af（PR base）"},
  "after": {"ref": "7beca14", "commit": "7beca14（merge commit）"},
  "source": "real-function",
  "scenarios": [
    {
      "id": "compress-pair",
      "title": "压缩切点拆散配对",
      "description": "改动前保留段以孤立 tool 开头；改动后切点回退。",
      "why": "发给模型的 history 必须保持 assistant → tool 的配对。",
      "code": [
        {
          "path": "backend/core/agent/context.py",
          "before_ref": "71a46af",
          "after_ref": "7beca14",
          "before": "dropped = _droppable_count(...)",
          "after": "dropped = _pair_safe_drop(...)",
          "note": "节选；新增安全切点函数。"
        }
      ]
    }
  ]
}
```

- `source` 只能是 `real-function` / `testclient` / `screenshot` / `simulated`。
- 代码片段用「节选」时在 `note` 里写明；不要贴整份文件。
- 场景 `id`、步骤 `id` 必须与两边 JSON 一致；渲染器按 id 合并。
- 详细字段与合并规则见 [`references/data-contract.md`](references/data-contract.md)。

### 5. 采集前后状态

```bash
.venv311/Scripts/python.exe .agents/skills/fix-walkthrough/scripts/collect.py \
  --worktree .qoder/tmp/fix-walkthrough/<slug>/before \
  --scenario .qoder/tmp/fix-walkthrough/<slug>/scenario.py \
  --out .qoder/tmp/fix-walkthrough/<slug>/before.json --ref before

.venv311/Scripts/python.exe .agents/skills/fix-walkthrough/scripts/collect.py \
  --worktree .qoder/tmp/fix-walkthrough/<slug>/after \
  --scenario .qoder/tmp/fix-walkthrough/<slug>/scenario.py \
  --out .qoder/tmp/fix-walkthrough/<slug>/after.json --ref after
```

采完先自己读一遍 JSON：before 必须复现 bug，after 必须修好。若两边一样，回到第 2 步换状态或换场景。

### 6. 渲染并自检

```bash
.venv311/Scripts/python.exe .agents/skills/fix-walkthrough/scripts/render.py \
  --before .qoder/tmp/fix-walkthrough/<slug>/before.json \
  --after  .qoder/tmp/fix-walkthrough/<slug>/after.json \
  --meta   .qoder/tmp/fix-walkthrough/<slug>/meta.json \
  --out    .qoder/tmp/fix-walkthrough/<slug>/out
```

打开 `out/compare.html` 自检：

- 首屏是否停在第一个有差异的步骤（模板默认如此）。
- 前后两栏的协议/状态差异是否一眼可见。
- 判据表是否每行都有「改动前 / 改动后」两个结果。
- 代码 diff 是否可读，是否有「节选」说明。
- 窄屏（约 900px）是否堆叠而不是溢出。
- 顶部来源标注是否正确（`simulated` 必须是黄色警告）。

可用无头 Chrome 截图检查布局：

```bash
"/c/Program Files/Google/Chrome/Application/chrome.exe" --headless=new --disable-gpu \
  --hide-scrollbars --window-size=1700,1200 --virtual-time-budget=3000 \
  --screenshot=out/check.png "file:///<绝对路径>/out/compare.html"
```

### 7. 交付

- 把 `before.html`、`after.html`、`compare.html` 的绝对路径给用户，并打开 `compare.html`：

  ```bash
  cmd //c start "" "file:///<absolute-path>/out/compare.html"
  ```

- 用一分钟读法说明：先看哪个场景、点「下一步」看哪一步、判据表的哪一行是关键、代码 diff 对应哪一处。
- 明确写出：哪些是真实执行，哪些是模拟；哪些面没覆盖（例如真实模型随机性、浏览器时序）。
- 保留 `.qoder/tmp/fix-walkthrough/<slug>/` 下的 JSON 与 HTML；**清理 worktree**：

  ```bash
  git worktree remove --force .qoder/tmp/fix-walkthrough/<slug>/before
  git worktree remove --force .qoder/tmp/fix-walkthrough/<slug>/after
  git worktree prune
  ```

## 保真度阶梯

| source | 含义 | 使用条件 |
|---|---|---|
| `real-function` | 直接调用真实函数/节点 | 纯逻辑、消息处理、策略、schema |
| `testclient` | 进程内跑真实 FastAPI app + mock LLM | 任务流程、API、事件、checkpoint |
| `screenshot` | 真实浏览器渲染后截图嵌入 | UI 视觉/交互；需后台跑一次浏览器 |
| `simulated` | 手写状态或手写逻辑 | 只在前三档都做不到时；必须标注 |

## 反模式

- 为了「让对比明显」而手写一个 after 状态，却不跑真实代码。
- 只给代码 diff，没有可点击的状态变化。
- 一个场景塞 10 步、横跨多个 AC，用户看不出哪一步是关键的。
- 把两个版本都从主检出 import（等于两边跑的是同一版代码）。
- 让用户自己启动服务、开两个端口、点两个浏览器标签。
- 在 HTML 里放 Key、完整对话、本机用户名或绝对路径。

## 资源

- [`scripts/collect.py`](scripts/collect.py)：在指定 worktree 里运行场景模块，输出 JSON。
- [`scripts/render.py`](scripts/render.py)：合并前后 JSON，生成三份 HTML + `demo.json`。
- [`assets/template.html`](assets/template.html)：自包含模板（场景/分步/并排/判据/代码/证据）。
- [`references/observation-playbook.md`](references/observation-playbook.md)：本仓各观测面的采集配方。
- [`references/data-contract.md`](references/data-contract.md)：`before.json` / `after.json` / `meta.json` 的完整字段。
