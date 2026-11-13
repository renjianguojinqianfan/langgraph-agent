# 观测面采集配方（langgraph-agent）

本文件只讲「这个仓库里，去哪里拿真实状态」。产物结构与命令见 `SKILL.md`，字段定义见 `data-contract.md`。

## 1. 定 base / after

```bash
# 已合并 PR
gh pr view 284 --json number,title,body,state,baseRefName,headRefName,headRefOid,mergeCommit,files
git rev-parse 7beca14^1        # before
git rev-parse 7beca14          # after

# 未合并 PR
gh pr view <N> --json headRefOid
git merge-base origin/master <headRefOid>   # before
# after = headRefOid

# 只有 issue：先找关联 PR
gh issue view <N> --json title,body,labels
gh pr list --search "<N> in:body" --state all --json number,title,state
```

没有 after 时不要编造：在 HTML 里写「当前 master 仍复现，尚未修复」，`meta.source` 保持真实采集方式。

worktree：

```bash
git worktree add --detach .qoder/tmp/fix-walkthrough/<slug>/before <before-ref>
git worktree add --detach .qoder/tmp/fix-walkthrough/<slug>/after  <after-ref>
```

两个 worktree 共用主检出的 `.venv314`；`collect.py` 只把它们放到 `sys.path[0]`，不需要在每个 worktree 里再装依赖。

## 2. 纯函数 / 纯逻辑

最轻，也最忠实。直接 import 两个 ref 都存在的函数，构造输入，记录输出。

```python
# scenario.py —— 在 before / after 两个 worktree 都能跑
import copy

from backend.core.agent.context import compress_messages


def build():
    raw = [
        {"role": "user", "content": "u" * 400},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c0", "type": "function", "function": {"name": "read", "arguments": "{}"}}
        ]},
        {"role": "tool", "tool_call_id": "c0", "content": "{}"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "read", "arguments": "{}"}}
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "{}"},
    ]
    out, meta = compress_messages(copy.deepcopy(raw), budget=1, keep_recent=3)
    return {
        "scenarios": [{
            "id": "compress-pair",
            "steps": [{
                "id": "compressed",
                "label": "压缩之后",
                "action": "compress_messages(msgs, budget=1, keep_recent=3)",
                "state": {"kind": "messages", "messages": out},
                "metrics": {"dropped": meta.get("dropped")},
                "checks": [{"label": "配对合法", "ok": not _issues(out), "detail": _detail(out)}],
                "note": "切点是否回退到 assistant 之前。",
            }],
        }]
    }
```

要点：

- **先深拷贝输入**。压缩、逐出、清扫这类函数会原地改消息 dict；不拷贝的话「输入步」会显示成被改过的样子。
- 判据从输出计算，不要写死 `ok: True/False`；两边跑同一个模块，结果才有意义。
- 需要 `Settings` 时显式传临时路径，别依赖仓库 `.env`：

  ```python
  Settings(data_dir=str(tmp), artifacts_dir=..., trace_dir=..., checkpoint_dir=...,
           use_mock_llm=True, trace_enabled=False, checkpoint_enabled=False,
           mcp_enabled=False, subagent_enabled=False, llm_base_url="", llm_api_key="")
  ```

## 3. 节点 / AgentRuntime

节点的私有方法也可以直接调用；用最小 `SimpleNamespace` 冒充 task manager，不需要起服务。参考 `backend/tests/test_dangling_tool_calls.py` 的 `_rt`。

```python
from types import SimpleNamespace

from backend.core.agent.nodes import AgentRuntime
from backend.core.llm.client import MockLLMClient
from backend.services.event_bus import EventBus


def runtime():
    settings = _settings()
    bus = EventBus()
    tm = SimpleNamespace(
        settings=settings,
        event_bus=bus,
        add_artifact=lambda *a, **k: None,
        is_stop_flagged=lambda _tid: False,
        _active_states={"t1": {}},
    )
    return AgentRuntime(
        "t1", tm,
        llm=MockLLMClient(final_answer="收尾答案"),
        tools=[],
        # 非空 schema 让 MockLLMClient 走「最终回答」；空列表会被当成旧规划调用，
        # 返回计划 JSON。
        tool_schemas=[{"type": "function", "function": {
            "name": "noop", "description": "", "parameters": {"type": "object", "properties": {}},
        }}],
    )
```

典型用途：

- 清扫：`runtime.agent(state)`，看 `state["messages"]` 的顺序与 `steps[*].tool_calls[*].status`。
- 收尾：`runtime.finish(state)`，看 `steps[-1].status` 与 `state["status"]`。
- 构造 state 时带上 `round_no`、`steps[*].round`；否则 `finish` 的同轮判断没有意义。

## 4. 端到端任务 / TestClient

不需要 uvicorn；`TestClient` 在进程内跑真实 app、真实图、真实事件总线，只把 LLM 换成 mock。

```python
from fastapi.testclient import TestClient

from backend.main import app
from backend.services.event_bus import EventBus
from backend.services.persistence import Persistence
from backend.tests.conftest import make_manager, make_settings

settings = make_settings(tmp_path, checkpoint_enabled=False)
bus = EventBus()
persistence = Persistence(settings)
manager = make_manager(settings, MockLLMClient(final_answer="done"),
                       event_bus=bus, persistence=persistence)
app.state.settings = settings
app.state.event_bus = bus
app.state.persistence = persistence
app.state.task_manager = manager
client = TestClient(app, base_url="http://localhost")

task_id = manager.create_task(title="demo", user_input="写文件")
# 轮询 manager.get_task(task_id).status 到 INTERRUPTED / FAILED
snapshot = client.get(f"/api/tasks/{task_id}").json()
events = bus.snapshot(task_id)       # 或订阅后再跑
```

要点：

- `backend.tests.conftest` 是测试辅助，不是 pytest fixture；`make_settings` / `make_manager` 可以直接 import。它顶部的环境重定向会把数据根指到检出外，正好符合演示隔离。
- `TaskManager` 是后台线程跑任务；要轮询到 `INTERRUPTED` / `FAILED` 再取快照，不要假设 `create_task` 返回时已经跑完。
- 事件总线是内存态；先 `bus.subscribe(task_id, ...)` 再跑任务，或者用 `bus.snapshot(task_id)`（若当前 ref 提供）。
- 需要「续说 / 悬空 checkpoint」时，用 `manager._checkpointer` + `build_graph(...).update_state(...)`，参考 `test_dangling_tool_calls.py::test_continuation_keeps_protocol_order`。

## 5. Mock LLM

`MockLLMClient` 的规则（`backend/core/llm/client.py`）：

- 剩余 `tool_calls` 每轮发一个；
- 脚本耗尽后：plan 模式返回计划 JSON，build 模式返回 `final_answer`；
- `tools` 为空/None 时走旧规划调用，返回 `plan` JSON——所以要「最终回答」就给一个非空 schema（哪怕不挂真实工具）。

`mock_llm_script_path` 的 JSON：

```json
{
  "plan": ["...", "..."],
  "tool_calls": [
    {"id": "call_1", "name": "bash", "arguments": {"command": "echo hi"}}
  ],
  "final_answer": "..."
}
```

它适合「真实图 + 脚本化工具动作」的端到端场景；提示词不驱动模型，剧本才是驱动。

## 6. UI 视觉 / 交互面

进程内测试看不到真实渲染。只有这种面才需要后台跑一次浏览器：

1. 用 worktree 的 `frontend/dist`（或 `npm run build`）起两个静态页/两个端口；
2. 用 Playwright / browser-use 在同一个剧本下截图；
3. 截图放到 `out/`（相对路径）或转成 data URI，状态用：

   ```json
   {
     "kind": "image",
     "images": [
       {"src": "before.png", "alt": "改动前", "caption": "空列表显示为「没有任务」"},
       {"src": "after.png",  "alt": "改动后", "caption": "加载失败显示原因与重试"}
     ]
   }
   ```

   两份 HTML 与图片放同一目录即可离线打开；用 data URI 则是单文件。
4. `source` 写 `screenshot`，并在 summary 里说明「截图来自真实浏览器，但没有可点击的活应用」。
5. 如果改动只是「UI 产生的数据」（事件、步骤、消息、文件），优先用数据面板，不需要截图。

视口限制：browser-use 的视口可能很窄，不能用来判断布局；需要真实布局时用本机 Chrome + Playwright 固定视口，或直接用 headless Chrome 截图。

## 7. 常见坑

- **两边跑成同一版**：不要在 `collect.py` 之外先 `import backend`；不要从主检出跑场景。`collect.py` 已把 worktree 放 `sys.path[0]` 并清掉预加载的 `backend`。
- **输入被原地改**：`compress_messages`、逐出、清扫会改 dict；输入快照先 `deepcopy`。
- **Mock 返回计划 JSON**：`tool_schemas=[]` 时 `MockLLMClient` 走旧规划分支；需要最终回答就给非空 schema。
- **任务没跑完就取快照**：轮询到 `INTERRUPTED` / `FAILED`。
- **把测试辅助当生产 API**：`make_settings` / `make_manager` 是测试助手；跨 ref 使用时先确认签名还在。
- **把 `.env` 带进 worktree**：worktree 没有 `.env` 是好事；`collect.py` 用环境变量 + 临时数据目录隔离，不要复制 Key 进去。
