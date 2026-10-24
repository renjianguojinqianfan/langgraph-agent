# 安全基线（威胁模型 / 密钥面 / 暴露面 / 危险动作）

> **状态**：现役（2026-10-24 建立）。适用范围：本机单用户运行、源码在公开 GitHub、**不打包分发**。
> 相关决策：[ADR 0008](adr/0008-single-loop-plan-build-modes.md)（单 loop / 模式）、
> [ADR 0009](adr/0009-minimal-agent-chapter.md)（最简 agent；密钥面修订）、
> [ADR 0010](adr/0010-launch-dir-workspace-and-model-profiles.md)（日常形态；明文 Key 决策）。
> 实现事实以代码与 `README.md`「这不是安全边界」为准；本文只写**规则与处置**。

## 1. 威胁模型（先读这段）

- **部署形态**：`qianfan` 单后台；只监听 `127.0.0.1`；Host 只认 `127.0.0.1` / `localhost`；
  CORS 只放本机 origin；**默认无登录**。源码公开在 GitHub，**不发 PyPI / npm / 二进制**。
- **不是权限边界**：同机其他进程、以及你浏览器里的任意网页都能把请求打到本机端口；
  `bash` / `write` 工具以**运行账号权限**执行。具体绕过手段不在本文展开；运行边界与
  操作者处置见 `README.md`「这不是安全边界」与 §4。
- **风险排序**（本机 + 不分发的前提）：① 本机凭证与身份暴露面；② 依赖漏洞里**可达**的那些；
  ③ 公开仓库里的路径 / 用户名 / 一次性工作稿泄露。下游用户与包发布 credential 风险为 0。

## 2. 密钥面（明文、决策、轮换）

- **来源优先级**：界面粘贴（`~/.qianfan/credentials.json`，仓库外，**明文**）→ 配置里写的
  环境变量名（如 `LLM_API_KEY` / `DASHSCOPE_API_KEY`，只读启动时继承的那份）。
  `.env` 的 `llm_api_key` 保持**留空**；不进仓库、日志、trace、SSE；接口只回 `key_set` /
  `key_source`（已设 / 未设 + 来源），不回 Key 本身。
- **明文落盘的决策**：ADR 0010 决策 8——与 dsh / pi / opencode 同类取舍，不做系统钥匙串。
  已知风险：同账号进程可读；本仓 `bash` 工具也能读（ADR 0009 修订如实写）。
  缓解手段是运行隔离（§4）与轮换（下条），不是假装文件读不到。
- **轮换 / 泄露处置**：① 去供应商控制台**吊销并换新 Key**；② 界面重粘（或换环境变量）；
  ③ 检查 `data/logs/` 与 `data/traces/` 是否曾出现明文（正常应被 redact 成 `[redacted]`）；
  ④ 若已进过 git 历史或公开途径，**按已泄露处理**（历史不可撤回），直接轮换。
- **禁止**：Key 写进 `.env`（提交）/ 代码 / 文档 / 截图 / issue；测试只用假 Key（如 `sk-test`）；
  提交前依赖 GitHub secret scanning（公开仓库免费）。

## 3. 仓库暴露面（本机路径 / 用户名 / 主机名）

公开仓库里的 Markdown、截图、验证证据都按**公开信息**处理。硬规则：

- 不得出现 `C:\Users\<用户名>`、家目录 / 个人笔记路径、主机名、内网地址、一次性工作稿内容。
- 文档一律用占位符：`<repo-root>` / `<workspace>` / `<tmp>` / `<knowledge-base>` / `<user>`；
  `.pre-commit-config.yaml` 等配置用**仓库相对路径**，不写本机绝对路径。
- 验证证据（`docs/assets/verification/`）落库前必须脱敏：用户名、用户配置目录、本机绝对路径
  改成占位符（2026-10-24 已对 10 个 JSON 与 `qianfan-ui.png` 做了一轮）。
- 历史验证截图保留当时的临时目录路径（`<tmp>` 形态、无用户名/主机名）；新截图要求任务目录
  不含用户名，或先脱敏再提交。
- `commit` 作者名 / 邮箱是公开且不可改的元数据（除非重写历史）；在意的话把本机 git
  `user.email` 换成 GitHub noreply（只对**新提交**生效）。
- **历史提交不会被清理覆盖**：本节的脱敏只改 HEAD；旧提交里的路径 / 用户名仍在公开
  git 历史中。要彻底移除得重写历史（`git filter-repo` + 强推），属破坏性操作，需单独确认。
- `.env` / `data/` / `frontend/dist/` / `~/.qianfan/` / `.agents/handoffs|temp/` 都是本地态
  （已 gitignore）；不要 `git add -f`。

## 4. 运行边界与操作者 checklist

运行边界（`bash` 的真实权限、默认无登录、trace 非审计日志）以 `README.md`「这不是安全边界」
与 §1 为准，本文不复述。操作者只做三件事：

- **隔离**：不可信任务（陌生网页 / 仓库 / 外部 MCP server）在容器、虚拟机或专用低权账户
  里跑；不要用带敏感凭据的账户跑不可信内容。
- **收口**：服务只留 `127.0.0.1`，端口不做转发 / 内网暴露；必须暴露时先开 `auth_enabled`
  并把占位 token 换掉。
- **兜底**：危险动作规则表是最后一道机械闸，不是完整防护；删条目 = 放行该类动作，加规则按
  `.env.example` 的格式（正则对每个入参文本叶子做不区分大小写搜索，不跨叶子拼接）。
  规则漏掉的场景靠上面的隔离兜底，而不是把本文读成安全承诺。

## 5. 危险动作规则表（现役起步集）

- 机制：`Settings.policy_rules`（JSON 数组）+ `backend/core/agent/policy.py` 的钩子；
  命中即拒，`{block, reason}` 回给模型。**空表 = 不拦**；写坏规则 warning 后作废；
  规则名命不中任何已挂载工具时启动期点名。
- `.env.example` 随附一组保守起步规则（本地 `.env` 已启用同一组）：
  破坏性文件 / OS 命令、重写 git 历史、读凭据（含 `.env` / `~/.qianfan` / ssh）、
  下载即执行、账户 / 服务 / 持久化、反弹 shell、写 `.env` 与 `.git/hooks`。
- 起步集是**可调基线**，不是完整清单：按自己的任务面删减；规则故意写得窄（避免挡正常操作），
  漏掉的场景优先靠运行隔离兜底。
- 已知边界（2026-10-24 评审复核，故意不追的取舍）：
  - `shutdown` / `reboot`、Linux 账户命令（`useradd` / `usermod` / `userdel` / `groupadd` /
    `groupdel`）与 `systemctl` 的 `enable|disable|mask|unmask`（允许 `--user` 一类前置旗标）
    只锚**命令位**（行首或 `;`、`&`、`|`、换行之后）；`timeout 10 shutdown`、`xargs shutdown`、
    绝对路径调用会漏拦，换来 `cat logs/shutdown.log` / `grep -rn reboot docs/` 不误伤。
    `format` / `diskpart` / `mkfs` / `bcdedit` 保持宽匹配：宁可误伤 `grep -rn bcdedit docs/`，
    也不漏磁盘 / 启动项命令。
  - `.env` 家族拦 `.env` 与 `.env.<后缀>`（后缀可多段，如 `.env.production.local`），放行
    `.env.example` / `.env.sample` / `.env.template`；引号包裹 / 括号收尾等没有空白或 shell
    元字符边界的写法可能漏拦。
  - 下载即执行拦 `| bash|sh|zsh`（含 `sudo` 前缀）；`| env bash` / `| python`、
    `sudo -u … bash` 不在表内。
  - `git restore` / `git commit --amend` 这类历史或工作树重写未覆盖。
  - 更一般的漏拦仍是环境变量拼接，以及 argv 被拆成多段（后者的机制见 `.env.example` 的
    pattern 说明）。

## 6. 依赖与已知 CVE（当前缓解）

- **starlette 0.38.6**（`fastapi==0.112.4` 钉住 `<0.39`）命中 2026 年一批 CVE。其中
  **CVE-2026-48818**（Windows `StaticFiles` UNC → SSRF / NTLM 凭证泄露）在本机可达：
  现役用 `backend/api/static_guard.py::SafeStaticFiles` 在 `os.path.realpath` 之前拒掉
  **前导分隔符**（UNC / drive-root-relative / 前导 `//`）、盘符路径与 NUL；普通嵌套路径
  （Windows 上 `get_path` 返回的 `assets\app.js`）必须放行，否则前端 assets 全 404。前端挂载只走这个类。
  **升级到 `fastapi>=0.162` + `starlette>=1.1` 的独立 chore 尚未执行**；升级落地后删除该 guard。
- 其余同批 starlette CVE（Host 投毒 / `HTTPEndpoint` / `request.url.hostname` / form 限制）
  在本仓不可达或已被 `TrustedHostMiddleware` 缓解；mcp 的 StreamableHTTP 服务端 CVE 与
  LangGraph Store CVE 对应的功能面本仓不使用。
- 依赖没有 lock / constraints：安装随 PyPI 浮动。建议开 Dependabot alerts + 定期
  `pip-audit` / `npm audit`（2026-10-24 用 OSV 对装好的 100 个 Python 包与 78 个 npm 包做过全量扫描）。
  前端 React 18 / Vite 8 当前无 advisory；RSC 类漏洞不适用于 Vite SPA。

## 7. 本机运行边界（明确不保证的东西）

- `127.0.0.1` + `TrustedHostMiddleware`（挡 DNS rebinding）+ CORS 白名单：**减少暴露面**，
  不挡同机进程或浏览器发出的简单请求。
- 默认无鉴权：`auth_enabled=false` 时鉴权依赖本身关闭，token 仍是占位符；这不是权限边界。
- 停止 / 超时只回收本仓建立的进程树；`setsid` 等自行脱管的进程不受管；trace 不是防篡改审计日志。

## 8. 变更记录

- 2026-10-24：建立本文件；`qianfan-ui.png` 脱敏（用户配置目录 → `<workspace>`）；10 个验证
  JSON 里的用户名 / 用户目录 / 本机绝对路径换成占位符；文档与 pre-commit 的本机绝对路径清理；
  `.env.example` 增加危险动作起步集；新增 `backend/api/static_guard.py`（CVE-2026-48818 缓解）。
- 2026-10-24（复审）：§4 精简为操作者 checklist，删除与 README 重复的越界描述；运行边界
  统一指向 `README.md`「这不是安全边界」，本文不展开任何绕过手段。
- 2026-10-24（评审返工）：`SafeStaticFiles` 改为拒**前导分隔符 / 盘符 / NUL**（原「任何反斜杠」
  在 Windows 上会把 `assets\app.js` 这类正常嵌套路径全部 404，白屏级回归）；补 mount 级回归测试。
  规则起步集补 `rm --recursive`、`.env` 多段后缀（`.env.local` / `.env.production.local`）、
  `| sudo bash`、Linux 账户 / `systemctl` 持久化，`shutdown` / `reboot`、账户 / 服务命令改为
  命令位锚定消误伤；§5 记录已知边界。
