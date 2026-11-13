# 数据契约

三份 JSON 喂给 `render.py`：

```text
<slug>/
├── before.json      # collect.py 产出（before worktree）
├── after.json       # collect.py 产出（after worktree）
├── meta.json        # 人写：标题、refs、场景文案、代码片段
├── scenario.py      # 人写：两边都能跑的采集模块
└── out/
    ├── before.html
    ├── after.html
    ├── compare.html
    ├── demo.json
    └── *.png        # 可选，kind=image 的截图
```

## before.json / after.json

`collect.py` 在场景模块返回值外包一层元信息：

```json
{
  "ref": "before",
  "worktree": "<repo>/.qoder/tmp/fix-walkthrough/255/before",
  "python": "3.14.x",
  "generated_at": "2026-11-04T00:10:50+00:00",
  "scenarios": [
    {
      "id": "compress-pair",
      "title": "压缩切点拆散配对",
      "steps": [
        {
          "id": "compressed",
          "label": "压缩之后",
          "action": "compress_messages(msgs, budget=1, keep_recent=3)",
          "state": { "kind": "messages", "messages": [] },
          "metrics": { "dropped": 1 },
          "checks": [
            { "label": "压缩后配对合法", "ok": true, "detail": "所有调用都有紧随回复" }
          ],
          "note": "切点回退到 assistant 之前。"
        }
      ]
    }
  ]
}
```

约束（`collect.py` 会机械校验一部分）：

- `scenarios` 非空数组；每个场景有非空 `id`。
- 每个场景 `steps` 非空数组；每个步骤有非空 `id` 和 `state`。
- 同一场景在 before / after 两边的 `id` 必须一致；步骤同理。缺失的一侧会在渲染时显示「这一侧没有数据」并打印 warning。
- `state` 必须 JSON 可序列化。

## state.kind

模板内置五种渲染器：

### `messages`

```json
{
  "kind": "messages",
  "messages": [
    { "role": "user", "content": "写文件" },
    { "role": "assistant", "content": "", "tool_calls": [
      { "id": "c1", "type": "function", "function": { "name": "write", "arguments": "{}" } }
    ]},
    { "role": "tool", "tool_call_id": "c1", "content": "{\"ok\": false}" }
  ]
}
```

渲染器会自动做协议检查：每个 assistant `tool_calls` 后必须紧跟按顺序的 tool 回复；孤立 tool 回复会被标红。这个检查独立于 `checks`，所以即使你忘了写判据，页面也会显示「配对断裂」。

### `steps`

```json
{
  "kind": "steps",
  "steps": [
    { "index": 1, "round": 1, "status": "done", "thought": "", "tool_calls": [] },
    { "index": 2, "round": 2, "status": "running", "tool_calls": [] }
  ]
}
```

`status` 支持 `running` / `done` / `failed` / `skipped`，其余值按灰色显示。

### `records`

```json
{
  "kind": "records",
  "records": [
    { "id": "c1", "tool_name": "write", "status": "pending",
      "input": { "value": ["a", "b"] }, "error": "" }
  ]
}
```

用于 `ToolCallRecord`、持久化记录、旧数据兼容；`input` 可以是任意 JSON。

### `json`

```json
{ "kind": "json", "value": { "any": "thing" } }
```

兜底；渲染为格式化 JSON。

### `image`

```json
{
  "kind": "image",
  "images": [
    { "src": "before.png", "alt": "改动前", "caption": "空列表" },
    { "src": "after.png", "alt": "改动后", "caption": "失败原因 + 重试" }
  ]
}
```

`src` 可以是 `out/` 下的相对路径，也可以是 `data:image/png;base64,...`。只用于 UI 视觉面；`meta.source` 写 `screenshot`。

新增渲染器：在 `assets/template.html` 的 `renderState()` 里加一个 `case`，并补对应 CSS；同时在本文档加一节。

## metrics / checks / note

- `metrics`：`{ "dropped": 1 }` 形式，渲染成面板顶部的只读 chips；用来放数字证据（`dropped`、`band_evicted`、`protocol_issues`、`round_no`、`messages` 等）。
- `checks`：判据数组。每条 `{ "label": string, "ok": boolean, "detail"?: string }`。渲染器按 `label` 合并两边：同一个 label 会出现在同一行，分别显示「改动前 / 改动后」。**每个场景至少要有一条在 before=false、after=true 的判据**；否则这个场景没有证明任何修复。
- `note`：这一步的一句话解释；会显示在状态面板上方。不要写成长段落。

## meta.json

```json
{
  "title": "#255 上下文与工具消息配对",
  "summary": "一句话说清改动后的统一不变量。",
  "issue": { "number": 255, "url": "https://github.com/.../issues/255" },
  "pr": { "number": 284, "url": "https://github.com/.../pull/284" },
  "before": { "ref": "71a46af", "commit": "71a46af（PR base）" },
  "after": { "ref": "7beca14", "commit": "7beca14（merge commit）" },
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
          "before": "dropped = _droppable_count(messages, keep_recent)",
          "after": "dropped = _pair_safe_drop(messages, _droppable_count(messages, keep_recent))",
          "note": "节选；新增安全切点函数。"
        }
      ]
    }
  ]
}
```

- `source`：`real-function` / `testclient` / `screenshot` / `simulated`。渲染器据此显示顶部徽章；`simulated` 会显示黄色警告。
- `before` / `after` 的 `ref` 会显示在两侧面板标题；`commit` 可放更长的描述。
- `scenarios[*].id` 必须与 before/after 的 id 对应；meta 里多出的场景会被渲染，但两边都没有数据。
- `code`：每个条目是一处「为什么变」；`before` / `after` 是**代码片段**（不是完整 diff），`render.py` 用 `difflib` 生成 unified diff。片段是节选时在 `note` 里写明。
- 不要放 Key、完整对话、本机用户名/绝对路径；`meta.command` 由渲染器自动写入 `sys.argv`，路径只写到 `.qoder/tmp/...` 这一级。

## 合并与默认视图

`render.py` 的合并规则：

1. 场景顺序：meta 的顺序 → before 多出的 → after 多出的。
2. 步骤顺序：before 的顺序 → after 多出的；按 `id` 合并。
3. 每步的 `label` / `action` 取有值的一侧；`before` / `after` 各自的 `state` / `metrics` / `checks` / `note` 独立保留。
4. 同一步两边 `state` 不同时，两侧面板显示「有差异」。
5. 页面默认停在**第一个有差异的步骤**；URL hash 可指定：`#s=1&step=2&view=compare`（`s` 是场景下标）。
6. 三份 HTML 共享同一份数据，只改 `data-default-view`：`before.html` / `after.html` / `compare.html`。

## 校验与错误

`collect.py` 会在写文件前校验：

- 场景模块存在且定义了 `build()`；
- 返回 `scenarios` 非空；
- 每个场景/步骤有 `id`，每个步骤有 `state`。

`render.py` 会在合并时校验：

- `scenarios` 是数组，id 非空；
- meta 的 `scenarios` 是数组，id 非空；
- 缺一侧的场景/步骤会打印 warning，不会中断渲染。

常见错误：

| 现象 | 原因 | 处理 |
|---|---|---|
| 两边状态完全一样 | 场景没有触发改动，或两边跑的是同一版代码 | 检查 `collect.py --worktree`；检查场景是否真的调用了改动点 |
| `ImportError` / 旧代码行为 | 场景模块用了 after 才有的 API | 只用两个 ref 都存在的 API；新 API 放到 checks/note 里讲 |
| `ValidationError` 没被捕获 | 旧格式场景没 try/except | 用 try/except 把异常转成 `records.error` |
| 页面打开停在没有差异的输入步 | 默认第一步没有差异 | 把有差异的步骤排前，或确认模板的「第一个有差异步骤」逻辑生效 |
