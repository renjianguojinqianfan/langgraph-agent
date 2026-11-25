# OpenCode Go 接入指南

> 适用：通过 OpenCode Go 订阅在本应用里调用模型。Go 的套餐、免费模型与地区 / 隐私限制由 OpenCode 决定，本应用只负责按配置发请求。
> 官方资料：[OpenCode Go 文档](https://opencode.ai/docs/go/) · [控制台模型列表](https://opencode.ai/console/go#models)

## 1. 用内置预设添加（推荐）

1. 打开「设置」→「添加模型提供商」。
2. 选「第三方模型供应商」页签，点 **OpenCode Go**。
3. 对话框会切到「自定义模型 API」，并自动填好：

   | 字段 | 值 |
   |---|---|
   | Provider ID | `opencode-go` |
   | 显示名称 | `OpenCode Go` |
   | API 协议 | OpenAI Chat Completions |
   | API 地址 | `https://opencode.ai/inference/go/openai/v1` |
   | 环境变量名 | `OPENCODE_API_KEY` |

   这些字段都可以改。预设**不预置任何模型**，也不写 `data/models.json`；只有点「创建」后，Provider 与选中的模型目录才会一起落库。
4. 填 Key：在「API 密钥」里粘贴，或提前把 `OPENCODE_API_KEY` 放进启动后台的环境变量（见 §3）。
5. 点「获取可用模型」→ 勾选需要的模型（可搜索 / 全选 / 取消全选）→「创建」。
6. 需要时在模型条目上点「测试」，或在 Provider 卡片上设为默认。

> 只有 Provider ID `opencode-go` 还没被占用时才会显示这个内置模板；已经存在时按普通 Provider 编辑，不能重复添加。关闭对话框或切换页签不会写入任何状态。

## 2. 自定义添加

### OpenAI 兼容（可自动发现模型）

| 字段 | 值 |
|---|---|
| API 协议 | OpenAI Chat Completions |
| API 地址 | `https://opencode.ai/inference/go/openai/v1` |
| 环境变量名 | `OPENCODE_API_KEY` |

填好地址与 Key 后，「获取可用模型」会请求该地址下的 `/models`，返回当前端点实际提供的模型。勾选并保存即可。

### Anthropic 原生（手动添加模型）

| 字段 | 值 |
|---|---|
| API 协议 | Anthropic 原生（非流式） |
| API 地址 | `https://opencode.ai/inference/go/anthropic` |
| 环境变量名 | `OPENCODE_API_KEY` |

Anthropic 路径自动用 `x-api-key` 认证，但该协议没有标准目录端点，本应用**不自动发现**模型：在对话框或 Provider 卡片手动填模型 ID 后保存。Anthropic 路径暂不支持 `thinking` / `reasoning_effort`（见 [ADR 0011](adr/0011-model-config-two-protocols.md)）。

## 3. Key 来源与存储

- 两种来源：界面粘贴的 Key 优先；没有粘贴时，读取 Provider 上写明的环境变量（OpenCode Go 默认 `OPENCODE_API_KEY`，只读后台启动时继承的那份）。
- 粘贴的 Key 明文保存在仓库外的 `~/.qianfan/credentials.json`（按 Provider id 取），不是系统钥匙串；同账号进程与本应用的 `shell` 工具都能读到。取舍见 [`docs/security.md`](security.md) §2。
- 接口只回 `key_set` / `key_source`（已设 / 未设，来源为粘贴 / 环境变量 / 无），不回 Key 本身；Key 不进响应、日志、trace、SSE 或错误文本。
- 不要把 Key 写进提交的 `.env`、代码、文档、截图或 issue。

## 4. 模型发现（草稿）

- 「获取可用模型」走只读接口 `POST /api/providers/discover`：用表单当前的协议、地址、环境变量名与 Key 探测，不接收 Provider ID，不写任何状态。
- Key 优先级：表单 Key 优先；留空时读环境变量；两者都没有时请求不带 `Authorization`，由端点决定是否拒绝。
- 只有 OpenAI 兼容协议支持自动发现；选 Anthropic 时会提示手动添加模型 ID。
- 发现请求带客户端 UA，不带 `x-opencode-session`；超时、响应大小上限、拒绝重定向与错误脱敏沿用现有 Provider 发现（[`backend/services/model_store.py`](../backend/services/model_store.py)）。
- 发现失败不阻塞配置：错误会显示可读原因，仍可手动填模型 ID 并保存。
- 本应用不维护固定模型清单，也不缓存发现结果：端点返回什么就列什么。官方目录见 [控制台模型列表](https://opencode.ai/console/go#models)。

## 5. 套餐、免费模型与限制

- OpenCode Go 的额度按官方窗口计算（5 小时 / 周 / 月等），具体以 [Go 文档](https://opencode.ai/docs/go/) 为准。
- 哪些模型免费、是否受地区 / 隐私 / 数据保留限制，由 OpenCode 决定；本应用不判断、不绕过，也不显示用量或剩余额度。
- 模型只有出现在端点返回的列表里才会成为候选；官方新增或下线模型后，重新「获取可用模型」即可。

## 6. 客户端身份与会话头

每个模型请求都带 `User-Agent: qianfan/0.1.0`（OpenAI 兼容、Anthropic 原生、模型发现都带）。当 base URL 的 host 是 `opencode.ai` 或 `www.opencode.ai`，且本次调用有会话 id 时，再带 `x-opencode-session`。

| 场景 | `x-opencode-session` |
|---|---|
| 主任务 | task id |
| 子代理 | 父任务 id |
| 压缩摘要 | 父任务 id |
| 测试模型 | 每次临时生成的新 id |
| 新任务 | 新 id |
| 续说 / 停止后续跑 / 中途换模型 | 同一任务保持同一 id |

模型发现只带 UA，不带会话头。实现见 [`backend/core/llm/identity.py`](../backend/core/llm/identity.py)。

## 7. 代理 / 自定义网关

- 判定只看配置里的 base URL host：不是 `opencode.ai` / `www.opencode.ai` 就不会自动加 `x-opencode-session`；本应用不感知中间代理，也不按代理域名补头。
- 用反向代理或网关的域名作为 base URL 时，本应用不会为这一跳补会话头。网关转发到 Go 时必须保留请求上的 `User-Agent` 与 `x-opencode-session`；若网关自己终止了本应用的请求，则要由网关补一个在同一对话里稳定的会话 id，并避免在改写 host 时丢掉这些头。否则 Go 会返回 HTTP 400 `MissingSessionID`。
- 更稳妥的做法是 base URL 直接填官方地址，让代理只做透明转发（保留请求头）。

## 8. 排障

| 现象 | 可能原因 | 处理 |
|---|---|---|
| HTTP 400 `MissingSessionID` | 代理 / 网关剥掉了会话头；base URL 指向网关或非 OpenCode host | 确认 base URL host 是 `opencode.ai`；经代理时保留会话头；在 Trace 里核对实际请求头（见 §9） |
| HTTP 401 | Key 无效或过期；环境变量没有被后台进程继承 | 重新粘贴 Key，或把 `OPENCODE_API_KEY` 放进启动后台的环境后重启 |
| HTTP 403 | 没有有效的 Go 订阅；模型未授权；地区 / 隐私限制 | 在 [OpenCode 控制台](https://opencode.ai/console/go#models) 核对订阅、模型权限与限制 |
| 发现结果为空 | 地址不是 `.../openai/v1`；协议选错；Key 不可用；端点不返回 OpenAI 风格 `{object, data: [...]}` | 检查地址 / 协议 / Key；仍失败就手动添加模型 ID |
| 模型列表变化 / 旧模型不可用 | 官方新增或下线了模型 | 重新「获取可用模型」，或手动更新模型 ID |

## 9. 在 Trace 里核对请求头

任务详情 → Trace 区 → 模型 → 展开一次调用 → 请求 JSON 的 `headers`：

- `User-Agent: qianfan/0.1.0` 应在。
- 目标是 OpenCode host 时，`x-opencode-session` 应等于该任务的 id。
- 看不到 `Authorization` / `x-api-key` 是预期：认证头在写入 trace 前已被脱敏。

没有 `x-opencode-session` 时，先确认该次调用的 base URL 是 OpenCode host，再检查是否经过代理或网关。

## 10. 真机验收清单

前置：后台启动环境里有真实 `OPENCODE_API_KEY`（不打印、不落盘），且能访问官方地址。结果放 PR 评论或 `docs/assets/verification/<日期>/`，本文不记日期、模型名与数量。

- [ ] **预设**：设置 → 添加模型提供商 → 第三方模型供应商 → OpenCode Go，五个字段按 §1 自动填好并切到自定义表单。
- [ ] **获取模型**：Key 填好（或留空走环境变量）后点「获取可用模型」，候选来自该端点。
- [ ] **保存**：勾选模型后创建，Provider 与模型目录同时出现；关闭对话框 / 取消不产生 Provider、模型或凭据。
- [ ] **测试模型**：模型条目「测试」通过，不返回 `MissingSessionID`。
- [ ] **建任务**：用最小任务跑一轮并成功。
- [ ] **Trace 头**：展开模型调用，`User-Agent` 与 `x-opencode-session` 正确，且没有认证头。
- [ ] **续说同 session**：同一任务续说 / 停止后续跑 / 中途换模型后，`x-opencode-session` 不变。
- [ ] **新任务新 id**：新建任务后，`x-opencode-session` 与旧任务不同。
- [ ] **代理边界（可选）**：经网关时确认转发保留了 UA 与会话头；否则直接用官方地址。

> 没有真实 `OPENCODE_API_KEY` 时，在 PR / 验收记录里明确写「真机验收未运行」，不要用离线测试替代。
