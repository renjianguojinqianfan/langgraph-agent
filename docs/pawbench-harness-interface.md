# PawBench harness 接入接口分析（一手来源调研）

> 生成于 2026-09-14。本文是 [`roadmap-pawbench.md`](roadmap-pawbench.md) 里 **M1 第一件事**
> 的交付物：从 PawBench 官方仓库源码取证，回答"一个自定义 harness 要接入 PawBench 跑分，
> 到底要实现什么契约"。能力优先级的推导依据见
> [`capability-first-principles.md`](capability-first-principles.md)。
> 本文所有事实性结论均标注一手来源编号（见 §二），凡未证实处显式标记 **【推测】**。

> ⚠️ **方向修订（2026-09-14，晚于本文生成）**：本项目已决定**不接入 PawBench 的评测机器**
> （不写 `ContainerAgent` 适配类、不 fork/维护 patch、不建评测镜像、不搞 OpenClaw transcript 格式），
> 改为**只借 PawBench 的题目 + 每题自带的 `grade()` 自动检查**，自建一个轻量的跨-agent 对比评测
> （详见 [`roadmap-pawbench.md`](roadmap-pawbench.md) §一 v2 修订与 §四）。因此本文
> **§三（harness 接入契约）与 §四（对 langgraph-agent 的直接影响）大部分作废**，仅作背景资料保留；
> **仍然有用的是**：§3.2 任务文件格式、§3.4 评分机制（automated `grade()` + hybrid 0.75 闸门 +
> LLM judge）、§五 对 roadmap §二 的事实核对、§六.1/.3 的成本与 judge 结论。读本文时以此抬头为准。

---

## 一、一句话结论

**接口形态 = 「在 PawBench 仓库内新增一个 Python 类」**，不是 CLI 约定、不是文件投递协议、
也没有插件/entry-point 机制：继承 `ContainerAgent`，实现 4 个抽象方法，往
`AgentFactory._REGISTRY` 加一条，再放宽 `run_bench.py` 的 `--agents choices` 白名单
（来源：S7 `pawbench/agents/base.py`、S8 `pawbench/agents/factory.py`、S3 `run_bench.py`）。

**适配成本判断：中低，但有一处架构性代价。**
接口面很窄（`setup / run / teardown / version` + 一个返回 dict + 一份 transcript 事件格式），
且官方 Hermes 适配器就是"纯 CLI 单发"范本，可直接照抄骨架（来源：S11 `hermes_agent.py`）。
代价在于**必须修改上游源码 3 处**，因此本项目要维护一个 PawBench fork 或 patch 集；
另外**没有官方接入文档**（`docs/` 目录 404，仓库无 CONTRIBUTING.md，只有 README 里一张
Contributing 表格 —— 来源：S1 README、S20 `docs/` 404 探测），一切契约只能读源码反推。

对 [`roadmap-pawbench.md`](roadmap-pawbench.md) §四 M1 的直接影响：**P0-C headless 入口
必须做成"CLI 单发、无人值守、闸门旁路"形态**，不要做成"起 HTTP 服务 + 调 API"
（理由见 §四.1）。

---

## 二、一手来源清单

全部于 **2026-09-14** 通过 GitHub 仓库页面与 `raw` 端点抓取。仓库真实存在且开源：
`agentscope-ai/PawBench`（109 stars / 19 forks，默认分支 `main`），与 roadmap 记录的地址一致。

| # | 来源 | URL（`https://github.com/agentscope-ai/PawBench` 为根） |
|---|---|---|
| S1 | README（仓库首页） | `/`（blob: `/blob/main/README.md`） |
| S2 | 榜单站点 | <https://agentscope-ai.github.io/PawBench/> |
| S3 | 评测总入口 | `/blob/main/run_bench.py`（540 行） |
| S4 | 包公开 API | `/blob/main/pawbench/__init__.py` |
| S5 | Backend（装载/执行/评分编排） | `/blob/main/pawbench/backend.py`（793 行） |
| S6 | Runner（并发/重试/落盘） | `/blob/main/pawbench/runner.py`（655 行） |
| S7 | **harness 抽象基类** | `/blob/main/pawbench/agents/base.py`（106 行） |
| S8 | **harness 注册表** | `/blob/main/pawbench/agents/factory.py`（91 行） |
| S9 | 共享常量（workspace 路径 / 镜像名） | `/blob/main/pawbench/agents/constants.py` |
| S10 | transcript 解析器 | `/raw/refs/heads/main/pawbench/agents/transcript.py`（818 行） |
| S11 | **Hermes 适配器（CLI 范本）** | `/blob/main/pawbench/agents/impl/hermes_agent.py`（1002 行） |
| S12 | QwenPaw 适配器（HTTP 范本） | `/blob/main/pawbench/agents/impl/qwenpaw_agent.py`（770 行） |
| S13 | 环境抽象基类 | `/blob/main/pawbench/envs/base.py`（39 行） |
| S14 | **Docker 环境实现** | `/blob/main/pawbench/envs/docker.py`（202 行） |
| S15 | Local 环境实现（非 Docker 逃生舱） | `/raw/refs/heads/main/pawbench/envs/local.py`（337 行） |
| S16 | 任务 Markdown 解析器 | `/blob/main/pawbench/task_loader.py`（185 行） |
| S17 | **评分引擎** | `/blob/main/pawbench/grader.py`（681 行） |
| S18 | 异常检测规则 | `/blob/main/pawbench/utils/anomalies.py`（405 行） |
| S19 | 任务文件样本 + 任务目录清单 | `/raw/refs/heads/main/data/pawbench-v1.0/tasks/T053_pinchbench_blog.md`；`/tree/main/data/pawbench-v1.0/tasks` |
| S20 | 负面证据探测 | `/tree/main/docs` → **404**（无官方文档目录） |
| S21 | Hermes 评测镜像 | `/raw/refs/heads/main/docker/Dockerfile.pawbench-hermes`；`/tree/main/docker` |
| S22 | 宿主依赖 | `/raw/refs/heads/main/requirements.txt` |
| S23 | 榜单提交规范 | `/blob/main/site/README.md` |
| S24 | 提交聚合脚本 | `/blob/main/site/scripts/aggregate_results.py`（580 行） |
| S25 | 榜单构建脚本 | `/blob/main/site/scripts/build_leaderboard.py`（366 行） |
| S26 | 真实 submission 样本 + 目录清单 | `/raw/refs/heads/main/submissions/pawbench-4models-opusjudge-20260529__qwen3.6-plus__qwenpaw.json`；`/tree/main/submissions`（27 个文件） |

**未找到 arXiv 论文**：WebSearch 未返回任何 PawBench 论文；README 的 Citation 段给的是
`@misc` 而非 `@article`，作者署名为 "The OpenJudge Team"（来源：S1）。→ PawBench 是工程产物
而非论文基准，**一手权威只有仓库源码本身**，二手报道的数字不可信（见 §五 的两处纠正）。

---

## 三、harness 接入契约（逐条附来源）

### 3.1 要实现什么：`ContainerAgent` 子类

抽象基类 `BaseAgent`（来源：S7）：

```python
class BaseAgent(abc.ABC):
    def __init__(self, name: str, **kwargs): ...
    @abc.abstractmethod
    async def setup(self, environment: BaseEnvironment) -> None: ...
    @abc.abstractmethod
    async def run(self, instruction: str, environment: BaseEnvironment) -> Dict[str, Any]: ...
    @abc.abstractmethod
    async def teardown(self, environment: BaseEnvironment) -> None: ...
    @property
    @abc.abstractmethod
    def version(self) -> Optional[str]: ...
```

`ContainerAgent` 在此之上把 `setup()` 默认接到 `install()`，并追加三个**非抽象但实际必需**的钩子
（来源：S7）：

| 钩子 | 何时被调 | 职责 |
|---|---|---|
| `install(env)` | `setup()` 内 | 镜像里已有二进制则 no-op，否则 pip/源码安装 |
| `post_run_collect(env)` | `run()` 之后、`docker cp` 之前 | 把 harness 自己内部目录同步进标准 workspace；顺便抓 system prompt 存到 `self._last_system_prompt` |
| `extract_transcript(local_workspace, stdout)` | 容器停止后、评分前 | 产出 OpenClaw 事件列表；默认实现委托给 S10 的 `build_transcript_from_session` |
| `get_system_prompt()` | backend 拼 transcript 时 | 返回 `self._last_system_prompt`，会被前置到 transcript |

基类还提供一个静态工具 `_sync_workspace_to_output(env, workspace)`：把 workspace 内
`maxdepth 3` 的非 `*.pyc`、非 `site-packages` 文件复制进 `output/` 子目录，注释明写
"The grader reads `workspace/output/` for files produced by the agent"（来源：S7）。

**注册需要改 3 处上游源码**（这是本次调研最重要的架构结论）：

1. `pawbench/agents/impl/<your>_agent.py` 新增子类；
2. `AgentFactory._REGISTRY` 加一条 —— 其 docstring 原文：
   "Adding a new agent type only requires: 1. Create a new `ContainerAgent` subclass under
   `impl/`. 2. Add one entry to `AgentFactory._REGISTRY` here."（来源：S8）；
3. `run_bench.py` 的 `--agents` 是**硬编码白名单** `choices=["qwenpaw","openclaw","hermes"]`
   （来源：S3）—— factory 的 docstring 漏说了这一处，不改则 CLI 直接报参数非法。

`AgentFactory._populate()` 里三个 import 也是硬编码的，无 importlib 扫描、无 entry_points、
无环境变量注册（来源：S8）。**结论：无插件机制，必须 fork 或维护 patch。**

### 3.2 任务如何下发（输入契约）

**任务文件格式**：`data/pawbench-v1.0/tasks/T<N>_<source>_<slug>.md`，Markdown + YAML
front-matter，由 `TaskLoader.load_task()` 解析（来源：S16、S19）。目录实测 **150 个 .md 文件**，
命名如 `T053_pinchbench_blog.md`、`T016_claweval_T002_email_triage.md`（来源：S19 目录清单）。

T053 的真实 front-matter（来源：S19 raw）：

```yaml
id: task_blog
name: Blog Post Writing
category: Content Creation
subcategory: Writing
grading_type: hybrid
grading_weights: {automated: 0.6, llm_judge: 0.4}
timeout_seconds: 300
input_modality: text-only
external_dependency: none
workspace_files: []
labels:
  scenario: Content_Creation/Writing
  capabilities: [Tool_Use]
  modality: {type: text}
  complexity: L1
  environment: closed
```

正文按 `## ` 分节，解析器识别：`## Prompt`、`## Expected Behavior`、`## Grading Criteria`
（`- [ ]` 复选框列表）、`## Automated Checks`（```python 代码块）、`## LLM Judge Rubric`
（来源：S16 `_parse_sections` / `_extract_grading_criteria`，S19）。

**关键：harness 只拿到一个字符串。** backend 的调用是 `agent.run(task.prompt, env)`
（来源：S5）。`Expected Behavior` / `Grading Criteria` / `Automated Checks` / `LLM Judge Rubric`
**一律不下发给 harness** —— 这是刻意的零提示泄漏设计，harness 无法"面向评分器"作弊。

**workspace 文件注入**：`_stage_workspace_files()` 遍历 `task.workspace_files`，两种形态
（来源：S5）：

- `{"path": "...", "content": "..."}` → `env.write_file(f"{WORKSPACE_ROOT}/{path}", content)`；
- `{"source": "...", "dest": "..."}` → 从 `data/<dataset>/assets/` 解析后 `env.copy_to()`。
  查找顺序为 `assets/<task_id>/<rel>`、`assets/<rel>` 等 4 个候选；**拒绝绝对路径、拒绝 `..`
  穿越、拒绝含 symlink 的源**。

**每题开跑前 workspace 被清空**：`mkdir -p {WORKSPACE_ROOT} && find {WORKSPACE_ROOT}
-mindepth 1 -maxdepth 1 -exec rm -rf {} +`，然后 `mkdir -p {WORKSPACE_ROOT}/output
{WORKSPACE_ROOT}/sessions`；注释说明目的是"remove any pre-existing files from the base image
(for example BOOTSTRAP.md)"（来源：S5）。

> **BOOTSTRAP.md 澄清**：它不是 PawBench 的任务输入，而是 QwenPaw 镜像自带的 onboarding 残留。
> QwenPaw 适配器有 `skip_bootstrap`（**默认 True**）显式 `rm -f {AGENT_WORKSPACE}/BOOTSTRAP.md`，
> 注释理由是"避免 qwenpaw 专属引导指令分散 agent 注意力"（来源：S12）；`run_bench.py` 另有
> `--skip-bootstrap` 开关（来源：S3）。自定义 harness 无需关心它，backend 的清空步骤已经处理。

**凭据注入**：容器 env 只有三个变量 —— `OPENAI_API_KEY`、`OPENAI_BASE_URL`、
`DASHSCOPE_API_KEY`（后两者的值都等于被测模型的 api_key）（来源：S5 `DockerEnvironment(...
environment_vars={...})`）。

**超时是三层同心圆**，全部由任务自己的 `timeout_seconds × --timeout-multiplier` 推导
（来源：S5，含原文注释）：

| 层 | 值 | 位置 |
|---|---|---|
| inner | `task_timeout_s` | 容器内 `timeout Ns <harness cmd>` |
| middle | inner + 60s（Hermes 实现用 +70s） | `docker exec` 墙钟 |
| outer | inner + 600s | backend 的 `asyncio.wait_for` |

注释特别说明：早期版本把 inner/middle 硬编码成 660/720s，会静默截断 `timeout_seconds`
更大的任务，现已改为透传（来源：S5）。→ **adapter 必须从 `self.config["task_timeout_s"]`
读超时，不要自己硬编码。**

**工作目录常量**：`AGENT_WORKSPACE = "/app/working/workspaces/default"`（来源：S9），
backend 里的 `WORKSPACE_ROOT` 同值（来源：S5），LocalEnvironment 的
`CONTAINER_WORKSPACE_BASE` 也同值并注明"Must match the path pre-created in all three
Dockerfiles"（来源：S15）。三处必须一致，这是硬约定。

### 3.3 产物如何被收集（输出契约）

**`run()` 的返回 dict**：backend 只消费两个键 —— `output`（→ `execution_result["stdout"]`）
和 `success`（→ `status` 与 `exit_code = 0 if exit_ok else 1`）（来源：S5）。Hermes 实际返回
`{success, output, error, returncode, session_data, metrics{...}}`（来源：S11），多余键不进
execution_result，仅供 adapter 自用。

**Hermes 的真实执行命令**（CLI 型 harness 的完整范本，来源：S11）：

```bash
cd /app/working/workspaces/default && \
timeout --kill-after=10s <inner_timeout>s hermes chat \
  -q '<instruction>' -Q --yolo [--provider <p>] --model <model> \
  > /tmp/hermes_output.txt 2>&1 || true
```

代码注释里有两条对本项目极有价值的经验（来源：S11）：

- `--kill-after=10s`：SIGTERM 后 10s 补 SIGKILL，"guaranteeing the process (and its inherited
  fds) eventually closes"；
- **用直接文件重定向而不是 `| tee`**，理由是"orphaned hermes child processes holding the pipe
  write-end cannot keep docker exec alive"。
  → 本项目有 SSE 长连接与后台任务（[`backend/api/sse.py`](../backend/api/sse.py)、
  [`backend/services/task_manager.py`](../backend/services/task_manager.py)），**孤儿进程挂住
  `docker exec` 是本项目最可能踩的坑**，必须照抄直接重定向。

**workspace 回收**：`docker cp` 两次（来源：S5）——

1. 整棵树 `{container}:{WORKSPACE_ROOT}/.` → host 临时目录；
2. 再把 `{container}:{WORKSPACE_ROOT}/output/.` **平铺**到同一临时目录根，
   注释说明是"so graders that look at the workspace root can find them directly"。

之后还会 merge 一层可能出现的 `workspace/` 子目录。→ **产物写到 workspace 根或 `output/`
都能被 grader 找到**，T053 的 grader 就找 `workspace / "blog_post.md"`（来源：S19）。

**transcript 契约（最主要的适配工作量）**：目标格式是 **OpenClaw 事件列表**。S10 的模块
docstring 写明："All current agents normalise their session data to the qwenpaw
`agent.memory.content` format during `run()` and write a JSON file under `<workspace>/sessions/`"，
`build_transcript_from_session` 按三级回退取值（来源：S10）：

1. `<local_workspace>/sessions/*.json` 结构化 session（首选）；
2. stdout 逐行扫 `{"events": [...]}` 信封（legacy）；
3. stdout 尾部 40000 字符包成一条 text message（兜底）。

事件结构从 Hermes 的 `_build_openclaw_events()` 反推（来源：S11）：4 个头部事件
（`type: session` / `model_change` / `thinking_level_change` / `custom` 且
`customType: model-snapshot`）+ 若干 `type: message` 事件；message 的 `role` ∈
`user` / `assistant` / `toolResult`；assistant 的 content blocks 类型有 `text` /
`thinking` / `toolCall{name, arguments}`；toolResult 带 `toolCallId` / `toolName` /
`content` / `isError`；每个事件有 `id` / `parentId` 构成链、`timestamp` 为 ISO-Z 毫秒。
`role == "system"` 被刻意跳过（改由 `get_system_prompt()` 单独前置）。

**结果落盘**（`BenchmarkRunner`，来源：S6）：

| 产物 | 路径 | 默认 |
|---|---|---|
| checkpoint JSON | `<results_dir>/<ts>.json`，**每题完成即原子覆写**（`.tmp` + `replace`） | ✅ |
| transcript | `<results_dir>/transcripts/<task_id>[_runN].jsonl` | ✅（transcript 非空时） |
| workspace 快照 | `<results_dir>/workspaces/<task_id>/` | ❌ 需 `--save-workspace` |
| 容器镜像快照 | `<results_dir>/docker_images/<task_id>.tar`（`docker commit` + `docker save` + `docker rmi`） | ❌ 需 `--save-docker-image` |

checkpoint payload schema（来源：S6 `_write_checkpoint`）：
`{benchmark, model, timestamp, summary{total_runs, tasks_completed, passed, pass_rate,
avg_score, runs_per_task, total_time, avg_execution_time, total_usage, errors{...},
by_label{...}, pass@k?}, results[{task_id, task_name, score, max_score, passed,
grading_type, breakdown, notes, execution_time, status, usage, transcript_length,
timed_out, error, anomaly, labels}], task_stats?}`。

`run_bench.py` 默认输出根为 `./results/<YYYYMMDD_HHMMSS>/pawbench/<model>/<agent>/`，
`--no-results-version-path` 可去掉时间戳层（重跑覆写）（来源：S3 docstring 与 `main()`）。

**exit code 的真实作用**：grader 不直接读它。harness 的 returncode 只经 `success` 折算成
`execution_result["exit_code"] = 0 | 1`，然后由异常规则消费（来源：S5、S18）。

### 3.4 评分：automated / llm_judge / hybrid

入口 `grade_task(task=..., execution_result=..., judge_model=..., judge_timeout_seconds=...,
verbose=...)`，按 `task.grading_type` 三分支（来源：S17）。

**automated（规则断言的真实形态）** —— 与 roadmap 记录的"文件落盘 / diff / exit code"有出入，
实际机制是（来源：S17 `_grade_automated`、`_extract_grading_code`）：

1. 用正则 `` ```python\s*\n(.*?)\n\s*``` `` 从 `## Automated Checks` 抽出代码块；
2. 在**宿主机 runner 进程内** `exec(grading_code, namespace)`；
3. 取 `namespace["grade"]`，调用签名固定为 **`grade(transcript: list, workspace_path: str) -> dict`**；
4. **score = 返回 dict 中所有数值的算术平均**（`_average_scores`），`max_score = 1.0`。

缺代码块 → `score=0.0, notes="No automated grading code found"`；缺 `grade` 函数 →
`score=0.0, notes="Automated grading function missing"`（两者都会触发 ERROR 级异常
`GRADING_MISSING_FUNCTION`，来源：S18）。执行期间还会临时 monkey-patch
`sys.modules["subprocess"]` 以打印 grader 内部子进程的 cmd/returncode/stdout/stderr，
并预先打印 workspace 文件清单与 pytest 可用性（来源：S17）——**这是官方留给 harness 开发者的
调试抓手，跑分时应开 `--verbose` 看这些行**。

T053 的 `grade()` 实例（来源：S19）返回
`{file_created, word_count_target, has_structure, covers_remote_work, covers_dev_benefits}`
五个 0–1 分量，全部基于 `Path(workspace_path) / "blog_post.md"` 的存在性、字数分档、
Markdown 标题与段落数、关键词命中数 —— 印证了"产物级硬校验防虚假完工"。

**llm_judge**（来源：S17）：

- **强制依赖 `JUDGE_BASE_URL` + `JUDGE_API_KEY` 环境变量**，缺一即 `RuntimeError`；
- 默认 judge 模型 `DEFAULT_JUDGE_MODEL = "claude-opus-4-5-20251101"`，`run_bench.py` 的回退链是
  `--judge` → `$JUDGE_MODEL` → 该默认值（来源：S17、S3）；
- 端点选择：base_url 含 `api.anthropic.com` → 走原生 `/v1/messages`（`x-api-key` +
  `anthropic-version: 2023-06-01`）；否则走 OpenAI 兼容 `<base_url>/chat/completions`
  （`Authorization: Bearer`）。两者都是 `temperature=0.0`、`max_tokens=20480`；
- **重试 `JUDGE_API_MAX_RETRIES = 100` 次，指数退避基数 5s**，单次 HTTP 超时默认
  `DEFAULT_JUDGE_TIMEOUT_SECONDS = 1800`（CLI 默认传 300s）；
- prompt 由 `_build_judge_prompt` 拼装：固定前缀（"You are a grading function. Your ONLY job
  is to output a single JSON object."、禁止用工具、禁止 JSON 外散文、"Be a strict evaluator.
  Reserve 1.0 for genuinely excellent performance."）+ `## Task`(prompt) +
  `## Expected Behavior` + `## Agent Transcript (summarized)` + `## Grading Rubric`，
  要求返回 `{"scores": {...}, "total": 0.0, "notes": "..."}`；
- rubric 取 `task.llm_judge_rubric`，为空则退化用 `Grading Criteria` 列表；
- 响应解析有多级兜底（裸 JSON → ```json 代码块 → 花括号配平扫描 → 正则抓
  `total|overall|final score`），失败则 `{}` → total 为 None → **score 记 0.0**。

> ⚠️ **transcript 格式错 = judge 瞎判**。`_summarize_transcript` 只认 `type == "message"` 的事件，
> 且只读 `message.content` 里 `type` 为 `toolCall` / `text` 的块，role 只处理
> `assistant` / `toolResult` / `user`（来源：S17）。摘要上限
> `MAX_JUDGE_SUMMARY_CHARS = 120_000`、单条 text `MAX_TEXT_EVENT_CHARS = 20_000`，超限中间截断。
> 如果我们喂进去的 transcript 不符合 OpenClaw 事件结构，judge 看到的就是接近空白的上下文。

**hybrid（roadmap 未记录的关键闸门）**（来源：S17 `_combine_grades`）：

- 权重取 `task.grading_weights`，缺省 `{automated: 0.5, llm_judge: 0.5}`（T053 覆盖为 0.6/0.4）；
- `AUTO_PENALTY_THRESHOLD = 0.75`：**当 automated 分 < 0.75 时，llm_judge 的贡献直接置 0**，
  注释原文 "to prevent the judge from rescuing a completely failed run"；
- 例外：异常检测判定为真实 API 基础设施故障（`TERMINAL_API_FAILURE` / `API_RATE_LIMIT` /
  `API_SERVER_ERROR`）时跳过惩罚；但 **`EMPTY_TRANSCRIPT` 与 `ZERO_TOKEN_RESPONSE` 被刻意排除**，
  注释理由是"agent 完全没输出时 judge 倾向默认给 1.0，会造出约 0.5 的虚假安慰分"；
- 未惩罚的加权值同时存为 `score_simple`，便于事后区分"真差"与"被闸门砍掉"。

→ **对策略的直接影响**：hybrid 题上，automated 不到 0.75 就等于 judge 白跑。提升顺序必须是
**先把产物硬校验做到 0.75 以上，再谈语义质量**。这条应该写进
[`capability-first-principles.md`](capability-first-principles.md) P7（完成验证）的佐证里。

**passed 语义**：`TaskResult.passed = grade.score >= grade.max_score`，即**满分才算 passed**
（来源：S5）。所以 checkpoint 里的 `pass_rate` 是"满分率"，`avg_score` 才是榜单口径的分数
（榜单 `overall` = 每题 score 的均值，来源：S24 `safe_mean(per_task_score)`）。
**汇报时不要把 pass_rate 当通过率。**

**异常检测**（来源：S18）：13 条规则，ERROR 级会让 runner 重试（`--max-retries` 默认 3）。
与新 harness 强相关的 ERROR 规则：`EMPTY_TRANSCRIPT`（transcript_length == 0）、
`SHORT_TRANSCRIPT`（0 < len < **5**）、`ZERO_TOKEN_RESPONSE`、`EXECUTION_EXCEPTION`
（exit_code == -1，"Docker/subprocess failed before agent ran"）、`EXIT_CODE_OOM`（137）、
`EXIT_CODE_NONZERO`、`TASK_TIMED_OUT`、`QUICK_EXIT_SUSPICIOUS`（<10s 且 status=error）、
`GRADING_SCRIPT_ERROR`、`GRADING_MISSING_FUNCTION`。
→ **adapter 必须保证 transcript 事件数 ≥ 5**，否则每题都会白重试 3 次。
另外 `API_RATE_LIMIT` / `API_SERVER_ERROR` 即使 `status=success` 也会重试，退避基数 30s
（来源：S6 `_run_with_retry`）；`_collect_log_text` 会从
`logs/qwenpaw_server.log` / `openclaw_gateway.log` / `logs/main.log` 三个固定路径读日志尾部
512KB 来发现被内部重试掩盖的 429（来源：S5）。
→ **建议本项目 headless 模式把日志写到 `<workspace>/logs/main.log`**，蹭这条既有规则，
让限流能被识别为基础设施故障而不是能力失败（**【建议，非上游要求】**）。

### 3.5 Docker 沙箱边界（roadmap §六 开放问题 2 的确证答案）

**整个 harness 进容器，不只是 workspace 隔离。** `DockerEnvironment.start()` 的实际命令
（来源：S14）：

```
docker rm -f <name>            # 清理同名残留
docker run -d --name <name> [-v host:container]... [-p host:container]... \
       -e KEY=VALUE... <image> sleep infinity
```

容器名 = `pawbench-<agent.name>-<task.task_id>-<uuid4前8位>`，**每题一个全新容器**，
跑完 `docker stop -t 5` + `docker rm -f`（来源：S5、S14）。

逐条边界事实（全部来源：S14，除注明外）：

| 维度 | 实测结论 |
|---|---|
| bind mount | backend 构造时 `volumes` 参数**未传** → 默认 `{}` → **无挂载**。任务文件靠 `docker cp` / `write_file`（host 临时文件 + `docker cp`）注入，产物靠 `docker cp` 取出（来源：S5、S14） |
| 网络 | **无 `--network` 参数** → 默认 bridge，容器可出公网（`open` 环境题型需要） |
| 资源限制 | **无 `--memory` / `--cpus` / `--pids-limit`**；OOM 只能事后由 exit_code=137 推断 |
| 权限 | 无 `--user` / `--read-only` / `--cap-drop` / `--security-opt` → **以 root 运行**；镜像里 Chromium 被 `sed` 成 `--no-sandbox`（来源：S21） |
| shell | `docker exec <name> bash -c "<command>"` → **镜像必须有 bash** |
| 命令超时 | `asyncio.wait_for(proc.communicate(), timeout)`，未传时默认 600s；超时 `process.kill()` |

**镜像构建**：`docker/` 下实测三个文件 —— `Dockerfile.pawbench-qwenpaw`、
`Dockerfile.pawbench-openclaw`、`Dockerfile.pawbench-hermes`（来源：S21 目录清单）。
默认镜像名映射（来源：S9）：`qwenpaw → qwenclawbench-qwenpaw:latest`、
`openclaw → openclaw-pawbench:latest`、`hermes → hermes-qwenclawbench:latest`。
README 给的构建命令是 `docker build -f docker/Dockerfile.pawbench-qwenpaw
-t qwenclawbench-qwenpaw:latest .`（注意 build context 是仓库根，来源：S1）。

`Dockerfile.pawbench-hermes` 的完整内容（自定义镜像的最佳抄写对象，来源：S21）：
`FROM node:22-slim`；apt 装 python3/pip/venv、git/curl/wget/build-essential/libssl-dev/jq/
sqlite3/ripgrep、chromium + 一整套 X11 库、xvfb + xfce4 + xfce4-terminal + dbus + scrot
（注释说明是给 `gui_*_zh` 截图题用的虚拟桌面）、中文字体 `fonts-wqy-zenhei` /
`fonts-wqy-microhei`；装 uv；`pip install hermes-agent==2026.4.23`（失败则 git clone tag +
`uv pip install -e '.[all]'` 并软链到 `/usr/local/bin/hermes`）；预置
`/root/.hermes/config.yaml`（**`approvals: mode: off`** + `workspace:
/app/working/workspaces/default`，注释明写"benchmark mode: no interactive confirmations"）；
`pip install --break-system-packages` 一批任务常用库（requests、pyyaml、pandas、numpy、
matplotlib、Pillow、scikit-learn、python-docx、yfinance、certifi、fastapi、uvicorn、httpx、
openai、oss2、**pytest**）；`mkdir -p` workspace/output/sessions；装 ossutil；
`COPY pawbench/ run_bench.py requirements.txt` 到 `/opt/copawbench` 并设 `PYTHONPATH`
（镜像里也带一份 runner，供容器内模式使用）；末尾一串 smoke test RUN。
另设 `ENV COPAW_RUNNING_IN_CONTAINER=1`、`PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium`、
`PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`。

**逃生舱：`PAWBENCH_ENV=local` 可完全不用 Docker。** backend 里
`_use_local = os.environ.get("PAWBENCH_ENV") == "local"`，为真时改用 `LocalEnvironment`
（来源：S5）。S15 的 docstring 列了两个用途：① 已在 Agent-Platform Pod 容器内，不必嵌套起容器；
② **"Local development with `PAWBENCH_ENV=local`: run without Docker to iterate faster
(requires the agent CLI to be installed locally)"**。实现是
`asyncio.create_subprocess_shell(command, cwd=workspace_root)`，并把
`/app/working/workspaces/default`、`/app/working.secret`、`/app/working` 三个容器路径**字符串替换**
成本地临时目录（`_remap_command` / `_resolve`）；若检测到 `COPAW_RUNNING_IN_CONTAINER` 或
`/.dockerenv` 则不重映射（来源：S15）。

> **【推测】** Windows 宿主直接跑 local 模式大概率失败：adapter 与 backend 下发的命令是
> `mkdir -p` / `find ... -exec rm -rf {} +` / `rm -f` 等 POSIX 语法，而
> `create_subprocess_shell` 在 Windows 上落到 cmd.exe。未实测。本项目开发机是 Windows，
> → **M1 联调建议走 WSL2 或直接 Docker 模式**，不要指望 local 模式。

### 3.6 批量跑 150 题的入口与编排

唯一入口是 `run_bench.py`（来源：S3）。与批量执行相关的 flag：

| flag | 默认 | 语义 |
|---|---|---|
| `--agents` | `["qwenpaw"]` | 多值，**硬编码白名单**，多个 agent 顺序跑 |
| `--model` | `$OPENAI_MODEL` | `action="append"`，可多次传，多模型顺序跑；`--api-key` / `--base-url` 可传 1 次（广播）或 N 次（一一对应，数量不符则报错） |
| `--tasks` | 全部 | 匹配 `task_id` 精确/前缀，**或文件名 stem 精确/前缀**（注释举例：`--tasks T053` 命中 `T053_pinchbench_blog.md`）（来源：S5 `_matches`） |
| `--concurrency` | 1 | `asyncio.Semaphore` 并发；每题跑在 `asyncio.to_thread` 里（因为 `run_and_grade` 是同步的）（来源：S6） |
| `--runs N` | 1 | 每题重复 N 次 → per-task mean/std/min/max + `pass@k` / `pass^k`（来源：S6、S17） |
| `--max-retries` | 3 | 见 §3.4 异常触发条件 |
| `--timeout-multiplier` | 1.0 | 缩放所有任务超时 |
| `--docker-image` | 按 agent 类型 | 镜像覆盖 |
| `--dataset` | `pawbench-v1.0` | `data/` 下的数据集目录名 |
| `--results-dir` | `./results` | 见 §3.3 |
| `--judge` / `--judge-api-key` / `--judge-base-url` / `--judge-timeout` | 见 §3.4 | judge 配置 |
| `--thinking` | 无 | 仅 openclaw：low/medium/high/xhigh |
| `--skip-bootstrap` | False（但 QwenPaw 适配器内部默认 True） | 仅 qwenpaw |
| `--save-workspace` / `--save-docker-image` | False | 见 §3.3 |

run matrix = `models × agents` 笛卡尔积，逐格调用一次 `BenchmarkRunner.run()`
（来源：S3 `main()`）。全量 150 题就是不加 `--tasks`。

> ⚠️ **没有 resume**。`parse_args()` 里不存在 `--resume` / `--skip-completed` 之类的 flag
> （来源：S3 全量参数清单）；checkpoint 只是"把累计结果覆写到一个固定路径"，中断后重跑会
> **重新执行所有任务**（来源：S6 注释 "Generate stable paths before any task runs so
> checkpoints accumulate in the same file even if the process is interrupted mid-run"）。
> → M2 全量跑分必须自己按 `--tasks` 分片，用多个进程/多次调用来实现可续跑。
> 讽刺的是本项目自己的 checkpoint/resume 能力在这里帮不上忙（每题独立容器、无跨题状态）。

### 3.7 榜单提交流程

`site/README.md` 给出**两条路**（来源：S23）：

**路径 A — 从原始跑批自动聚合**：把产物放到
`result/<run>/<model>/<harness>/<task-id>/output/metrics.json`（注意是单数 `result/`，
gitignored），跑 `npm run build:data`；`site/scripts/aggregate_results.py` 读每个 metrics 的
`task_score` / `grading_type` / `breakdown` / `status`，与 `site/src/data/tasks.json`
（由 `build_tasks.py` 从任务 md 生成）做标签 join，每个 `(run, model, harness)` 输出一个
`submissions/<run>__<model>__<harness>.json`。

**路径 B — 手写 submission JSON**，最小 schema（来源：S23、S25 docstring）：

```json
{
  "run": "pawbench-rerun-20260519",
  "model": "gpt-5.4",
  "harness": "openclaw",
  "overall": 0.612, "automated": 0.71, "judge": 0.55,
  "tasks": 150, "tasks_errored": 0,
  "by_source": {...}, "by_capability": {...}, "by_complexity": {...},
  "updated": "2026-05-18"
}
```

**关键发现：聚合器还接受第二种布局** —— `result/<run>/<model>/<harness>/<timestamp>.json`
单文件汇总，其期望 schema 是 `{summary, results:[{task_id, task_name, score, max_score,
passed, grading_type, breakdown, status, timed_out, labels}]}`（来源：S24
`_aggregate_from_single_summary` 的 docstring 与实现）。**这正好是 S6 `_write_checkpoint`
的输出 schema** —— 逐字段比对一致。
→ **`run_bench.py` 的原生产物可以直接喂给聚合器**，只需把它放到
`result/<run>/<model>/langgraph/<ts>.json` 这个路径形状下（默认输出是
`results/<ts>/pawbench/<model>/<agent>/<ts>.json`，多了 `pawbench` 一层且根目录是复数
`results/`，需要 `--results-dir` + `--no-results-version-path` 组合或事后搬移）。
这条路走 hermes 时官方自己就在用（S24 注释举例
`result/.../openai.claude-opus-4-6/hermes/20260525_115007.json`）。

聚合的其他一手事实（来源：S24）：

- **缺失任务补 0**，`total_expected = len(task_meta)` → 分母恒为全量任务数，
  注释原文 "preventing harness crashes from inflating the mean"；
- error/missing 的任务 score 强制记 0 并计入 `tasks_errored`；
- `hybrid` 题的 automated/judge 两列分别从 breakdown 的 `automated.*` / `llm_judge.*`
  前缀键取均值（`split_breakdown`）；纯 automated / 纯 llm_judge 题只贡献一列；
- `HARNESS_ALIAS = {"copaw": "qwenpaw"}`，未知 slug **原样通过**；
- `MODEL_ALIAS` + `SAFE_VENDOR_PREFIXES`（openai/deepseek/anthropic/google/meta）负责目录名 →
  显示名，注释明确说**不会**自动剥 `vendor.` 前缀以免把 `qwen3.6-27b` 的版本点误读；
- 需要 `site/src/data/tasks.json` 存在，否则 `sys.exit(2)`。

`build_leaderboard.py`（来源：S25）：读全部 `submissions/*.json`，按 `(model, harness)` 去重
保留 `updated` 最新者，按 `overall` 降序，输出 `site/src/data/leaderboard.json`。
`HARNESS_META` 只登记了三个已知 harness 的 display/version
（`qwenpaw` → QwenPaw 1.1.3、`openclaw` → OpenClaw 2026.4.24、`hermes` → Hermes 2026.4.23），
但 `collect_harness_meta` 对未知 harness 回退成 `{display: <slug>, version: ""}`
→ **新 harness 无需改任何站点代码即可上榜**，只是列头没有版本号。
`submissions/` 为空时才回退到内置 mock 行（`is_mock: true`）。

**提交动作本身 = 向 `agentscope-ai/PawBench` 提 PR 把 `submissions/*.json` 加进去**：
README 的 Contributing 表格写 "New results | Raw run logs or `submissions/*.json` with
overall and slice scores"，并把 "submitting evaluation results for an untested
model × harness pair" 列为 good first contribution；部署由
`.github/workflows/deploy-site.yml` 在 push 到 main 且触及 `site/**`、`data/**`、
`submissions/**` 时自动触发（来源：S1、S23）。
现有 27 个 submission 文件全部来自单一 run `pawbench-4models-opusjudge-20260529`，
`updated` 均为 `2026-05-29`（来源：S26 目录清单与样本）。

> **【未证实】** 是否有 maintainer review 门槛、是否强制附 raw logs、是否要求可复现证明、
> 第三方 harness 上榜是否需先合并 adapter PR —— 仓库内无 CONTRIBUTING.md（S20 `docs/` 404），
> README 只有上述表格，无流程细则。M3 若要真提交，需先开 issue 问清。

---

## 四、对 langgraph-agent 的直接影响

### 4.1 P0-C headless 入口：契约反推出的 8 条硬要求

roadmap §三 把 P0-C 列为"评测前置依赖"，本次调研可以把它从模糊需求变成可验收清单。
以下每条都对应一处一手证据：

1. **CLI 单发、非交互、跑完即退**。必须能被
   `bash -c "cd /app/working/workspaces/default && timeout --kill-after=10s Ns <cmd> > /tmp/out.txt 2>&1 || true"`
   包住（来源：S11）。
   → **定向结论：不要走 QwenPaw 的 HTTP 路线。** S12 显示 HTTP 型适配器要在容器内起服务、
   轮询 `/api/version` 就绪、写 `call_agent.py` 走 provider 配置 + SSE 流式解析 + 等 session
   文件落盘，共 770 行，还得 monkey-patch 上游 `agentscope` 才能拿到 `_model_trajectory`。
   本项目虽有现成 FastAPI + SSE（[`backend/api/routes.py`](../backend/api/routes.py)、
   [`backend/api/sse.py`](../backend/api/sse.py)），但**评测态用 CLI 单发的成本低一个数量级**。
2. **闸门必须可旁路**。三个官方 harness 无一例外关掉了确认机制：Hermes 用 CLI `--yolo`，
   镜像预置 `approvals: mode: off`（来源：S11、S21），QwenPaw 用
   `QWENPAW_TOOL_GUARD_ENABLED=false` + `PUT /api/config/security/tool-guard {enabled: false}`
   （来源：S12）。
   → 本项目 [`backend/core/agent/`](../backend/core/agent) 的 `_needs_confirm` 重算块属于
   roadmap §五 纪律 1 的**受保护文件**，P0-C 不应改它；正确做法是在 headless 入口层
   加一个显式开关（如 `PAWBENCH_YOLO=1` / `--headless --auto-approve`），
   并在文档里写清"评测态 ≠ 生产态"。这与
   [`capability-first-principles.md`](capability-first-principles.md) 的"确认闸门是护城河"
   主张存在张力，需要显式记录为"跑分时刻意降级"，而不是悄悄改掉。
3. **防孤儿进程挂住 `docker exec`**：输出用直接文件重定向，**不要用 `| tee`**；
   超时用 `timeout --kill-after`（来源：S11 注释）。本项目有 SSE 与后台任务，这是最高危的一条。
4. **cwd 与产物路径固定**：工作目录 `/app/working/workspaces/default`，产物写该目录根或
   `output/` 子目录都能被回收（来源：S5、S7、S9）。
5. **凭据只从环境变量读，且外部 env 优先**：容器只注入 `OPENAI_API_KEY` /
   `OPENAI_BASE_URL` / `DASHSCOPE_API_KEY`（来源：S5）。本项目
   [`backend/config.py`](../backend/config.py) 若用 `load_dotenv()`，必须是
   `override=False` 语义（PawBench 自己的 `run_bench.py` 就是这么做的，并注明
   "Existing shell exports take priority"，来源：S3）。
6. **每题无状态**：容器是一次性的，但**镜像里不能预置脏状态**。Hermes 每题显式
   `_reset_hermes_state()` 删 `state.db` / `sessions/*.jsonl`，backend 也会清空整个 workspace
   （来源：S11、S5）。
   → 本项目 [`data/checkpoints/checkpoints.sqlite`](../data/checkpoints) 与
   [`data/traces/`](../data/traces) 在 headless 模式下必须用**每题唯一的 thread_id**
   （建议直接用 task_id），或跑完清理，否则跨题串味。
7. **必须产出 ≥5 条 OpenClaw 格式 transcript 事件**，否则触发 `SHORT_TRANSCRIPT` /
   `EMPTY_TRANSCRIPT`（均 ERROR 级）→ 每题白重试 3 次，且 hybrid 题拿不到惩罚豁免
   （来源：S18、S17）。
   → **最低成本实现**：在 adapter 里 override `extract_transcript()`，直接把本项目
   [`backend/services/trace.py`](../backend/services/trace.py) 已落盘的
   `data/traces/<thread_id>.jsonl` 映射成 OpenClaw 事件列表，**不要**去容器里伪造
   qwenpaw 风格的 `sessions/*.json`。这是本项目相对三个官方 harness 的天然优势
   （我们本来就有结构化 trace）。映射要点：assistant 的 tool 调用 → `toolCall` block，
   tool 返回 → `role: toolResult` 事件，token usage 填进 `message.usage`。
8. **token usage 键名要能被识别**：`prompt_tokens/completion_tokens`、
   `input_tokens/output_tokens`、`inputTokens/outputTokens` 三种风格任一即可；
   否则回退 tiktoken 离线估算并标 `estimated: true`（来源：S5
   `_extract_usage_from_transcript`）。

**Python/运行时约束**：镜像与 runner 都是 Python 3.11+（来源：S1、S22），本项目 `.venv311`
已对齐；镜像必须有 `bash`（来源：S14）。

### 4.2 适配层要做什么（`scripts/pawbench_run.py` + adapter）

按依赖顺序：

| # | 交付物 | 位置 | 参照 |
|---|---|---|---|
| 1 | PawBench fork / patch 管理策略 | 本仓库外（submodule 或 vendored patch） | 无上游机制，必须自建（S8、S3） |
| 2 | `LangGraphAgent(ContainerAgent)` | fork 内 `pawbench/agents/impl/langgraph_agent.py` | 抄 S11 骨架，可省掉 SQLite 抓取与事件合成（改用 §4.1-7 的 trace 映射） |
| 3 | 注册 | `AgentFactory._REGISTRY["langgraph"]` + `constants.LANGGRAPH_DEFAULT_IMAGE` | S8、S9 |
| 4 | CLI 白名单 | `run_bench.py` 的 `--agents choices` 加 `"langgraph"` | S3 |
| 5 | 评测镜像 | fork 内 `docker/Dockerfile.pawbench-langgraph` | 抄 S21：中文字体 + chromium/xvfb（多模态题）+ pytest + 本项目 requirements |
| 6 | P0-C headless CLI | 本项目内（roadmap 分支 `feat/headless-runner`） | §4.1 八条 |
| 7 | `scripts/pawbench_run.py` | 本项目内 | 对齐 [`scripts/live_e2e.py`](../scripts/live_e2e.py) 的 live 模式 |

`scripts/pawbench_run.py` 的两种模式（对齐 roadmap §一"评测形态"决策）：

- `--check`（离线冒烟，进 CI，不需 Key）：校验 fork 路径存在、`pawbench` 可 import、
  `LangGraphAgent` 可被 `AgentFactory.create({"agent_type": "langgraph", ...})` 实例化、
  镜像已构建（`docker image inspect`）、任务 md 可被 `TaskLoader` 解析、
  `.env` 含 `DASHSCOPE_API_KEY` + `JUDGE_API_KEY` + `JUDGE_BASE_URL`、
  headless CLI 在 `--help` 下可退出 0。
- 真跑（环境变量注入 Key，不进 pytest，过 ruff/mypy）：subprocess 调 fork 里的
  `run_bench.py --agents langgraph --model dashscope/qwen3.6-plus --tasks <切片>`，
  然后把产物从 `results/<ts>/pawbench/<model>/langgraph/` 搬成
  `result/<run>/<model>/langgraph/<ts>.json`，以便直接走 §3.7 路径 A 的聚合器。

### 4.3 现在缺什么（gap 清单）

- ❌ P0-C headless 入口（roadmap 已列为 M1 前置）；
- ❌ 闸门旁路开关（§4.1-2，注意受保护文件纪律）；
- ❌ trace → OpenClaw 事件映射器（§4.1-7，**主要新增工作量**）；
- ❌ PawBench fork/patch 策略（§4.2-1）；
- ❌ 评测镜像：本项目现有 [`backend/Dockerfile`](../backend/Dockerfile) 是 API 服务镜像，
  **不含** chromium/xvfb/中文字体/pytest，也不是"CLI + 评测依赖"形态，需要新写；
- ❌ JUDGE 凭据（见 §六.3）；
- ❌ 宿主 grader 依赖：`requirements.txt` 里 `cvxpy>=1.4.0`、`unified-planning==1.3.0`、
  `up-pyperplan==1.1.0`、`pytest>=7.0.0`、`pandas`、`numpy`、`tiktoken`、`litellm`
  是**评分器在宿主机 exec 时用的**（注释原文 "Grader deps — used by skillbench automated
  checks (pytest TEST_SOURCE)"，来源：S22）。缺依赖 → grader 抛异常 →
  `GRADING_SCRIPT_ERROR` → score 0。这些依赖偏重（cvxpy 带求解器、unified-planning 是 PDDL
  规划器），**是 M1 环境准备里最容易被低估的一块**。

---

## 五、与 [`roadmap-pawbench.md`](roadmap-pawbench.md) §二 的事实核对

一手来源确证为**正确**的：仓库地址与开源性质；150 = Text 124 + Multimodal 26（来源：S2 三个
tab 标签 `Overall 150 / Text 124 / Multimodal 26`）；聚合自 6 个评测集（来源：S1 表：
self-built 21 / claweval 52 / qwenclawbench 29 / pinchbench 23 / skillsbench 15 /
wildclawbench 10 = 150）；五维标签体系与复杂度 L1–L3 定义（L1 = 1-2 步、L2 = 3-5 步、
L3 = >5 步含分支或回溯）、closed/open 环境二分（来源：S1）；官方三个 harness 及其定位
（QwenPaw = 默认基线、OpenClaw = 通用开放运行时、Hermes = 社区替代）（来源：S1）；
"评分 = 规则断言 + LLM-as-judge"的大方向（来源：S17）。

需要**修正或补充**的：

| # | roadmap 原文 | 一手事实 | 判定 |
|---|---|---|---|
| 1 | "qwen3.6-plus + QwenPaw = **76.5**，+ Hermes = **72.6**" | QwenPaw = **75.0**（submission `overall: 0.7501`）、Hermes = **70.4** | ❌ **两个数都错**（来源：S26、S2 榜单第 9/17 行） |
| 2 | "harness 分差最高 **6.4 分**" | 同模型跨 harness 最大极差 **11.5 分**（qwen3.6-35b-a3b：QwenPaw 68.3 / OpenClaw 68.2 / Hermes 56.7）；harness 宏平均 QwenPaw 74.9 / OpenClaw 72.9 / Hermes 69.3（极差 5.6）；站点矩阵列均值 73.7 / 72.1 / 68.4（极差 5.3） | ❌ **6.4 在一手来源里不可复现**，疑为二手报道口径（来源：S1 Core Findings、S2） |
| 3 | "原子能力（tool use、Skill use、planning、logical reasoning、self-verification）" | 实际是 **7 个**：`Logic_Reasoning, Math_Computation, Code_Manipulation, Tool_Use, Skill_Use, Planning, Self_Verification`（站点也自称"7能力维度"） | ⚠️ **漏了 `Math_Computation` 与 `Code_Manipulation`**（来源：S1 标签表、S2、S26 `by_capability`） |
| 4 | "评分 = 规则断言（文件落盘 / diff / exit code）" | automated 的真实形态是**任务 md 内嵌 `grade(transcript, workspace_path) -> dict` 的 Python 函数，在宿主机 `exec()` 后取各 key 算术平均**；exit code 不被 grader 直接读取，只折算成 `success` 后供异常规则消费 | ⚠️ **机制描述不准**，需按 §3.4 改写（来源：S17、S5） |
| 5 | （未记录） | **hybrid 题存在 0.75 惩罚闸门**：automated < 0.75 时 llm_judge 贡献归零 | ➕ **roadmap 完全缺失，且直接影响能力优先级**（来源：S17 `AUTO_PENALTY_THRESHOLD`） |
| 6 | "轨迹 / grader 产物 / 环境快照完整保留可回放" | 轨迹默认保留；**workspace 快照与 Docker 镜像快照需 `--save-workspace` / `--save-docker-image` 显式开启** | ⚠️ 需补"opt-in"限定（来源：S3、S6） |
| 7 | "全部任务在 Docker 沙箱执行" | 默认如此，但存在 **`PAWBENCH_ENV=local` 非 Docker 路径**（官方用于 AP Pod 内与本地快速迭代） | ➕ 补充（来源：S5、S15） |
| 8 | （未记录） | PawBench 属 **OpenJudge 生态**，README 推荐用 OpenJudge 的 50+ graders 建自己的评测体系 | ➕ 补充上下文（来源：S1） |
| 9 | （未记录） | **`passed` = 满分**（`score >= max_score`），checkpoint 的 `pass_rate` 是满分率不是通过率 | ➕ 补充，避免 M2 汇报口径错误（来源：S5） |
| 10 | （未记录） | **无 resume**：中断后重跑全量 | ➕ 补充，影响 M2 执行方式（来源：S3、S6） |

另有两处**上游自身的文档漂移**，读 PawBench 时要当心（一手比对发现）：

- `constants.py` 注释指向 `docker/Dockerfile.hermes` 与
  `examples/upstream/docker/Dockerfile.pawbench-openclaw`，而 `docker/` 目录实际文件是
  `Dockerfile.pawbench-hermes` / `Dockerfile.pawbench-openclaw`；`Dockerfile.pawbench-hermes`
  自己的头部注释也写着 `# docker/Dockerfile.hermes`（来源：S9、S21 目录清单与文件头）。
- `run_bench.py` 的 `--docker-image` 帮助文本说 openclaw 默认
  `ghcr.io/openclaw/openclaw:main`，而 `constants.py` 与 backend docstring 都是
  `openclaw-pawbench:latest`（来源：S3、S9、S5）。
  → **以 `constants.py` 为准，不要信 `--help` 文本。**

roadmap §二 提到的三个上游 harness 仓库链接（`agentscope-ai/QwenPaw`、`openclaw/openclaw`、
`NousResearch/hermes-agent`，来源：S1 Harnesses 表）**本次未逐一访问核实**，
标 **【未证实】**；M1 不需要它们（我们只对接 PawBench 的 adapter 层）。

**Skill_Use 是最难切片**这一点得到一手数据强化：README 给 Skill_Use 均分 47.2、
skillsbench 源均分 40.9（来源：S1）；qwen3.6-plus × qwenpaw 的 submission 里
`by_capability.Skill_Use = 0.3562`、`by_source.skillsbench = 0.2807`，是全表最低的两项
（来源：S26）。→ 印证 roadmap §三 把 skills 运行时列为 P1-A 的判断，且这是**分数弹性最大**
的维度。同表还有 `Finance_Investment/Quantitative_Trading = 0.1968`、
`Data_Analytics/Text_Analytics = 0.0`、`Manufacturing_Engineering/Quality_Control = 0.3995`
等极端低分切片，可作 M2 失败分析的对照。

---

## 六、未决问题与风险（对齐 roadmap §六）

### 6.1 适配成本 —— 已摸清，判定「中低」

roadmap §六 写"PawBench harness 接口适配成本未知——M1 存在的全部理由"。**这个未知现在可以关掉。**

- **窄的一面**：只需实现 4 个抽象方法 + 1 个返回 dict + 1 份 transcript 事件格式；
  **不需要**实现 `BaseEnvironment`（Docker/Local 两个实现上游已给）；不需要碰 grader、
  task_loader、runner；Hermes 适配器是可直接照抄的 CLI 骨架（来源：S7、S11、S13–S15）。
- **贵的一面**：① 必须改上游 3 处 → 长期维护 fork/patch，上游一改可能静默失效
  （**建议 pin commit hash**）；② transcript → OpenClaw 事件映射是纯新增工作量；
  ③ 评测镜像要重建（chromium/xvfb/中文字体/pytest/cvxpy/unified-planning）；
  ④ 无官方接入文档，只能读源码（S20）。
- **量级参考**：Hermes 适配器 1002 行、QwenPaw 770 行（来源：S11、S12 文件头行数）。
  其中 Hermes 有大段是 SQLite schema 动态发现 + OpenClaw 事件合成 —— 本项目有现成结构化
  trace，可省掉前者。**【推测】** 我们的 adapter 约 300–500 行、Dockerfile 约 100 行、
  加上 P0-C headless CLI 本体。未实测，M1 验收后可回填真实数字。

### 6.2 Docker 语义 —— 已确证

roadmap §六 问"整个 harness 进容器，还是仅 workspace 隔离（影响部署形态与 checkpoint 路径）"。
**答案：整个 harness 进容器**，每题一个全新容器，`sleep infinity` 保活 + `docker exec bash -c`
驱动，**无 bind mount**（文件靠 `docker cp` 进出），默认 bridge 网络可出公网，
**无资源限制、root 运行、无 seccomp/capability 收敛**（来源：S14、S5，详见 §3.5）。

对本项目的具体后果：

- **checkpoint 路径**：容器内可写、每题干净。SQLite 文件不能预置在镜像里，thread_id 必须每题唯一
  （§4.1-6）。跨题 resume 语义在评测态**无用武之地**（容器一次性）。
- **部署形态**：本项目现有的 `docker-compose.yml` + API 服务形态与评测态无关；评测态是
  "一个装了本项目 CLI 的胖镜像"。两者不要混。
- **安全认知**：PawBench 的沙箱是**隔离性而非安全性**设计（root + 无 cap-drop + Chromium
  `--no-sandbox` + 任务 md 里的 grader 代码在**宿主机** `exec()`）。
  → **不要在本机跑不受信任的第三方任务集/grader**；跑官方 v1.0 数据集可接受。
  这条与 [`capability-first-principles.md`](capability-first-principles.md) 的沙箱议题相关，
  值得单独记一笔。
- **Windows 开发机**：local 模式大概率不可用（§3.5 **【推测】**），M1 联调走 WSL2 或 Docker Desktop。

### 6.3 评分模型与 Key —— 已确证，但风险升级

roadmap §六 问"LLM-as-judge 需要的评分模型与 Key 额度（qwen3.6-plus 双用途可行性）"。

一手事实：judge 默认 `claude-opus-4-5-20251101`（来源：S17）；v1.0 榜单实际用
**claude opus 4.6** 作 judge（README Core Findings 原文 "claude opus 4.6 as judge"，
submission 的 run 名直接叫 `pawbench-4models-opusjudge-20260529`，来源：S1、S26）；
judge 走 OpenAI 兼容 `/chat/completions` 或 Anthropic 原生 `/v1/messages`，由 base_url 决定
（来源：S17）。

**技术上可行**：`--judge dashscope/qwen3.6-plus` + `JUDGE_BASE_URL` 指向 DashScope 兼容端点
即可让 qwen 当 judge（grader 会走 OpenAI 兼容分支，`api_model = judge_model.split("/",1)[-1]`
正好剥掉 `dashscope/` 前缀）（来源：S17）。

**但这会带来可比性问题，这是本次调研新发现的风险**：榜单现有 27 行**全部**由 opus judge 产出。
换 judge 模型 = 换尺子，得到的分数**不能与官方榜单横向比较**，也不能引用 roadmap 里的
72.6/75.0 之类锚点。

> **建议口径（需主线确认）**：M1 链路验证与 M2 内部迭代用 qwen judge（省钱、够用于
> **相对**比较与失败归因）；M3 若要对外声称分数或提交榜单，**必须换 opus judge 全量重跑**，
> 且要在报告里写明 judge 模型。切勿混用两套 judge 的数字做前后对比 ——
> 这会直接破坏 roadmap §四 M3 的"每个修复有前后切片对比"验收。

额度方面的一手警告：`JUDGE_API_MAX_RETRIES = 100`、退避基数 5s、单次 HTTP 超时默认 1800s
（CLI 默认 300s）（来源：S17）。**judge 限流时单题判分可能挂很久**（100 次指数退避在理论上是
天文数字），必须给跑批设总墙钟预算并监控。另外每题 judge 输入含最多 120,000 字符的
transcript 摘要、输出 `max_tokens=20480`（来源：S17）→ **judge 侧 token 消耗不可忽略**，
150 题 × 多模型的预算要单列。

### 6.4 本次调研新增的风险（roadmap 未记）

1. **无官方接入文档 + 上游文档已漂移**（S20、§五 末）→ 契约靠源码反推，
   `--help` 文本与注释都不可全信。**对策：fork 并 pin commit；把本文当活文档，
   上游有变更时重新核对 §三。**
2. **无 resume**（S3、S6）→ M2 全量 150 题一旦中断要重来。**对策：自己按 `--tasks` 分片跑，
   每片一个 run 目录，事后合并 submission。**
3. **`passed` = 满分**（S5）→ 汇报口径风险，见 §五-9。
4. **宿主 grader 重依赖**（cvxpy / unified-planning / up-pyperplan，S22）→ M1 环境准备
   最易被低估的一块；缺则 `GRADING_SCRIPT_ERROR` 静默吃 0 分。
   **对策：`--check` 冒烟里加一条"宿主可 import 全部 grader 依赖"。**
5. **transcript 事件数 < 5 触发 ERROR 重试**（S18）→ 适配初期最可能反复踩。
   **对策：M1 验收（roadmap §四"Text 切片 10 题跑完出分，0 分也算过"）应额外检查
   `anomaly.items` 里没有 `EMPTY_TRANSCRIPT` / `SHORT_TRANSCRIPT`** ——
   否则"0 分"的原因是链路没通而非能力不足，验收就不成立。
   这是对 roadmap §四 M1 验收标准的一条**具体化建议**。
6. **标签读取口径疑似不一致【未证实】**：backend 从 front-matter **顶层**读
   `scenario/capabilities/complexity/modality/environment`（S5），而 T053 把这些字段嵌在
   `labels:` 下（S19）→ 该题的 `TaskResult.labels` 可能为空、checkpoint 的 `by_label`
   切片可能落空。**榜单切片不受影响**，因为 `aggregate_results.py` 走的是
   `build_tasks.py` 生成的 `tasks.json`（S24）而非 checkpoint labels。
   是否 150 题都用嵌套 `labels:` 形式，本次未逐一核实。
   **对策：M1 跑完 10 题后检查 checkpoint 的 `summary.by_label` 是否为空；
   若空则切片分析改走 §3.7 路径 A 的聚合器。**
7. **多模态 26 题的镜像前置条件**：需要 chromium/xvfb/中文字体，且图像路径要转 base64
   data URL（QwenPaw 为此 monkey-patch 了 `_resolve_content_url`，注释说 DashScope 等端点
   拒绝裸文件路径）（来源：S21、S12）。roadmap 已把多模态降级二期，
   但**镜像层面要一次到位**，否则二期还要重建镜像。

---

## 七、给主线的三条行动建议

1. **把 §4.1 的 8 条当作 P0-C headless 入口的验收清单**，写进 `feat/headless-runner` 分支的
   PR 描述；其中第 2 条（闸门旁路）与第 3 条（防孤儿进程）是本项目的特有风险点。
2. **先 fork + pin commit，再写 adapter**；`scripts/pawbench_run.py --check` 的第一批断言
   应覆盖 §4.3 gap 清单里除 Key 以外的全部项（fork 路径、adapter 可实例化、镜像存在、
   宿主 grader 依赖可 import、任务可解析）。
3. **M1 验收标准补一条**：10 题切片跑完后，除"出分"外还需确认
   ①`anomaly.items` 无 `EMPTY_TRANSCRIPT`/`SHORT_TRANSCRIPT`/`GRADING_SCRIPT_ERROR`，
   ②`transcripts/<task_id>.jsonl` 非空且事件结构符合 §3.3，
   ③hybrid 题的 `breakdown` 同时含 `automated.*` 与 `llm_judge.*` 键
   （这证明两条评分通路都真的走通了，而不是一条静默失败）。
   同时按 §五 修正 roadmap §二 的分数锚点（76.5→75.0、72.6→70.4）与"6.4 分"表述
   （改为"同模型跨 harness 最大极差 11.5 分"）。
