// PROTOTYPE ONLY (branch prototype/frontend-redesign). Throwaway mock data +
// helpers for evaluating the workbench redesign variants. Not for production.
import type { Task, TaskStatus, TraceMarker } from "../types";

const now = Date.now();
const iso = (msAgo: number): string => new Date(now - msAgo).toISOString();

export function timeAgo(isoStr: string): string {
  const ms = now - new Date(isoStr).getTime();
  const min = Math.floor(ms / 60000);
  if (min < 1) return "刚刚";
  if (min < 60) return `${min} 分钟前`;
  const h = Math.floor(min / 60);
  if (h < 24) return `${h} 小时前`;
  return `${Math.floor(h / 24)} 天前`;
}

/** Main demo task: RUNNING at step 3/4, circuit open + confirm + retries. */
export const runningTask: Task = {
  id: "t-1024",
  title: "调研 LangGraph 1.x 生态，输出选型报告",
  user_input:
    "帮我调研 LangGraph 1.x 的主要生态工具和最佳实践，最后写成一份 markdown 选型报告放到工作区。",
  status: "RUNNING",
  plan: [
    { index: 1, description: "检索官方文档与社区实践", status: "done" },
    { index: 2, description: "对比主流工具组合", status: "done" },
    { index: 3, description: "撰写选型报告", status: "active" },
    { index: 4, description: "校验产物并总结", status: "pending" },
  ],
  steps: [
    {
      index: 1,
      thought: "先检索知识库和官方文档，了解 LangGraph 1.x 的变化点。",
      status: "done",
      tool_calls: [
        {
          id: "tc-1",
          tool_name: "kb_query",
          input: { query: "LangGraph 1.x 迁移要点", top_k: 5 },
          output: { hits: 5 },
          status: "success",
          need_confirm: false,
          confirmed: false,
        },
        {
          id: "tc-2",
          tool_name: "web_search",
          input: { query: "LangGraph 1.2 release notes" },
          output: { results: 8 },
          status: "success",
          need_confirm: false,
          confirmed: false,
        },
      ],
    },
    {
      index: 2,
      thought: "整理候选工具清单，读取已有资料做横向对比。",
      status: "done",
      tool_calls: [
        {
          id: "tc-3",
          tool_name: "web_search",
          input: { query: "LangGraph vs CrewAI vs AutoGen 2026" },
          output: { results: 6 },
          status: "success",
          retries: 1,
          need_confirm: false,
          confirmed: false,
        },
        {
          id: "tc-4",
          tool_name: "read_file",
          input: { path: "docs/roadmap-pawbench.md" },
          output: { bytes: 4820 },
          status: "success",
          need_confirm: false,
          confirmed: false,
        },
      ],
    },
    {
      index: 3,
      thought: "联网检索连续失败触发熔断，先把报告骨架写入工作区，稍后重试。",
      status: "running",
      tool_calls: [
        {
          id: "tc-5",
          tool_name: "web_search",
          input: { query: "LangGraph Platform pricing" },
          output: null,
          status: "failed",
          error: "HTTP 429: rate limited",
          retries: 2,
          need_confirm: false,
          confirmed: false,
        },
        {
          id: "tc-6",
          tool_name: "web_search",
          input: { query: "LangGraph Platform pricing (retry)" },
          output: null,
          status: "skipped",
          circuit_open: true,
          need_confirm: false,
          confirmed: false,
        },
        {
          id: "tc-7",
          tool_name: "write_file",
          input: {
            path: "langgraph-selection-report.md",
            content: "# LangGraph 1.x 选型报告\n\n(骨架)…",
          },
          output: { bytes: 2048 },
          status: "success",
          need_confirm: true,
          confirmed: true,
        },
      ],
    },
    { index: 4, thought: "", status: "pending", tool_calls: [] },
  ],
  artifacts: [
    {
      id: "a-1",
      filename: "langgraph-selection-report.md",
      path: "langgraph-selection-report.md",
      mime: "text/markdown",
      size: 2048,
      created_at: iso(2 * 60000),
    },
  ],
  final_answer: "",
  error: null,
  created_at: iso(14 * 60000),
  updated_at: iso(30 * 1000),
  risk_report: [
    {
      step_index: 3,
      level: "medium",
      matched_keywords: ["write_file"],
      suggestion: "写文件将覆盖工作区同名文件，执行前请确认路径无误。",
      action: "confirm",
    },
  ],
  subtasks: [
    {
      subtask_id: "st-1",
      name: "官方资料收集",
      status: "completed",
      summary: "汇总 1.x 迁移指南与 release notes 要点 12 条。",
      artifacts: [],
      error: null,
    },
    {
      subtask_id: "st-2",
      name: "竞品对比",
      status: "running",
      summary: "",
      artifacts: [],
      error: null,
    },
  ],
};

/** Live flow markers for the running task. */
export const runningMarkers: TraceMarker[] = [
  {
    type: "context_compressed",
    ts: (now - 8 * 60000) / 1000,
    data: { step_index: 2, dropped: 6, context_tokens: 5210, strategy: "drop_oldest" },
  },
  {
    type: "tool_circuit_open",
    ts: (now - 3 * 60000) / 1000,
    data: { tool_name: "web_search", cooldown_sec: 60 },
  },
];

export const completedTask: Task = {
  id: "t-1023",
  title: "整理上周变更并写周报",
  user_input: "看下 git log 上周的提交，整理成一份周报。",
  status: "COMPLETED",
  plan: [
    { index: 1, description: "读取上周提交记录", status: "done" },
    { index: 2, description: "归类整理要点", status: "done" },
    { index: 3, description: "生成周报", status: "done" },
  ],
  steps: [
    {
      index: 1,
      thought: "读取 git 日志。",
      status: "done",
      tool_calls: [
        {
          id: "tc-101",
          tool_name: "git_log",
          input: { since: "7 days ago" },
          output: { commits: 14 },
          status: "success",
          need_confirm: false,
          confirmed: false,
        },
      ],
    },
    {
      index: 2,
      thought: "按主题归类。",
      status: "done",
      tool_calls: [],
    },
    {
      index: 3,
      thought: "写出周报文件。",
      status: "done",
      tool_calls: [
        {
          id: "tc-102",
          tool_name: "write_file",
          input: { path: "weekly-2026-W38.md" },
          output: { bytes: 3120 },
          status: "success",
          need_confirm: true,
          confirmed: true,
        },
      ],
    },
  ],
  artifacts: [
    {
      id: "a-2",
      filename: "weekly-2026-W38.md",
      path: "weekly-2026-W38.md",
      mime: "text/markdown",
      size: 3120,
      created_at: iso(26 * 3600000),
    },
  ],
  final_answer: [
    "## 周报（2026 W38）",
    "",
    "上周共 **14** 次提交，按主题归为三类：",
    "",
    "- **运行时深度**：P1-A skills 运行时交付，`load_skill` 工具上线",
    "- **稳定性**：熔断与重试语义补齐，修复 resume 死循环",
    "- **文档**：AGENTS.md 精简 27%，ADR-0002 定稿",
    "",
    "### 下周计划",
    "",
    "1. 启动评测台 M1 最小 runner",
    "2. 前端工作台改版（本原型）",
    "",
    "产物已写入 `weekly-2026-W38.md`。",
  ].join("\n"),
  error: null,
  created_at: iso(27 * 3600000),
  updated_at: iso(26 * 3600000),
  risk_report: [],
  subtasks: [],
};

export const failedTask: Task = {
  id: "t-1022",
  title: "部署到测试环境",
  user_input: "把当前分支部署到测试环境。",
  status: "FAILED",
  plan: [{ index: 1, description: "构建并推送", status: "done" }],
  steps: [
    {
      index: 1,
      thought: "构建通过，准备推送。",
      status: "done",
      tool_calls: [
        {
          id: "tc-201",
          tool_name: "run_command",
          input: { cmd: "npm run build" },
          output: { exit_code: 0 },
          status: "success",
          need_confirm: false,
          confirmed: false,
        },
        {
          id: "tc-202",
          tool_name: "git_push",
          input: { remote: "origin", branch: "master" },
          output: null,
          status: "failed",
          error: "HTTP 403: permission denied",
          retries: 3,
          need_confirm: true,
          confirmed: true,
        },
      ],
    },
  ],
  artifacts: [],
  final_answer: "",
  error: "工具 git_push 连续失败：远端拒绝（403 权限不足）。请检查 GitHub App 的仓库授权。",
  created_at: iso(2 * 24 * 3600000),
  updated_at: iso(2 * 24 * 3600000),
  risk_report: [],
  subtasks: [],
};

export const interruptedTask: Task = {
  id: "t-1021",
  title: "批量重命名素材文件",
  user_input: "把 data/ 下的素材按日期重命名。",
  status: "INTERRUPTED",
  plan: [{ index: 1, description: "扫描并重命名", status: "done" }],
  steps: [
    {
      index: 1,
      thought: "扫到一半被手动停止。",
      status: "done",
      tool_calls: [
        {
          id: "tc-301",
          tool_name: "run_command",
          input: { cmd: "ls data/" },
          output: { exit_code: 0 },
          status: "success",
          need_confirm: false,
          confirmed: false,
        },
      ],
    },
  ],
  artifacts: [],
  final_answer: "",
  error: null,
  created_at: iso(3 * 24 * 3600000),
  updated_at: iso(3 * 24 * 3600000),
  risk_report: [],
  subtasks: [],
};

export const allTasks: Task[] = [runningTask, completedTask, failedTask, interruptedTask];

// --- status meta: single source for the prototype (real app would unify too) ---

export const STATUS_META: Record<TaskStatus, { label: string; dot: string; text: string }> = {
  PENDING: { label: "等待中", dot: "bg-ink-mute", text: "text-ink-dim" },
  RUNNING: { label: "运行中", dot: "bg-signal", text: "text-signal" },
  COMPLETED: { label: "已完成", dot: "bg-ok", text: "text-ok" },
  FAILED: { label: "失败", dot: "bg-danger", text: "text-danger" },
  INTERRUPTED: { label: "已停止", dot: "bg-warn", text: "text-warn" },
};
