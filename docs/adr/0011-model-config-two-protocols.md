---
status: accepted
---

# 模型配置双协议：Provider 层 + Anthropic 原生适配器

> 2026-11-07 由 spec #287 Phase 2 切片 P2-2（PR B）落地；拍板点已在 spec #287 的 D-35 / D-36 记录。
> 本文只记决策与边界，不复制实施细节；数据结构与 API 的现役事实以 `docs/architecture.md` 的 §3.3 为准。
> 修订 [ADR 0010](0010-launch-dir-workspace-and-model-profiles.md) 决策 7 与「被否的选项」里的
> 「接口格式 = 只 OpenAI 兼容」。

## 背景

ADR 0010 决策 7 把模型配置定成「一套 OpenAI 兼容接口」：预设只有 DeepSeek，其余用自定义 `base_url`。
#243 的日常使用反馈把这条边界打破了：设置页按 Provider 组织、一个 Provider 下挂多个模型，并且要
「API 协议（OpenAI Chat Completions / Anthropic）」这一项。spec #287 的 D-33 / D-34 已把
Provider + 模型目录落进 `data/models.json` v2（PR A1/A2），但当时只有 OpenAI 兼容有适配器，
写面显式拒绝 anthropic。本片补上第二种协议并把写面打开。

## 决策

1. **数据结构（D-33，已由 PR A1/A2 交付）**：`data/models.json` schema v2 = `providers[]` +
   `models[]`，`default` 指向模型条目 id；Key 仍在仓库外
   `~/.qianfan/credentials.json`，按 Provider id 取。本 ADR 不重述字段。
2. **两种协议（D-35）**：Provider 的 `protocol` 取 `openai-compatible` / `anthropic`；两者都是
   **可创建、可编辑、可被任务使用**的一等形态。实现 = `backend/core/llm/protocols.py`（词汇）+
   `backend/core/llm/factory.py`（唯一工厂，Settings 种子路径与任务路径共用）+
   `backend/core/llm/anthropic_native.py`（httpx 非流式 `complete()`）。
3. **Anthropic 适配器只做非流式 `complete()`**：graph 当前只调 `complete()`；`stream()` 抛可读错误。
   OpenAI 风格 messages / system / tools / `role: tool` 与 Anthropic content block
   （text / tool_use / tool_result）双向映射；不支持的输入形态抛可读 `ValueError`，不做静默错映射。
   Key 走 `x-api-key`，带 `anthropic-version` 头；错误文本不插值 Key。
4. **thinking / reasoning_effort 在 Anthropic 路径上暂不支持（本片边界）**：Anthropic extended
   thinking 的工具循环要求把上一轮 assistant 的 thinking block **连同 signature** 原样回传，而本仓
   历史是从 OpenAI 风格 messages 重建的，signature 无处保存。本片选择：D-36 的 `thinking` /
   `reasoning_effort` 字段照常持久化，但**写面（store 校验）在 protocol=anthropic 且有效值为
   开启 / 非空时直接拒绝**（400 可读），运行期构造客户端再守一层；OpenAI 兼容路径按 D-36 走
   `extra_body` 透传已校验值（`reasoning_effort` 限 low / medium / high）。参数可显式 `null` 清空，所以「先清空再切 anthropic」是可走通的。
   开 Anthropic thinking 需先解决签名保留（另开票 / 后续片），届时同步 reasoning 持久化面。
5. **采样参数（D-36）**：`temperature` / `max_tokens` / `thinking` / `reasoning_effort` 挂在模型
   条目，Provider 可作为默认值、entry 覆盖；`temperature` 缺省 0.2（请求构造不再硬编码在调用点）；
   Anthropic 的 `max_tokens` 必填，缺省 4096（Provider / entry 可覆盖）；OpenAI 兼容路径不设缺省
   `max_tokens`。temperature 合法范围按协议：openai-compatible 0–2、anthropic 0–1（写面校验）。
   参数更新支持显式 `null` 清空（省略 = 不改）：清空后回落到 Provider 默认 / 客户端缺省，
   这也是把已配置的 thinking / 参数从卡片上撤掉的唯一路径。
6. **发现接口口径（D-34）**：`/api/providers/{id}/models/discover` 只支持 OpenAI 兼容协议的
   `GET <base_url>/models`；anthropic 返回 400「协议暂不支持发现」，不猜、不落库。

## 与 ADR 0010 的关系

- **修订**决策 7 的「只支持 OpenAI 兼容」：改为「两种协议；OpenAI 兼容为默认与预设路径」。
- **保留**决策 7 其余语义：全局默认 + 任务中途可换 + 下一条消息生效；子代理 / 压缩摘要跟随父任务
  模型；预设只有 DeepSeek；Key 来源与明文决策（决策 8）不变。
- **保留**决策 8 / 9 的 Key 面：Key 仍只在仓库外凭据文件或环境变量；接口只回 `key_set` /
  `key_source`，任何响应 / 日志 / trace / SSE 不回明文。
- ADR 0010「被否的选项」表里「接口格式 = 只 OpenAI 兼容」的「被否 Anthropic」随本 ADR 失效；
  重开条件「Anthropic 原生协议」已达成。

## 被否的选项

| 题 | 选定 | 被否 | 否掉的理由 |
|---|---|---|---|
| 协议数量 | 两种（OpenAI 兼容 + Anthropic） | 只保 OpenAI 兼容 | #243 验收与 DSH 形态都要求协议字段；spec 风险条允许先合 Provider / 目录，适配器后补 |
| Anthropic 实现面 | 非流式 `complete()`（httpx，零新增依赖） | 引入官方 SDK / 直接上流式 | 新增依赖 = D-03 红线；graph 当前只用 `complete()`，流式属后续片 |
| thinking 翻译 | 本片不做、配置时显式报错 | 硬翻译 thinking 预算 / 静默忽略 | 工具循环需要 thinking 签名，半对实现会在多轮工具任务里 400；静默忽略更糟 |
| 协议分派位置 | 单一工厂（`core/llm/factory.py`） | 任务管理器 / 存储层各自 if/else | 两处分支必然漂移；Settings 种子路径与任务路径共用同一工厂 |
| Anthropic base_url 形态 | 同时接受裸 host 与 `.../v1` | 只接受一种 | 用户从不同文档抄来的两种写法都常见，归一化成本低 |
| 发现接口 | 只 OpenAI 兼容 | Anthropic 也猜 `/v1/models` | Anthropic 无标准模型目录端点，猜测会给出误导清单 |

## 三笔账（明说的代价）

1. **协议 × 参数矩阵变宽**：客户端缓存、Key 解析、错误文案都要按协议验证；测试面从单一 OpenAI
   扩成两条路径（MockTransport + fake SDK 两条离线接缝）。
2. **thinking 的债务**：Anthropic 思考可见性要等签名保留方案；在那之前配置该字段会明确报错，
   而不是「配了没效果」。
3. **文档同步面**：发现接口、写面白名单、REST 表、README、`.env.example` 都随协议能力变化同步。

## 重开条件

- 需要 Anthropic thinking / reasoning_effort：先解决 thinking 签名保留（assistant thinking block
  原样回传），另开票并同步 reasoning 持久化面。
- 需要流式：按 D-35 的边界另开票（graph 目前只调 `complete()`）。
- 需要第三种协议：protocol 词汇 + 工厂分支 + 适配器 + 本 ADR 修订。
