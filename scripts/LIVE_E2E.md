# 真实 LLM 端到端验证指南（Live E2E）

> 项目默认的离线测试全部基于 `MockLLMClient`（离线、无 Key、无网络）。
> 本指南是**真实 LLM 验证的唯一权威**——步骤、额度核验、CI key gate 的实际语义都以本文为准
> （规则与缘由见 `AGENTS.md`「跨任务安全边界」）。
> 现役模型口径（`.env`）：DashScope `deepseek-v4.1-flash`（评测线统一大脑，见 `docs/roadmap-pawbench.md`）；
> 全绿历史记录为 `qwen3.7-flash-2026-07-15`（2026-08-24）。已知：场景 2 在 `deepseek-v4.1-flash` 下
> 会因模型中途请求确认而超时（[#68](https://github.com/renjianguojinqianfan/langgraph-agent/issues/68)，既有问题，与主干无关）。

---

## 1. 前置条件

| 项 | 要求 |
|----|------|
| 解释器 | `.venv311/Scripts/python.exe`（Python 3.11.15，已装依赖） |
| API Key | 任一 OpenAI 兼容供应商的 Key（OpenAI / DeepSeek / DashScope(QianWen) / Ollama 本地） |
| 额度 | 所选模型在账户上**必须有可用额度**（免费额度常见耗尽，务必实测） |
| `.env` | `llm_base_url` + `llm_model` 正确指向供应商；`use_mock_llm=false` |

---

## 2. 快速开始（以 QianWen/DashScope 为例）

### 2.1 配置 `.env`（Key 不落明文，用环境变量注入）

```bash
cp .env.example .env
```

编辑 `.env` 的 LLM 段：

```dotenv
# Provider preset: openai | deepseek | ollama | mock
llm_provider=openai
# DashScope OpenAI 兼容端点
llm_base_url=https://dashscope.aliyuncs.com/compatible-mode/v1
# 留空！运行时用环境变量 LLM_API_KEY 注入，避免明文落盘
llm_api_key=
llm_model=qwen3.7-flash-2026-07-15
use_mock_llm=false
```

### 2.2 先做连通性 + 额度探测（不假设免费额度可用）

```bash
# 探测：HTTP 200 且含 choices 即 Key 有效；403 AllocationQuota.* 即额度/权限问题
curl -sS -X POST "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions" \
  -H "Authorization: Bearer $DASHSCOPE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3.7-flash-2026-07-15","messages":[{"role":"user","content":"hi"}],"max_tokens":10}'
```

- `403 AllocationQuota.FreeTierOnly` → 免费额度耗尽，换模型或到控制台充值/关闭“仅免费额度”模式。
- **换模型前先核免费额度**（`qianwen usage free-tier`），别让调用悄悄走按量付费。
- 额度实测记录（唯一权威处，2026-09-16）：`qwen3.6-plus` 已 expire、`qwen3.7-flash`（无日期）已
  exhaust，仅 `qwen3.7-flash-2026-07-15` status=valid（resetDate 2026-10-23）。

### 2.3 运行真实端到端验证

```bash
cd langgraph-agent
LLM_API_KEY="$DASHSCOPE_API_KEY" .venv311/Scripts/python.exe scripts/live_e2e.py
```

脚本含两个场景，任一失败即整批 FAIL：

- **场景 1（冒烟）**：创建任务「写一份自身能力摘要到 `agent_summary.txt` 并汇报」，校验 9 项检查——
  task persisted / status COMPLETED / `plan_update` / `tool_call` / `tool_result` / `artifact_created` /
  `final_answer` / `task_completed` 事件 + `agent_summary.txt` 真实落盘。
- **场景 2（断点续跑）**：第二个任务跑到中途 `stop` → 重建 `TaskManager`（模拟进程重启）→ `resume` →
  COMPLETED。轮询窗口 180s；真实模型偶发单次调用挂起（>窗口）会误伤单跑，**先 A/B 复跑再归因**，
  不要直接当成回归。

**验证过的结果（2026-08-24）**：`=== RESULT: PASS ===`，9/9 通过，~18s 完成，
事件流含 `plan_update → tool_call → tool_result → artifact_created → final_answer → task_completed`
（含 `risk_report`，P1 风险扫描在真实链路同样生效）。场景 2 亦有多次 PASS 记录。

### 2.4 CI 上的 live job（key gate 语义）

`ci.yml` 的 `live-e2e` job 在 push master 时自动跑，或手动 dispatch（勾选 `run_live_e2e`）：

- **secrets 为空（push 触发）**：gate 步骤判 `run=false`、后续步骤全跳过——**job 仍是绿的，但没有任何
  真实模型调用**。看 CI 结论前先看 gate 输出：**绿 ≠ 真实模型跑过**。
- **显式 dispatch 且勾选运行**：secrets 为空则该 job 失败（显式要求就得给结果，不静默跳过）。
- secrets 口径：`LLM_API_KEY` 或 `DASHSCOPE_API_KEY` 任一即可；模型默认 `qwen3.7-flash-2026-07-15`
  （`LLM_MODEL` secret 可覆盖）。

---

## 3. 换其他供应商

本项目 LLM 抽象为 OpenAI 兼容接口，只需改 `.env`：

| 供应商 | `llm_base_url` | 示例 `llm_model` |
|--------|----------------|------------------|
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| DashScope/QianWen | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen3.7-flash-2026-07-15` |
| Ollama（本地） | `http://localhost:11434/v1` | `qwen2.5:7b` |

Key 注入两种方式（`.env` 的 `llm_api_key` 保持留空，不落明文）：
1. 环境变量（唯一推荐）：`LLM_API_KEY="sk-..." python scripts/live_e2e.py`
2. 供应商专属环境变量（如 `DASHSCOPE_API_KEY`）需手动映射到 `LLM_API_KEY` 才能被 pydantic-settings 读到

---

## 4. 安全规范（沿用 qianwen-ops-auth skill 约定）

- **绝不**在对话/日志/文档输出 Key 明文——只报状态（"set"/"not set"、"valid"/"invalid"）。
- **只走**环境变量注入，`.env` 的 `llm_api_key` 保持留空。
- 探测命令用 `$DASHSCOPE_API_KEY` 变量引用，不拼接明文。
- Key 失效排查分层：先探活 → 区分 401（Key 错）/403（额度）/网络错误，再对症处理。

---

## 5. 文件说明

| 文件 | 作用 |
|------|------|
| `scripts/live_e2e.py` | 真实 LLM 端到端验证脚本（独立于 pytest，不参与离线用例） |
| `scripts/live_skill_test.py` | skills 联测两腿：references→KB→真实模型 + skills 运行时（`load_skill`）验收 |
| `backend/tests/` | 离线测试（MockLLM，无需 Key/网络，日常回归用） |
| `.env.example` | 配置样板（LLM/Agent/沙箱/检索/P0/P1 全量配置） |

> 设计取舍：离线测试保证**确定性**与 **CI 可跑**；live 脚本验证**真实世界兼容性**。
> 两者互补：CI 跑离线全量回归，发布前/换供应商时跑一次 live_e2e + live_skill_test。
